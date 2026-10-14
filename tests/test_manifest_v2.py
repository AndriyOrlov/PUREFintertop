"""Schema 2 exchange: lossless round trips, FrameRef media mapping, sidecar data."""

import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image, ImageChops
from pur_adapter import Board, qt
from pur_adapter.drawings import ARROW, Stroke
from pur_adapter.manifest import export_manifest, import_manifest
from pur_adapter.render import render_scene
from pur_adapter.sidecar import read_sidecar, sidecar_path, write_sidecar


class ManifestVersion2(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def picture(self, name, size=(80, 60), color=(200, 40, 40)):
        path = self.folder / name
        Image.new("RGB", size, color).save(path)
        return path

    def rich_board(self):
        board = Board()
        group = board.add_group("References", x=40, y=30, color="#ff334455", locked_mode=False)
        board.update(group, comment="group comment")
        image = board.add_image(self.picture("a.png"), x=10, y=5, parent_id=group)
        board.configure_image(image, crop=(10, 5, 40, 30), flip_horizontal=True,
                              grayscale=True)
        board.update(image, rotation=15, scale=1.5, opacity=0.7, locked=True, z=4)
        second = board.add_image(self.picture("b.png", color=(10, 200, 30)), x=200,
                                 parent_id=group)
        note = board.add_note("<p>Caption <b>bold</b></p>", y=60, parent_id=image)
        board.style_note(note, background_color="#ff102030", text_color="#ffeeeeee",
                         width=150, height=40)
        board.add_drawing([Stroke((250, 200, 60, 255), 3, ((0, 0, 0), (1, 90, 40)), 0,
                                  (0, 0), ARROW)], x=-100, parent_id=group)
        board.reorder([second, image] + [child for child in board.ordered_children(group)
                                         if child not in (second, image)])
        return board

    def test_round_trip_keeps_every_readable_property(self):
        with self.rich_board() as original:
            export_manifest(original, self.folder / "package")
            before = {item.id: item for item in original.items}
            settings = {item.id: original.image_settings(item.id)
                        for item in original.items if item.kind == "image"}
            order = original.ordered_children(0)
            render_before = render_scene(original, width=400)
        restored, report = import_manifest(self.folder / "package/board.json")
        with restored:
            self.assertEqual(report["unresolved"], [])
            for item in restored.items:
                old = before[item.id]
                for field in ("kind", "name", "parent_id", "x", "y", "opacity", "locked",
                              "z", "comment", "text"):
                    self.assertEqual(getattr(item, field), getattr(old, field), field)
                for a, b in zip(item.matrix, old.matrix):
                    self.assertAlmostEqual(a, b)
                if item.kind == "image":
                    self.assertEqual(restored.image_settings(item.id), settings[item.id])
            self.assertEqual(restored.ordered_children(0), order)
            self.assertEqual(restored.group_style(0),
                             {"background_color": "#ff334455", "locked_mode": False})
            render_after = render_scene(restored, width=400)
        self.assertIsNone(ImageChops.difference(render_before, render_after).getbbox())

    def test_export_describes_items_for_a_host(self):
        with self.rich_board() as board:
            export_manifest(board, self.folder / "package")
        document = json.loads((self.folder / "package/board.json").read_text(encoding="utf-8"))
        self.assertEqual(document["schema_version"], 2)
        self.assertTrue((self.folder / "package/preview.png").exists())
        image = next(item for item in document["items"] if item["name"] == "a")
        self.assertEqual(image["image"]["crop"], [10, 5, 40, 30])
        self.assertTrue(image["image"]["flip_horizontal"])
        note = next(item for item in document["items"] if item["kind"] == "note")
        self.assertEqual(note["plain_text"], "Caption bold")
        self.assertEqual(note["text_format"], "html")
        drawing = next(item for item in document["items"] if item["kind"] == "drawing")
        self.assertEqual(drawing["strokes"][0]["style"], "arrow")
        stacking = sorted(document["items"], key=lambda item: item["stacking_index"])
        self.assertEqual(stacking[0]["kind"], "group")  # a group is drawn below its children

    def test_failed_export_leaves_no_package(self):
        with Board() as board:
            board.add_note("x")
            board._conn.execute("UPDATE items SET transform='broken'")
            with self.assertRaises(ValueError):
                export_manifest(board, self.folder / "package")
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_frameref_media_is_mapped_and_reported(self):
        self.picture("poster.png")
        self.picture("p1.png", (100, 140))
        self.picture("p2.png", (100, 140), (0, 0, 200))
        manifest = {
            "schema_version": 2,
            "items": [
                {"id": "clip", "kind": "video", "preview_path": "poster.png",
                 "url": "https://youtu.be/example", "extensions": {"trim": [12.5, 30]}},
                {"id": "doc", "kind": "pdf", "title": "Plan",
                 "pages": [{"path": "p1.png", "page": 3}, "p2.png"], "x": 500},
                {"id": "pointer", "kind": "arrow", "points": [[0, 0], [120, 40]],
                 "color": "#ffff8800", "width": 4},
                {"id": "site", "kind": "link", "url": "https://example.com", "title": "Ref"},
                {"id": "text", "kind": "note", "text": "line one\nline two",
                 "text_format": "plain"},
            ],
        }
        board, report = import_manifest(manifest, base_dir=self.folder)
        with board:
            ids = report["id_map"]
            video = board.get(ids["clip"])
            self.assertEqual((video.kind, video.comment), ("image", "https://youtu.be/example"))
            self.assertEqual(report["extensions"][ids["clip"]],
                             {"trim": [12.5, 30], "kind": "video",
                              "url": "https://youtu.be/example"})
            pdf = board.get(ids["doc"])
            pages = [board.get(child) for child in board.ordered_children(pdf.id)]
            self.assertEqual(pdf.kind, "group")
            self.assertEqual([page.name for page in pages], ["Page 3", "Page 2"])
            self.assertEqual([page.x for page in pages], [50, 170])
            arrow = board.drawing_strokes(ids["pointer"])[0]
            self.assertEqual((arrow.style, arrow.color, arrow.width), (ARROW, (255, 136, 0, 255), 4))
            link = board.get(ids["site"])
            self.assertEqual(link.kind, "note")
            self.assertIn("https://example.com", link.text)
            self.assertIn("line one<br>line two", board.get(ids["text"]).text)
        codes = {change["code"] for change in report["changes"]}
        self.assertTrue({"media_preview_used", "pdf_pages_as_images", "arrow_as_pen_drawing",
                         "media_as_note", "plain_text_to_html"} <= codes)

    def test_unknown_fields_and_kinds_are_reported_or_refused(self):
        manifest = {"schema_version": 2, "items": [
            {"id": 1, "kind": "note", "text": "a", "rotaton": 15},
            {"id": 2, "kind": "hologram"},
        ]}
        board, report = import_manifest(manifest, base_dir=self.folder)
        board.close()
        problems = [(entry["code"], entry.get("field") or entry.get("kind"))
                    for entry in report["unresolved"]]
        self.assertEqual(problems, [("unknown_field", "rotaton"), ("unsupported_kind", "hologram")])
        with self.assertRaises(ValueError):
            import_manifest(manifest, base_dir=self.folder, strict=True)

    def test_sidecar_keeps_host_data_and_notices_changes(self):
        with Board() as board:
            video = board.add_image(self.picture("v.png"), name="clip")
            note = board.add_note("hello")
            path = board.save(self.folder / "board.pur")
            write_sidecar(board, path, {video: {"kind": "video", "trim": [1, 2]},
                                        note: {"kind": "audio"}}, host="FrameRef")
        self.assertTrue(sidecar_path(path).exists())
        with Board.load(path) as board:
            data, report = read_sidecar(board, path)
            self.assertEqual(data, {video: {"kind": "video", "trim": [1, 2]},
                                    note: {"kind": "audio"}})
            board.update(note, text="changed in PureRef")
            data, report = read_sidecar(board, path)
            self.assertEqual(list(data), [video])
            self.assertEqual(report["stale"][0]["item_id"], note)

    def test_note_without_style_stays_without_style(self):
        # Found by the board report: empty colours must not become explicit ones.
        with Board() as board:
            note = board.add_note("plain")
            before = board.note_style(note)
            export_manifest(board, self.folder / "package", scene_preview=False)
        restored, _ = import_manifest(self.folder / "package/board.json")
        with restored:
            self.assertEqual(restored.note_style(note), before)

    def test_version_one_documents_still_import(self):
        self.picture("old.png")
        manifest = {"schema_version": 1, "items": [
            {"id": 0, "kind": "image", "path": "old.png", "image_id": 0, "text": None,
             "pixel_size": [80, 60], "matrix": [1, 0, 0, 0, 1, 0, 5, 6, 1],
             "native_image_properties": {
                 "image_bounds": qt.image_bounds(80, 60),
                 "image_transform": qt.transform(qt.affine(-40, -30)),
                 "flags": 1}},
            {"id": 1, "kind": "group", "name": "G"},
        ]}
        board, report = import_manifest(manifest, base_dir=self.folder)
        with board:
            self.assertEqual(report["unresolved"], [])
            self.assertEqual((board.get(0).x, board.get(0).y), (5, 6))
            self.assertEqual(board.group_style(1)["background_color"], "")


if __name__ == "__main__":
    unittest.main()
