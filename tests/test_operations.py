"""Merge, split, de-duplicate, arrange, search, linked images and shrinking."""

import shutil
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from pur_adapter import Board
from pur_adapter.assets import embed_linked, linked_images, relink, shrink_images
from pur_adapter.operations import (arrange_grid, deduplicate_images, merge_boards,
                                    search, split_by_group)
from pur_adapter.render import item_world_bounds


class Operations(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def picture(self, name, size=(40, 30), color=(200, 40, 40), **options):
        path = self.folder / name
        Image.new("RGB", size, color).save(path, **options)
        return path

    def test_merge_keeps_hierarchy_and_places_content_beside(self):
        shared = self.picture("shared.png")
        with Board() as target, Board() as source:
            target.add_image(shared, x=0)
            group = source.add_group("G", x=0)
            child = source.add_image(shared, parent_id=group)
            source.add_note("caption", y=40, parent_id=child)
            mapping = merge_boards(target, source)
            self.assertEqual(len(target.items), 4)
            self.assertEqual(target.get(mapping[child]).parent_id, mapping[group])
            images = target._conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
            self.assertEqual(images, 1)  # identical files are shared
            boxes = item_world_bounds(target)
            self.assertGreater(boxes[mapping[group]][0], boxes[0][2])

    def test_split_saves_each_group_alone(self):
        with Board() as board:
            for name in ("Faces", "Hands"):
                group = board.add_group(name)
                board.add_image(self.picture(f"{name}.png", color=(len(name), 0, 0)), parent_id=group)
            board.add_note("loose note")
            paths = split_by_group(board, self.folder / "parts")
        self.assertEqual(sorted(path.name for path in paths), ["Faces.pur", "Hands.pur"])
        with Board.load(self.folder / "parts/Faces.pur") as part:
            self.assertEqual(sorted(item.kind for item in part.items), ["group", "image"])

    def test_deduplicate_shares_identical_images(self):
        with Board() as board:
            first = board.add_image(self.picture("a.png"))
            second = board.add_image(self.picture("b.png", color=(1, 1, 1)))
            data = board._conn.execute("SELECT data, checksum FROM images WHERE id=0").fetchone()
            board._conn.execute("UPDATE images SET data=?, checksum=? WHERE id=1", tuple(data))
            self.assertEqual(deduplicate_images(board), 1)
            self.assertEqual(board.get(first).image_id, board.get(second).image_id)

    def test_arrange_grid_removes_overlaps_inside_a_scaled_group(self):
        with Board() as board:
            group = board.add_group("G")
            board.update(group, scale=2)
            for index, size in enumerate([(40, 30), (90, 20), (25, 70), (60, 60)]):
                board.add_image(self.picture(f"{index}.png", size, (index * 50, 0, 0)),
                                name=f"img{index}", parent_id=group)
            moved = arrange_grid(board, parent_id=group, gap=5)
            self.assertEqual(moved, 4)
            boxes = [box for item_id, box in item_world_bounds(board).items()
                     if board.get(item_id).kind == "image"]
            for i, a in enumerate(boxes):
                for b in boxes[i + 1:]:
                    overlap = a[0] < b[2] - 1e-6 and b[0] < a[2] - 1e-6 and \
                        a[1] < b[3] - 1e-6 and b[1] < a[3] - 1e-6
                    self.assertFalse(overlap, (a, b))

    def test_search_finds_names_comments_and_note_text(self):
        with Board() as board:
            board.add_image(self.picture("castle.png"), name="Castle gate")
            note = board.add_note("<p>Check the <b>lighting</b></p>")
            board.update(note, comment="from the art director")
            fields = {(hit["item_id"], hit["field"]) for hit in search(board, "LIGHT")}
            self.assertEqual(fields, {(note, "text")})
            self.assertEqual(search(board, "director")[0]["field"], "comment")
            self.assertEqual(search(board, "castle")[0]["field"], "name")

    def test_relink_and_embed_linked_images(self):
        original = self.picture("photo.png", (30, 20))
        with Board() as board:
            item = board.add_image(original)
            board._conn.execute("UPDATE images SET data=NULL, source=? WHERE id=0",
                                (str(self.folder / "old-place" / "photo.png"),))
            self.assertFalse(linked_images(board)[0]["exists"])
            moved = self.folder / "new-place"
            moved.mkdir()
            shutil.move(original, moved / "photo.png")
            report = relink(board, [self.folder])
            self.assertTrue(report["relinked"][0]["checksum_verified"])
            report = embed_linked(board)
            self.assertEqual(len(report["embedded"]), 1)
            self.assertEqual(linked_images(board), [])
            self.assertEqual(board.image_settings(item)["pixel_size"], (30, 20))

    def test_shrink_images_is_lossless(self):
        noisy = Image.effect_noise((256, 256), 40).convert("RGB")
        path = self.folder / "big.png"
        noisy.save(path, compress_level=0)
        with Board() as board:
            item = board.add_image(path)
            report = shrink_images(board)
            self.assertGreater(report["bytes_saved"], 0)
            data = board._conn.execute("SELECT data FROM images").fetchone()[0]
            from io import BytesIO
            with Image.open(BytesIO(data)) as image:
                self.assertEqual(image.convert("RGB").tobytes(), noisy.tobytes())
            self.assertEqual(board.get(item).kind, "image")


if __name__ == "__main__":
    unittest.main()
