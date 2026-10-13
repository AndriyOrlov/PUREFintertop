import io
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from pur_adapter import Board, UnsupportedFormat, InvalidPurFile
from pur_adapter.container import pack_container
from pur_adapter.compatibility import export_legacy
from pur_adapter import qt


class ValidationAndDowngrade(unittest.TestCase):
    def test_rejects_modern_sqlite_with_missing_columns(self):
        b = Board()
        b._conn.execute("ALTER TABLE items RENAME TO old_items")
        b._conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY)")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.pur"
            p.write_bytes(pack_container(b._conn, b.application_version, b._thumbnail))
            with self.assertRaises(UnsupportedFormat):
                Board.load(p)

    def test_direct_legacy_write_rejects_unbaked_image_opacity(self):
        fixture = Path(__file__).resolve().parents[1] / "fixtures/native-2.1.3.pur"
        b = Board.load(fixture)
        b.update(0, opacity=0.5)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(UnsupportedFormat):
                b.save(Path(d) / "bad.pur", format="legacy")

    def test_bad_matrix_returns_value_error_and_keeps_position(self):
        fixture = Path(__file__).resolve().parents[1] / "fixtures/native-2.1.3.pur"
        b = Board.load(fixture)
        with self.assertRaises(ValueError):
            b.update(0, x=100, matrix=None)
        self.assertEqual(b.get(0).x, 0)

    def test_gif_selected_frame_is_reported_and_exported(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            gif = root / "two.gif"
            Image.new("RGB", (20, 20), "red").save(
                gif,
                save_all=True,
                append_images=[Image.new("RGB", (20, 20), "green")],
                duration=100,
                loop=0,
            )
            b = Board()
            i = b.add_image(gif)
            b._conn.execute("UPDATE items_images SET playback_frame=1 WHERE id=?", (i,))
            b._conn.commit()
            out = root / "legacy.pur"
            report = export_legacy(b, out)
            self.assertIn(
                "animation_frame_selected", {c["code"] for c in report["changes"]}
            )
            c = Board.load(out)
            raw = c._conn.execute("SELECT data FROM images").fetchone()[0]
            with Image.open(io.BytesIO(raw)) as frame:
                self.assertEqual(frame.getpixel((0, 0))[:3], (0, 128, 0))

    def test_cropped_modern_image_stops_legacy_export(self):
        fixture = Path(__file__).resolve().parents[1] / "fixtures/native-2.1.3.pur"
        b = Board.load(fixture)
        b._conn.execute(
            "UPDATE items_images SET image_bounds=? WHERE id=0",
            (qt.image_bounds(50, 50),),
        )
        b._conn.commit()
        b._dirty = True
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(UnsupportedFormat):
                export_legacy(b, Path(d) / "bad.pur")
