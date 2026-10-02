# Light Layer Radar

Produces 320×360 baseline JPEG maps and the `GOODFELLA_RADAR_V1` feed for an ESP32 display. The browser viewer has a looping player, time slider, latest-radar button, and data-age indicator.

## Data

- Observed radar: RainViewer, approximately the past two hours in 10-minute steps.
- Forecast: NOAA HRRR composite reflectivity, hourly valid times through at least 24 hours ahead, from the newest complete extended model cycle (00/06/12/18 UTC).
- Viewport: public city center of Middletown, Connecticut. No home address or device credentials are published.
- Maps: OpenStreetMap contributors.

Future images are model forecasts, not observed radar or an extrapolated copy of the latest storm. Sources may be delayed. The workflow fails instead of publishing a fabricated or incomplete feed, keeping the last successful Pages deployment available.

## Hosting

In repository **Settings → Pages → Build and deployment → Source**, select **GitHub Actions**. Then run **Actions → Refresh radar → Run workflow** if the initial push run failed while Pages was disabled.

Expected site: https://seanmsalas203.github.io/Lightlayer-radar/

Device base URL: `https://seanmsalas203.github.io/Lightlayer-radar`

The device polls every five minutes. Actions requests refreshes every five minutes, but GitHub can delay or skip scheduled jobs. Scheduled workflows may be disabled after 60 days without repository activity. HRRR extended model data refreshes every six hours; polling does not produce a new model run. Public-repository Actions uses standard free runners; no paid weather key is required.

## Local run

Python 3.12 recommended. `pip install -r requirements.txt`, then `python -m unittest discover -s tests`, then `python scripts/render.py --output public`.

The renderer fetches only the reflectivity GRIB message via HTTP byte ranges. Cached forecast rasters are reused between refreshes. The feed includes actual timestamps and generation-specific frame paths, so a device cannot confuse different generations of images.

The existing v0.19 sketch uses `manifest.txt`; set its `RADAR_BASE_URL` to the device base URL. Keep it in the existing sketch folder with `StationTypes.h` and `StationNetwork.h`. Do not publish the complete device sketch if it contains credentials or personal assets.
