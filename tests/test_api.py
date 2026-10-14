import hashlib
import math
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from pur_adapter import Board, InvalidPurFile, UnsupportedFormat


class BoardContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.image = self.root / "test.png"
        Image.new("RGBA", (120, 80), (220, 80, 30, 128)).save(self.image)

    def tearDown(self):
        self.temp.cleanup()

    def test_create_read_edit_unicode_and_assets(self):
        board = Board()
        image_id = board.add_image(self.image, x=50, y=-100, name="Café reference 🏛️")
        note_id = board.add_note("Naïve lighting 🌞", x=200, y=40)
        out = self.root / "board.pur"
        board.save(out)
        loaded = Board.load(out)
        self.assertEqual(len(loaded.items), 2)
        self.assertEqual(loaded.get(image_id).name, "Café reference 🏛️")
        self.assertEqual(loaded.get(note_id).text, "Naïve lighting 🌞")
        loaded.update(image_id, x=300, y=400, scale=0.5, rotation=30)
        loaded.update(note_id, text="Updated ✓")
        edited = self.root / "edited.pur"
        loaded.save(edited)
        reread = Board.load(edited)
        item = reread.get(image_id)
        self.assertEqual((item.x, item.y), (300, 400))
        self.assertAlmostEqual(item.matrix[0], 0.5 * math.cos(math.pi / 6))
        self.assertEqual(reread.get(note_id).text, "Updated ✓")
        assets = reread.export_assets(self.root / "assets")
        self.assertEqual(len(assets), 1)
        self.assertEqual(
            hashlib.sha256(Path(assets[0]).read_bytes()).digest(),
            hashlib.sha256(self.image.read_bytes()).digest(),
        )

    def test_empty_and_note_only(self):
        for with_note in (False, True):
            board = Board()
            if with_note:
                board.add_note("Only note")
            path = self.root / f"empty-{with_note}.pur"
            board.save(path)
            self.assertEqual(len(Board.load(path).items), int(with_note))

    def test_noop_preserves_file_bytes(self):
        b = Board()
        b.add_image(self.image)
        b.add_note("abc")
        p = self.root / "original.pur"
        b.save(p)
        q = self.root / "copy.pur"
        Board.load(p).save(q)
        self.assertEqual(p.read_bytes(), q.read_bytes())

    def test_remove_and_duplicate(self):
        b = Board()
        i = b.add_image(self.image)
        j = b.duplicate(i, x=500)
        b.add_note("Child", parent_id=j)
        p = self.root / "dupes.pur"
        b.save(p)
        c = Board.load(p)
        self.assertEqual(len(c.items), 3)
        self.assertEqual(len(c.export_assets(self.root / "assets")), 1)
        c.remove(j)
        q = self.root / "removed.pur"
        c.save(q)
        self.assertEqual(len(Board.load(q).items), 1)

    def test_reject_corruption_and_unknown_format(self):
        b = Board()
        b.add_image(self.image)
        p = self.root / "a.pur"
        b.save(p)
        data = bytearray(p.read_bytes())
        data[-1] ^= 1
        p.write_bytes(data)
        with self.assertRaises(InvalidPurFile):
            Board.load(p)
        p.write_bytes(b"unknown format")
        with self.assertRaises(UnsupportedFormat):
            Board.load(p)

    def test_overwrite_is_explicit_and_bad_update_is_atomic(self):
        b = Board()
        i = b.add_image(self.image)
        p = self.root / "a.pur"
        b.save(p)
        with self.assertRaises(FileExistsError):
            b.save(p)
        with self.assertRaises(ValueError):
            b.update(i, x=500, rotation=float("nan"))
        self.assertEqual(b.get(i).x, 0)
        b.save(p, overwrite=True)


if __name__ == "__main__":
    unittest.main()


class NativeFixtures(unittest.TestCase):
    def test_read_and_lossless_copy_real_modern_files(self):
        fixtures = Path(__file__).resolve().parents[1] / "fixtures"
        for version in ("2.0.3", "2.1.3"):
            source = fixtures / f"native-{version}.pur"
            with tempfile.TemporaryDirectory() as folder:
                with Board.load(source) as board:
                    self.assertEqual(board.get(0).name, "red")
                    self.assertEqual(board.application_version, version)
                    path = Path(folder) / "copy.pur"
                    board.save(path)
                    self.assertEqual(path.read_bytes(), source.read_bytes())

    def test_legacy_read_edit_and_write(self):
        fixtures = Path(__file__).resolve().parents[1] / "fixtures"
        for version in ("1.10.4", "1.11.1"):
            with tempfile.TemporaryDirectory() as folder:
                b = Board.load(fixtures / f"native-{version}.pur")
                self.assertEqual(b.get(0).name, "red")
                b.update(0, x=90, y=-70, name="Renamed ✓")
                b.add_note("Legacy note", x=300, y=100)
                p = Path(folder) / "edited.pur"
                b.save(p)
                loaded = Board.load(p)
                self.assertEqual(loaded.get(0).name, "Renamed ✓")
                self.assertEqual((loaded.get(0).x, loaded.get(0).y), (90, -70))
                self.assertEqual(loaded.get(1).text, "Legacy note")
                m = Path(folder) / "modern.pur"
                loaded.save(m, format="modern")
                self.assertEqual(Board.load(m).get(1).text, "Legacy note")
