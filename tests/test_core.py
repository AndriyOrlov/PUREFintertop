import unittest
from pathlib import Path
import io
from PIL import Image
from pur_adapter import Board
from pur_adapter import core, qt

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class CommonCore(unittest.TestCase):
    def test_geometry_is_independent_and_invertible(self):
        local = core.affine(40, -20, 2, 31)
        parent = core.affine(120, 90, 0.5, -15)
        world = core.multiply(local, parent)
        recovered = core.multiply(world, core.inverse(parent))
        for actual, expected in zip(recovered, local):
            self.assertAlmostEqual(actual, expected)
        self.assertIs(qt.affine, core.affine)

    def test_four_releases_decode_common_image_pixels(self):
        pixels = []
        for release in ("1.10.4", "1.11.1", "2.0.3", "2.1.3"):
            with Board.load(FIXTURES / f"native-{release}.pur") as board:
                self.assertEqual(board.application_version, release)
                self.assertEqual(board.to_dict()["compatibility"]["status"], "tested_release")
                row = board._conn.execute("SELECT data FROM images LIMIT 1").fetchone()
                with Image.open(io.BytesIO(row[0])) as image:
                    pixels.append((image.size, image.convert("RGBA").tobytes()))
        self.assertTrue(all(value == pixels[0] for value in pixels))
