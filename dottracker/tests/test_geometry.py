import unittest
from app.geometry import inside


class GeofenceGeometryTests(unittest.TestCase):
    def test_circle_center_and_edge(self):
        f={'shape':'circle','geometry':{'lat':39.7392,'lng':-104.9903,'radius_m':400}}
        self.assertTrue(inside(f,39.7392,-104.9903))
        self.assertFalse(inside(f,39.75,-104.9903))

    def test_circle_longitude_wraparound(self):
        f={'shape':'circle','geometry':{'lat':0,'lng':179.999,'radius_m':500}}
        self.assertTrue(inside(f,0,-179.999))

    def test_polygon_inside_outside_and_boundary(self):
        f={'shape':'polygon','geometry':{'coordinates':[[-105,39],[-104,39],[-104,40],[-105,40]]}}
        self.assertTrue(inside(f,39.5,-104.5))
        self.assertFalse(inside(f,40.5,-104.5))
        self.assertTrue(inside(f,39,-104.5))

    def test_polygon_closed_ring(self):
        f={'shape':'polygon','geometry':{'coordinates':[[-105,39],[-104,39],[-104,40],[-105,40],[-105,39]]}}
        self.assertTrue(inside(f,39.5,-104.5))


if __name__ == '__main__':
    unittest.main()
