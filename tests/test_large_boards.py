"""Boards opened through a temporary SQLite file instead of memory."""

import tempfile
import unittest
from pathlib import Path
from pur_adapter import Board, InvalidPurFile, UnsupportedFormat
from pur_adapter.container import pack_container, unpack_container

ROOT = Path(__file__).resolve().parents[1]
THOUSAND = ROOT / "validation/thousand/ordered.pur"
DRAWING = ROOT / "fixtures/native-drawing-2.1.3.pur"


class FileBackedBoards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_unchanged_large_board_saves_byte_for_byte(self):
        with Board.load(THOUSAND, in_memory_limit=0) as board:
            self.assertIsNotNone(board._database_path)
            board.save(self.folder / "copy.pur")
        self.assertEqual((self.folder / "copy.pur").read_bytes(), THOUSAND.read_bytes())

    def test_edit_and_reload_large_board(self):
        with Board.load(THOUSAND, in_memory_limit=0) as board:
            first = board.items[0]
            board.update(first.id, name="renamed in file mode")
            board.save(self.folder / "edited.pur")
        with Board.load(self.folder / "edited.pur") as reloaded:
            self.assertEqual(reloaded.get(first.id).name, "renamed in file mode")
            self.assertEqual(len(reloaded.items), 2050)

    def test_streamed_and_in_memory_writers_agree(self):
        with Board.load(DRAWING, in_memory_limit=0) as board:
            board.update(0, x=12.5)
            board.save(self.folder / "streamed.pur")
            in_memory = pack_container(board._conn, board.application_version, board._thumbnail)
        self.assertEqual((self.folder / "streamed.pur").read_bytes(), in_memory)

    def test_copy_and_close_remove_temporary_files(self):
        board = Board.load(DRAWING, in_memory_limit=0)
        clone = board.copy()
        clone.update(0, name="only in the copy")
        self.assertNotEqual(board.get(0).name, "only in the copy")
        folders = [Path(board._workdir.name), Path(clone._workdir.name)]
        clone.close()
        board.close()
        self.assertFalse(any(folder.exists() for folder in folders))

    def test_damaged_large_board_is_rejected(self):
        damaged = bytearray(DRAWING.read_bytes())
        damaged[-10] ^= 0xFF
        path = self.folder / "damaged.pur"
        path.write_bytes(damaged)
        with self.assertRaises(InvalidPurFile):
            Board.load(path, in_memory_limit=0)

    def test_sqlite_triggers_are_refused(self):
        with Board() as board:
            board._conn.execute(
                "CREATE TRIGGER sneaky AFTER UPDATE ON items BEGIN DELETE FROM images; END"
            )
            data = pack_container(board._conn, "2.1.3", board._thumbnail)
        with self.assertRaises(UnsupportedFormat):
            unpack_container(data)


if __name__ == "__main__":
    unittest.main()
