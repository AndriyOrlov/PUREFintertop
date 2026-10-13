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
