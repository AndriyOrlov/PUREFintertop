import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from pur_adapter.cli import main
from pur_adapter import Board

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/native-2.1.3.pur"


class CommandLine(unittest.TestCase):
    def test_inspect_and_patch_are_json_and_do_not_overwrite_input(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "edited.pur"
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                result = main(
                    [
                        "patch",
                        str(FIXTURE),
                        str(output),
                        "--item",
                        "0",
                        "--set",
                        '{"x":20,"name":"Змінено"}',
                    ]
                )
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(stream.getvalue())["item"], 0)
            self.assertEqual(Board.load(output).get(0).name, "Змінено")
            self.assertEqual(Board.load(FIXTURE).get(0).name, "red")

    def test_bad_properties_return_structured_error(self):
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            result = main(
                [
                    "patch",
                    str(FIXTURE),
                    "unused.pur",
                    "--item",
                    "0",
                    "--set",
                    '{"scale":-1}',
                ]
            )
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(stream.getvalue())["error"], "ValueError")


class NewCommands(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *arguments):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = main([str(argument) for argument in arguments])
        self.assertEqual(code, 0)
        return json.loads(stream.getvalue())

    def test_host_round_trip_keeps_frameref_data_next_to_the_board(self):
        from PIL import Image

        Image.new("RGB", (40, 30), "red").save(self.folder / "poster.png")
        manifest = self.folder / "board.json"
        manifest.write_text(json.dumps({"schema_version": 2, "items": [
            {"id": "clip", "kind": "video", "preview_path": "poster.png",
             "url": "https://example.com/v", "extensions": {"trim": [1, 4]}}]}))
        board_path = self.folder / "board.pur"
        result = self.run_cli("create", manifest, board_path)
        self.assertTrue(Path(result["report"]["sidecar"]).exists())
        self.run_cli("export-package", board_path, self.folder / "package")
        exported = json.loads((self.folder / "package/board.json").read_text(encoding="utf-8"))
        self.assertEqual(exported["items"][0]["extensions"]["trim"], [1, 4])

    def test_render_search_merge_split_and_repair(self):
        rendered = self.run_cli("render", FIXTURE, self.folder / "board.png", "--width", 300)
        self.assertEqual(rendered["size"][0], 300)
        self.assertEqual(self.run_cli("search", FIXTURE, "red")["matches"][0]["item_id"], 0)
        merged = self.run_cli("merge", FIXTURE, FIXTURE, "--output", self.folder / "merged.pur")
        self.assertEqual(merged["items"], 2)
        drawing = FIXTURE.parent / "native-drawing-2.1.3.pur"
        split = self.run_cli("split", drawing, self.folder / "parts")
        self.assertEqual(len(split["boards"]), 1)
        repaired = self.run_cli("repair", FIXTURE, self.folder / "repair")
        self.assertEqual(repaired["status"], "healthy")
        self.assertEqual(self.run_cli("linked", FIXTURE)["linked"], [])
