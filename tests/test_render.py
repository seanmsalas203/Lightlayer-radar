import datetime as dt
import importlib.util
from pathlib import Path
import unittest
import numpy as np

spec = importlib.util.spec_from_file_location('renderer', Path(__file__).parents[1]/'scripts/render.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

class RenderTests(unittest.TestCase):
    def test_byte_range_selects_only_composite_reflectivity(self):
        index='1:0:d=2026100218:TMP:2 m above ground:1 hour fcst:\n2:100:d=2026100218:REFC:entire atmosphere:1 hour fcst:\n3:240:d=2026100218:VIS:surface:1 hour fcst:\n'
        self.assertEqual(r.select_range(index),(100,239))
        with self.assertRaises(ValueError):r.select_range('1:0:TMP:surface:')
    def test_mercator_alignment(self):
        lat,lon=r.target_coords();x,y=r.pixel(lat,lon);left,top=r.viewport()
        self.assertAlmostEqual(x[0,0],left+.5,places=6)
        self.assertAlmostEqual(y[-1,-1],top+r.H-.5,places=6)
        self.assertGreater(lat[0,0],lat[-1,0])
    def test_missing_values_and_clear_air_are_transparent(self):
        grid=np.zeros((r.H,r.W));grid[0,:5]=[np.nan,-10,9999,25,55]
        arr=np.array(r.colorize(grid));self.assertEqual(arr[0,:3,3].tolist(),[0,0,0])
        self.assertEqual(arr[0,3,3],200);self.assertNotEqual(arr[0,3,:3].tolist(),arr[0,4,:3].tolist())

if __name__=='__main__':unittest.main()
