"""Recovering damaged boards."""

import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from pur_adapter import Board
from pur_adapter.container import read_header
from pur_adapter.repair import repair

ROOT = Path(__file__).resolve().parents[1]


class Repair(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def damaged(self, name, data):
        path = self.folder / name
        path.write_bytes(bytes(data))
        return path

    def small_board(self, notes=True):
        board = Board()
        for index in range(3):
            path = self.folder / f"tile{index}.png"
            Image.new("RGB", (12, 12), (index * 80, 30, 200)).save(path)
            image = board.add_image(path, x=index * 20)
            if notes:
                board.add_note(f"note {index}", y=30, parent_id=image)
        path = board.save(self.folder / "good.pur", refresh_preview=False)
        board.close()
        return path

    def test_healthy_file_is_left_alone(self):
        report = repair(ROOT / "fixtures/native-2.1.3.pur", self.folder / "out")
        self.assertEqual(report["status"], "healthy")
        self.assertFalse((self.folder / "out/recovered.pur").exists())

    def test_wrong_checksum_only(self):
        data = bytearray((ROOT / "fixtures/native-2.1.3.pur").read_bytes())
        header = read_header(io.BytesIO(bytes(data)))
        data[header.header_size - 5] ^= 0xFF  # inside the thumbnail
        report = repair(self.damaged("bad.pur", data), self.folder / "out")
        self.assertEqual(report["status"], "checksum_repaired")
        with Board.load(self.folder / "out/recovered.pur") as board:
            self.assertEqual(len(board.items), 1)

    def test_damaged_table_keeps_the_rest(self):
        source = self.small_board()
        data = bytearray(source.read_bytes())
        header = read_header(io.BytesIO(bytes(data)))
        database = bytes(data[header.database_size:] + data[header.header_size:header.database_size])
        connection = sqlite3.connect(":memory:")
        connection.deserialize(database)
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        notes_page = connection.execute(
            "SELECT rootpage FROM sqlite_master WHERE name='items_notes'").fetchone()[0]
        connection.close()
        # Overwrite the notes table page with garbage, in its relocated position.
        offset = (notes_page - 1) * page_size
        self.assertGreaterEqual(offset, header.header_size)
        data[offset:offset + page_size] = b"\xA5" * page_size
        report = repair(self.damaged("bad.pur", data), self.folder / "out")
        self.assertEqual(report["status"], "partially_recovered")
        problems = {entry["problem"] for entry in report["broken_references"]}
        self.assertEqual(problems, {"item details lost"})
        with Board.load(self.folder / "out/recovered.pur") as board:
            self.assertEqual(sorted(item.kind for item in board.items), ["image"] * 3)

    def test_unreadable_header_falls_back_to_pictures(self):
        data = self.small_board(notes=False).read_bytes()
        report = repair(self.damaged("bad.pur", b"\0\0\0\x06" + data[40:]), self.folder / "out")
        self.assertEqual(report["status"], "images_only")
        colours = set()
        for path in (self.folder / "out/images").iterdir():
            with Image.open(path) as image:
                colours.add(image.convert("RGB").getpixel((0, 0)))
        # All three tiles; the old thumbnail JPEG is found as well.
        self.assertTrue({(0, 30, 200), (80, 30, 200), (160, 30, 200)} <= colours)

    def test_legacy_file_with_wrong_checksum(self):
        data = bytearray((ROOT / "fixtures/native-1.11.1.pur").read_bytes())
        data[50] = ord("0") if data[50] != ord("0") else ord("1")  # checksum text
        report = repair(self.damaged("bad.pur", data), self.folder / "out")
        self.assertEqual(report["status"], "checksum_repaired")
        with Board.load(self.folder / "out/recovered.pur") as board:
            self.assertEqual([item.kind for item in board.items], ["image"])


if __name__ == "__main__":
    unittest.main()
