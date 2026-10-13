import tempfile
import unittest
from pathlib import Path
from pur_adapter import Board
from pur_adapter.drawings import parse_strokes, rasterize_drawing
from pur_adapter import qt

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/native-drawing-2.1.3.pur"


class DrawingConversion(unittest.TestCase):
    def test_reads_native_pen_color_width_and_curves(self):
        board = Board.load(FIXTURE)
        raw = board._conn.execute(
            "SELECT strokes FROM items_drawings WHERE id=3"
        ).fetchone()[0]
        strokes = parse_strokes(raw)
        self.assertEqual(len(strokes), 1)
        self.assertEqual(strokes[0].color, (46, 132, 170, 200))
        self.assertEqual(strokes[0].width, 5)
        self.assertEqual(
            [point[0] for point in strokes[0].elements], [0, 2, 3, 3, 2, 3, 3]
        )

    def test_replaces_drawing_with_png_at_same_parent_coordinates(self):
        board = Board.load(FIXTURE)
        old = board.get(3)
        with tempfile.TemporaryDirectory() as folder:
            report = rasterize_drawing(board, 3, scale=4)
            new = board.get(3)
            self.assertEqual(new.kind, "image")
            self.assertEqual(new.parent_id, old.parent_id)
            self.assertEqual(new.z, old.z)
            bounds = report["local_bounds"]
            center = ((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
            self.assertAlmostEqual(new.x, old.x + center[0])
            self.assertAlmostEqual(new.y, old.y + center[1])
            self.assertAlmostEqual(new.matrix[0], 0.25)
            board.save(Path(folder) / "converted.pur")
            self.assertEqual(
                Board.load(Path(folder) / "converted.pur").get(3).kind, "image"
            )
            self.assertGreater(report["pixel_size"][0], 0)

    def test_keeps_child_world_transform_after_replacement(self):
        board = Board.load(FIXTURE)
        child = board.add_note("child", x=25, y=40, parent_id=3)

        def world(item_id):
            item = board.get(item_id)
            transform = qt.read_transform(
                board._conn.execute(
                    "SELECT transform FROM items WHERE id=?", (item_id,)
                ).fetchone()[0]
            )
            if item.parent_id is not None:
                transform = qt.multiply(transform, world(item.parent_id))
            return transform

        before = world(child)
        rasterize_drawing(board, 3, scale=3)
        for a, b in zip(before, world(child)):
            self.assertAlmostEqual(a, b)

    def test_invalid_scale_does_not_change_board(self):
        board = Board.load(FIXTURE)
        with self.assertRaises(ValueError):
            rasterize_drawing(board, 3, scale=0)
        self.assertEqual(board.get(3).kind, "drawing")
