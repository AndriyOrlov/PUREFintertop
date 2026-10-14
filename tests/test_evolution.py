import tempfile
import unittest
from pathlib import Path
from pur_adapter import Board, UnsupportedFormat
from pur_adapter.container import pack_container
from pur_adapter.drawings import encode_strokes, parse_strokes

ROOT = Path(__file__).resolve().parents[1]


class FormatEvolution(unittest.TestCase):
    def test_new_release_same_layout_is_unverified(self):
        with Board() as board, tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "future.pur"
            path.write_bytes(pack_container(board._conn, "2.99.0", board._thumbnail))
            with Board.load(path) as loaded:
                self.assertEqual(
                    loaded.to_dict()["compatibility"]["status"], "unverified_release"
                )

    def test_optional_native_column_survives_edit(self):
        with Board() as board, tempfile.TemporaryDirectory() as folder:
            item = board.add_note("original")
            board._conn.execute("ALTER TABLE items ADD COLUMN future_property TEXT")
            board._conn.execute("UPDATE items SET future_property='opaque'")
            path = Path(folder) / "extended.pur"
            board.save(path)
            with Board.load(path) as loaded:
                loaded.update(item, text="edited")
                loaded.save(Path(folder) / "edited.pur")
            with Board.load(Path(folder) / "edited.pur") as loaded:
                self.assertEqual(
                    loaded._conn.execute(
                        "SELECT future_property FROM items"
                    ).fetchone()[0],
                    "opaque",
                )

    def test_native_legacy_release_and_unrecorded_writer(self):
        with Board.load(ROOT / "fixtures/native-1.11.1.pur") as board:
            self.assertEqual(board.application_version, "1.11.1")
        with Board() as board, tempfile.TemporaryDirectory() as folder:
            board.add_note("simple")
            path = Path(folder) / "legacy.pur"
            board.save(path, format="legacy")
            with Board.load(path) as loaded:
                self.assertIsNone(loaded.application_version)
                self.assertEqual(
                    loaded.to_dict()["compatibility"]["status"],
                    "legacy_release_not_recorded",
                )

    def test_observed_modern_table_sql_is_equal(self):
        schemas = []
        for release in ("2.0.3", "2.1.3"):
            with Board.load(ROOT / f"fixtures/native-{release}.pur") as board:
                schemas.append(
                    board._conn.execute(
                        "SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"
                    ).fetchall()
                )
        self.assertEqual(
            [tuple(row) for row in schemas[0]], [tuple(row) for row in schemas[1]]
        )

    def test_native_drawing_encoder_preserves_measured_stroke(self):
        with Board.load(ROOT / "fixtures/native-drawing-2.1.3.pur") as board:
            strokes = board.drawing_strokes(3)
            self.assertEqual(parse_strokes(encode_strokes(strokes)), strokes)

    def test_legacy_direct_export_rejects_comments_and_image_flags(self):
        with Board() as board, tempfile.TemporaryDirectory() as folder:
            item = board.add_image(ROOT / "examples/assets/reference.png")
            board.update(item, comment="do not lose")
            with self.assertRaises(UnsupportedFormat):
                board.save(Path(folder) / "legacy.pur", format="legacy")
            board.update(item, comment=None)
            board.configure_image(item, grayscale=True)
            with self.assertRaises(UnsupportedFormat):
                board.save(Path(folder) / "legacy.pur", format="legacy")
