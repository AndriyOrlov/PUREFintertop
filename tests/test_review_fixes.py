"""Regression tests for the problems found in the 2026-10-14 code review."""

import tempfile
import threading
import unittest
from pathlib import Path
from PIL import Image
from pur_adapter import Board, UnsupportedFormat, qt
from pur_adapter.compatibility import plain_text, simplify_board
from pur_adapter.drawings import draw_dashes
from pur_adapter.manifest import export_manifest, import_manifest

ROOT = Path(__file__).resolve().parents[1]
LEGACY_FIXTURE = ROOT / "fixtures/native-1.11.1.pur"


class ReviewFixes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def picture(self, name, size, color=(255, 0, 0), **save_options):
        path = self.folder / name
        Image.new("RGB", size, color).save(path, **save_options)
        return path

    def test_dashed_line_that_used_to_hang_finishes(self):
        # Floating-point steps once stalled one ulp before a segment end.
        points = [
            (0.0, 0.0),
            (-27.558, -13.073),
            (34.695, -3.325),
            (29.337, 123.861),
            (41.721, 227.972),
        ]

        class CountingDraw:
            lines = 0

            def line(self, *args, **kwargs):
                self.lines += 1

        draw = CountingDraw()
        worker = threading.Thread(
            target=draw_dashes, args=(draw, points, (0, 0, 0, 255), 12), daemon=True
        )
        worker.start()
        worker.join(10)
        self.assertFalse(worker.is_alive(), "draw_dashes did not finish")
        self.assertGreater(draw.lines, 0)

    def test_new_legacy_image_does_not_inherit_removed_crop(self):
        wide = self.picture("wide.png", (400, 40), (0, 0, 255))
        with Board.load(LEGACY_FIXTURE) as board:
            removed = board.items[-1].id
            board.remove(removed)
            added = board.add_image(wide)
            self.assertEqual(added, removed)  # the ID really is reused
            board.save(self.folder / "reuse.pur")
        with Board.load(self.folder / "reuse.pur") as board:
            node = board._legacy["items"][board.items[-1].id]
            self.assertEqual(max(node.points[0]) - min(node.points[0]), 400)
            self.assertEqual(max(node.points[1]) - min(node.points[1]), 40)

    def test_duplicated_legacy_image_keeps_its_record(self):
        with Board.load(LEGACY_FIXTURE) as board:
            original = board.items[0].id
            copy_id = board.duplicate(original, x=500)
            self.assertIsNot(board._legacy["items"][copy_id], board._legacy["items"][original])
            self.assertEqual(
                board._legacy["items"][copy_id].points,
                board._legacy["items"][original].points,
            )

    def test_legacy_save_rejects_perspective_instead_of_dropping_it(self):
        with Board() as board:
            item = board.add_image(self.picture("a.png", (64, 64)))
            matrix = board.transform_matrix(item)
            matrix[qt.PERSPECTIVE_X] = 0.001
            board._conn.execute(
                "UPDATE items SET transform=? WHERE id=?", (qt.transform(matrix), item)
            )
            board._dirty = True
            with self.assertRaises(UnsupportedFormat):
                board.save(self.folder / "legacy.pur", format="legacy")

    def test_flattening_keeps_items_outside_a_group_on_top(self):
        # Native PureRef draws `outside` (z=3) over `inside` (z=10 in a z=1 group).
        with Board() as board:
            group = board.add_group("G")
            inside = board.add_image(self.picture("r.png", (32, 32)), parent_id=group)
            outside = board.add_image(self.picture("g.png", (32, 32), (0, 255, 0)))
            board.update(group, z=1)
            board.update(inside, z=10)
            board.update(outside, z=3)
            flat, report = simplify_board(board)
            with flat:
                self.assertGreater(flat.get(outside).z, flat.get(inside).z)
            codes = [change["code"] for change in report["changes"]]
            self.assertIn("stacking_flattened", codes)

    def test_manifest_round_trip_keeps_comment(self):
        with Board() as board:
            item = board.add_image(self.picture("c.png", (16, 16)))
            board.update(item, comment="keep me")
            export_manifest(board, self.folder / "package")
        restored, _ = import_manifest(self.folder / "package/board.json")
        with restored:
            self.assertEqual(restored.items[0].comment, "keep me")

    def test_remove_cleans_unused_images_even_with_null_reference(self):
        with Board() as board:
            first = board.add_image(self.picture("d.png", (16, 16)))
            broken = board.add_image(self.picture("e.png", (16, 16), (1, 2, 3)))
            kept = board.add_image(self.picture("f.png", (16, 16), (4, 5, 6)))
            board._conn.execute("UPDATE items_images SET image=NULL WHERE id=?", (broken,))
            board.remove(first)
            remaining = [row[0] for row in board._conn.execute("SELECT id FROM images")]
            self.assertEqual(remaining, [board.get(kept).image_id])

    def test_plain_note_mentioning_tags_is_not_treated_as_html(self):
        text = "Use <paint> layer, then <pass> 2"
        self.assertEqual(plain_text(text), text)
        self.assertEqual(plain_text("<p>caption</p>"), "caption")
        self.assertEqual(plain_text('<!DOCTYPE HTML><html><p>a</p></html>'), "a")

    def test_image_formats_are_spelled_like_native_pureref(self):
        cases = {
            "photo.jpg": ("JPEG", "JPG"),
            "photo2.jpeg": ("JPEG", "JPEG"),
            "scan.tif": ("TIFF", "TIF"),
            "png-named.jpg": ("PNG", "PNG"),
        }
        with Board() as board:
            for shade, (name, (pillow_format, expected)) in enumerate(cases.items()):
                # Different colours, so identical files are not merged into one.
                path = self.picture(name, (8, 8), (shade * 60, 0, 0), format=pillow_format)
                board.add_image(path)
                stored = board._conn.execute(
                    "SELECT format FROM images ORDER BY id DESC LIMIT 1"
                ).fetchone()[0]
                self.assertEqual(stored, expected, name)

    def test_exif_rotated_photo_uses_displayed_size(self):
        exif = Image.Exif()
        exif[0x0112] = 6  # rotate 90 degrees when displayed
        path = self.picture("rotated.jpg", (60, 20), exif=exif)
        with Board() as board:
            item = board.add_image(path)
            width, height = board._conn.execute(
                "SELECT width, height FROM images"
            ).fetchone()
            self.assertEqual((width, height), (20, 60))
            bounds = board._conn.execute(
                "SELECT image_bounds FROM items_images WHERE id=?", (item,)
            ).fetchone()[0]
            self.assertEqual(qt.read_path_rect(bounds), (-10, -30, 20, 60))

    def test_crop_and_flip_give_the_same_result_in_any_order(self):
        path = self.picture("quad.png", (100, 100))
        results = []
        for steps in (
            [dict(crop=(0, 0, 50, 50)), dict(flip_horizontal=True)],
            [dict(flip_horizontal=True), dict(crop=(0, 0, 50, 50))],
            [dict(flip_horizontal=True, crop=(0, 0, 50, 50))],
        ):
            with Board() as board:
                item = board.add_image(path)
                for options in steps:
                    board.configure_image(item, **options)
                row = board.image_options(item)
                results.append((row["image_bounds"], row["image_transform"]))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])
        # The top-left bitmap quarter appears on the right after a horizontal flip.
        bounds = qt.read_path_rect(bytes.fromhex(results[0][0]))
        self.assertEqual(bounds, (0, -50, 50, 50))

    def test_path_rect_round_trip(self):
        self.assertEqual(qt.read_path_rect(qt.path_rect(-5, 2, 10, 20)), (-5, 2, 10, 20))
        self.assertIsNone(qt.read_path_rect(b"not a path"))


if __name__ == "__main__":
    unittest.main()
