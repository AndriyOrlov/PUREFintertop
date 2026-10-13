import tempfile
import unittest
from pathlib import Path
from pur_adapter import Board, UnsupportedFormat
from pur_adapter.compatibility import export_legacy
from pur_adapter import qt

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/native-drawing-2.1.3.pur"


class CompatibilityExport(unittest.TestCase):
    def test_flattens_group_and_rasterizes_pen_without_changing_original(self):
        board = Board.load(FIXTURE)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "simple.pur"
            report = export_legacy(board, path)
            simple = Board.load(path)
            self.assertEqual({i.kind for i in simple.items}, {"image", "note"})
            self.assertTrue(all(i.parent_id is None for i in simple.items))
            self.assertEqual((simple.get(0).x, simple.get(0).y), (125, -60))
            self.assertEqual(board.get(3).kind, "drawing")
            self.assertIn(
                "drawing_rasterized", {entry["code"] for entry in report["changes"]}
            )

    def test_preserves_parent_rotation_in_flattened_coordinates(self):
        board = Board.load(FIXTURE)
        board.update(2, x=400, y=300, rotation=35, scale=1.5)
        local = qt.read_transform(
            board._conn.execute("SELECT transform FROM items WHERE id=0").fetchone()[0]
        )
        parent = qt.read_transform(
            board._conn.execute("SELECT transform FROM items WHERE id=2").fetchone()[0]
        )
        expected = qt.multiply(local, parent)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "flat.pur"
            export_legacy(board, path)
            item = Board.load(path).get(0)
            self.assertAlmostEqual(item.x, expected[6])
            self.assertAlmostEqual(item.y, expected[7])
            self.assertAlmostEqual(item.matrix[0], expected[0])

    def test_reports_baked_opacity(self):
        board = Board.load(FIXTURE)
        board.update(0, opacity=0.5)
        with tempfile.TemporaryDirectory() as folder:
            report = export_legacy(board, Path(folder) / "a.pur")
            self.assertIn(
                "opacity_baked", {entry["code"] for entry in report["changes"]}
            )
