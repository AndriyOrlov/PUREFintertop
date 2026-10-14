import tempfile
import unittest
from pathlib import Path
from PIL import Image
from pur_adapter import Board
from pur_adapter.manifest import export_manifest, import_manifest


class ManifestBridge(unittest.TestCase):
    def test_roundtrip_assets_names_transform_and_parent(self):
        fixture = (
            Path(__file__).resolve().parents[1] / "fixtures/native-drawing-2.1.3.pur"
        )
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder) / "package"
            with Board.load(fixture) as original:
                report = export_manifest(original, package)
                strokes = original.drawing_strokes(3)
            manifest = package / "board.json"
            restored, notes = import_manifest(manifest)
            self.assertEqual(len(restored.items), 4)
            self.assertEqual(restored.get(0).name, "Ukrainian reference")
            self.assertEqual(restored.get(0).parent_id, 2)
            # Schema 2 keeps pen drawings as vectors, with a PNG preview beside them.
            self.assertEqual(restored.get(3).kind, "drawing")
            self.assertEqual(restored.drawing_strokes(3), strokes)
            self.assertTrue((package / "assets/drawing-3.png").exists())
            self.assertEqual(notes["unresolved"], [])
            self.assertEqual(report["unresolved"], [])
            restored.close()

    def test_media_preview_becomes_image_and_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Image.new("RGB", (20, 30), "red").save(root / "preview.png")
            manifest = {
                "schema_version": 1,
                "items": [
                    {
                        "id": "clip",
                        "kind": "video",
                        "preview_path": "preview.png",
                        "x": 100,
                        "y": 150,
                    }
                ],
            }
            b, report = import_manifest(manifest, base_dir=root)
            self.assertEqual(b.get(0).kind, "image")
            self.assertEqual((b.get(0).x, b.get(0).y), (100, 150))
            self.assertEqual(report["changes"][0]["code"], "media_preview_used")

    def test_rejects_path_escape_and_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            for manifest in [
                {
                    "schema_version": 1,
                    "items": [{"id": 0, "kind": "image", "path": "../outside.png"}],
                },
                {
                    "schema_version": 1,
                    "items": [
                        {"id": 0, "kind": "note", "text": "a"},
                        {"id": 0, "kind": "note", "text": "b"},
                    ],
                },
            ]:
                with self.assertRaises(ValueError):
                    import_manifest(manifest, base_dir=folder)
