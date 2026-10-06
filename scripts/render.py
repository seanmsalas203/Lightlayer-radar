"""Produce the ESP32 V1 manifest and geographically aligned 320x360 JPEGs."""
import argparse
import concurrent.futures
import datetime as dt
import hashlib
import io
import json
import math
from pathlib import Path
import shutil
import time
import urllib.request
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image, ImageDraw

UTC = dt.timezone.utc
W, H, Z, SCALE = 320, 360, 6, 2
# Public city center, never a street or home location.
LAT, LON = 41.5623, -72.9000
S3 = 'https://noaa-hrrr-bdp-pds.s3.amazonaws.com'
CACHE = Path('.cache/radar')


def fetch(url, headers=None):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'LightLayerRadar/1.0', **(headers or {})})
            with urllib.request.urlopen(req, timeout=45) as response:
                data = response.read()
                if headers and 'Range' in headers and response.status != 206:
                    raise ValueError('Server did not honor byte range')
                return data
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1 + attempt)


def pixel(lat, lon):
    n = 256 * 2**Z * SCALE
    return (lon + 180) / 360 * n, (1 - np.arcsinh(np.tan(np.radians(lat))) / np.pi) / 2 * n


def viewport():
    cx, cy = pixel(LAT, LON)
    return round(cx - W / 2), round(cy - H / 2)


def target_coords():
    left, top = viewport()
    xx, yy = np.meshgrid(np.arange(W) + left + .5, np.arange(H) + top + .5)
    n = 256 * 2**Z * SCALE
    return np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * yy / n)))), xx / n * 360 - 180


def tiles(url_template, cache_prefix=None):
    left, top = viewport()
    image = Image.new('RGBA', (W, H))
    tile_size = 256 * SCALE
    def tile_at(pos):
            x,y=pos
            path = CACHE / f'{cache_prefix}-{Z}-{x}-{y}.png' if cache_prefix else None
            if path and path.exists():
                data = path.read_bytes()
            else:
                data = fetch(url_template.format(z=Z, x=x, y=y))
                if path:
                    path.write_bytes(data)
            tile = Image.open(io.BytesIO(data)).convert('RGBA')
            if tile.size == (256, 256):
                tile = tile.resize((tile_size, tile_size), Image.Resampling.BILINEAR)
            if tile.size != (tile_size, tile_size):
                raise ValueError(f'Unexpected tile size: {tile.size}')
            return x,y,tile
    positions=[(x,y) for y in range(top // tile_size, (top + H - 1) // tile_size + 1)
               for x in range(left // tile_size, (left + W - 1) // tile_size + 1)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for x,y,tile in pool.map(tile_at,positions):
            image.alpha_composite(tile, (x * tile_size - left, y * tile_size - top))
    return image


def base_map():
    return tiles('https://tile.openstreetmap.org/{z}/{x}/{y}.png', 'map-v2')


def decorate(image, valid_time, forecast, run=None):
    image = image.convert('RGB')
    d = ImageDraw.Draw(image)
    left, top = viewport()
    for name, lat, lon in [('Middletown', LAT, LON), ('Hartford', 41.7658, -72.6734),
                            ('New Haven', 41.3083, -72.9279), ('New London', 41.3557, -72.0995),
                            ('Waterbury', 41.5582, -73.0515), ('Danbury', 41.3948, -73.4540),\n                            ('Bridgeport', 41.1792, -73.1894), ('Springfield', 42.1015, -72.5898)]:
        x, y = pixel(lat, lon)
        x, y = round(x - left), round(y - top)
        if 10 < x < W - 10 and 40 < y < H - 40:
            d.ellipse((x-2, y-2, x+2, y+2), fill='white')
            d.text((min(x + 4, W - len(name)*6 - 2), y-6), name, fill='white', stroke_width=1, stroke_fill='black')
    local = valid_time.astimezone(ZoneInfo('America/New_York'))
    d.rectangle((0, 0, W, 30), fill='#10151e')
    d.text((8, 5), ('HRRR FORECAST' if forecast else 'OBSERVED RADAR'), fill='#fbc86b' if forecast else '#68dda4')
    d.text((8, 18), local.strftime('%a %I:%M %p %Z'), fill='white')
    d.rectangle((0, H-29, W, H), fill='#10151e')
    d.text((5, H-25), 'NOAA HRRR model' if forecast else 'Radar: RainViewer', fill='#cccccc')
    d.text((5, H-13), 'Map: OpenStreetMap contributors', fill='#cccccc')
    if run:
        d.text((165, H-25), 'Run '+run.strftime('%d %HZ'), fill='#cccccc')
    return image


def grib_url(run, lead):
    return f'{S3}/hrrr.{run:%Y%m%d}/conus/hrrr.t{run:%H}z.wrfsfcf{lead:02d}.grib2'


def select_range(index):
    lines = index.strip().splitlines()
    for i, line in enumerate(lines):
        if ':REFC:entire atmosphere:' in line:
            start = int(line.split(':')[1])
            end = int(lines[i+1].split(':')[1]) - 1 if i+1 < len(lines) else None
            return start, end
    raise ValueError('HRRR index lacks composite reflectivity')


def latest_run(now):
    # Extended runs supply all 24 future hours, even after their initialization time.
    cycle = now.replace(minute=0, second=0, microsecond=0)
    cycle -= dt.timedelta(hours=cycle.hour % 6)
    for back in range(4):
        run = cycle - dt.timedelta(hours=6 * back)
        final_lead = math.ceil((now - run).total_seconds()/3600) + 24
        if final_lead > 48:
            continue
        try:
            select_range(fetch(grib_url(run, final_lead) + '.idx').decode())
            return run
        except Exception as e:
            print(f'Cycle {run.isoformat()} not complete: {e}', flush=True)
    raise RuntimeError('No complete HRRR extended forecast cycle found')


def forecast_grid(run, lead):
    path = CACHE / f'hrrr-v1-{run:%Y%m%d%H}-{lead:02d}.npy'
    if path.exists():
        return np.load(path)
    import eccodes as ec
    from scipy.spatial import cKDTree
    url = grib_url(run, lead)
    start, end = select_range(fetch(url + '.idx').decode())
    blob = fetch(url, {'Range': f'bytes={start}-{end if end is not None else ""}'})
    msg = ec.codes_new_from_message(blob)
    try:
        values = ec.codes_get_values(msg)
        lats = ec.codes_get_array(msg, 'latitudes')
        lons = ec.codes_get_array(msg, 'longitudes')
        lons = (lons + 180) % 360 - 180
        target_lat, target_lon = target_coords()
        mask = ((lats >= target_lat.min()-.2) & (lats <= target_lat.max()+.2)
                & (lons >= target_lon.min()-.2) & (lons <= target_lon.max()+.2))
        if not mask.any():
            raise ValueError('Forecast grid does not cover viewport')
        factor = math.cos(math.radians(LAT))
        tree = cKDTree(np.column_stack((lats[mask], lons[mask] * factor)))
        _, nearest = tree.query(np.column_stack((target_lat.ravel(), target_lon.ravel() * factor)))
        grid = values[mask][nearest].reshape(H, W).astype(np.float32)
    finally:
        ec.codes_release(msg)
    np.save(path, grid)
    return grid


def colorize(grid):
    # Conventional reflectivity colors; HRRR intensity is modelled, not measured.
    rgba = np.zeros((H, W, 4), dtype=np.uint8)
    for threshold, color in [(5,(60,125,225)), (10,(20,195,90)), (20,(15,150,45)),
                              (30,(240,220,35)), (40,(255,155,15)), (50,(235,45,35)),
                              (60,(205,45,180)), (70,(250,220,255))]:
        rgba[np.isfinite(grid) & (grid >= threshold) & (grid < 200)] = (*color, 200)
    return Image.fromarray(rgba)


def build(out, now=None):
    now = now or dt.datetime.now(UTC)
    CACHE.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    base = base_map()
    print('Basemap ready; fetching observed timeline', flush=True)
    weather = json.loads(fetch('https://api.rainviewer.com/public/weather-maps.json'))
    past = weather['radar']['past']
    if not past:
        raise RuntimeError('No observed radar returned')
    latest = dt.datetime.fromtimestamp(past[-1]['time'], UTC)
    if (now - latest).total_seconds() > 3600:
        raise RuntimeError('Observed radar is over an hour old; retaining previous deployment')
    run = latest_run(now)
    start_lead = math.floor((now-run).total_seconds()/3600)+1
    end_lead = math.ceil((now-run).total_seconds()/3600)+24
    generation = hashlib.sha256(json.dumps([past,run.isoformat(),start_lead,end_lead,Z,LAT,LON,'ct-west-v1']).encode()).hexdigest()[:20]
    folder = out / 'frames' / generation
    folder.mkdir(parents=True, exist_ok=True)
    frames = []

    def save(image, valid, forecast):
        path = f'frames/{generation}/f{len(frames):03d}.jpg'
        offset = round((valid-now).total_seconds()/60)
        label = ('FORECAST ' if forecast else 'ACTUAL ') + valid.astimezone(ZoneInfo('America/New_York')).strftime('%I:%M %p')
        image.save(out/path, 'JPEG', quality=85, progressive=False, optimize=True)
        frames.append(dict(offset=offset, path=path, forecast=forecast, label=label, time=valid.isoformat()))

    for frame in past:
        valid = dt.datetime.fromtimestamp(frame['time'], UTC)
        if valid > now or valid < now-dt.timedelta(hours=2, minutes=10):
            continue
        overlay = tiles(weather['host']+frame['path']+'/512/{z}/{x}/{y}/2/1_0.png', 'rv-'+str(frame['time']))
        save(decorate(Image.alpha_composite(base, overlay), valid, False), valid, False)
        print('Observed', valid.isoformat(), flush=True)
    now_index = len(frames)-1
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        leads = range(start_lead, end_lead+1)
        for lead,grid in zip(leads,pool.map(lambda lead: forecast_grid(run,lead),leads)):
            valid = run+dt.timedelta(hours=lead)
            save(decorate(Image.alpha_composite(base, colorize(grid)), valid, True, run), valid, True)
            print('Forecast', valid.isoformat(), flush=True)
    if not 0 <= now_index < len(frames) < 100:
        raise RuntimeError('Manifest frame count invalid')
    # Retain recently published generations for devices still holding the old manifest.
    archive = CACHE/'published'
    archive.mkdir(exist_ok=True)
    shutil.copytree(folder, archive/generation, dirs_exist_ok=True)
    (archive/generation).touch()
    for old in archive.iterdir():
        if old.stat().st_mtime < time.time()-3600:
            shutil.rmtree(old)
        elif old.name != generation:
            shutil.copytree(old, out/'frames'/old.name, dirs_exist_ok=True)
    manifest = ['GOODFELLA_RADAR_V1', 'version='+generation, 'now='+str(now_index)]
    manifest += [f'frame={f["offset"]}|{f["path"]}|{"F" if f["forecast"] else "A"}|{f["label"]}' for f in frames]
    (out/'manifest.txt').write_text('\n'.join(manifest)+'\n')
    (out/'timeline.json').write_text(json.dumps(dict(version=generation, generated=now.isoformat(),
        observed=latest.isoformat(), model_run=run.isoformat(), now_index=now_index, frames=frames), indent=2))
    shutil.copyfile(Path(__file__).resolve().parents[1]/'web/index.html', out/'index.html')
    # Keep caches bounded, including when a runner restores older generations.
    cutoff = time.time()-2*86400
    for path in list(CACHE.glob('hrrr-*.npy'))+list(CACHE.glob('rv-*.png')):
        if path.stat().st_mtime < cutoff:
            path.unlink()
    print(f'Published {len(frames)} frames to {out}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('public'))
    args = parser.parse_args()
    build(args.output)
