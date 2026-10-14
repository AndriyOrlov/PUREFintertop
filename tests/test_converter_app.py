"""Helpers behind the converter window (the window itself is checked by hand)."""

import tempfile
import unittest
from pathlib import Path
from pur_adapter.converter import (_file_size, _short_path, default_output, explain_error,
                                   report_path, summarize_changes, unique_path)


class ConverterHelpers(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_outputs_never_reuse_an_existing_name(self):
        board = self.folder / "refs.pur"
        board.write_bytes(b"")
        self.assertEqual(default_output(board), self.folder / "refs-FrameRef")
        (self.folder / "refs-FrameRef").mkdir()
        self.assertEqual(default_output(board), self.folder / "refs-FrameRef (2)")
        frameref = self.folder / "Moodboard" / "main.frameref"
        frameref.parent.mkdir()
        frameref.write_text("{}")
        self.assertEqual(default_output(frameref), self.folder / "Moodboard-PureRef.pur")
        (self.folder / "Moodboard-PureRef.pur").write_bytes(b"")
        self.assertEqual(unique_path(self.folder / "Moodboard-PureRef.pur"),
                         self.folder / "Moodboard-PureRef (2).pur")

    def test_report_paths_match_the_converter(self):
        self.assertEqual(report_path(Path("x/out.pur")), Path("x/out.pur.conversion.json"))
        self.assertEqual(report_path(Path("x/out-FrameRef")), Path("x/out-FrameRef/conversion.json"))

    def test_identical_simplifications_are_grouped(self):
        changes = [dict(item=str(n), code="text_layout", message="layout") for n in range(3)]
        changes.append(dict(item="9", code="something_new", message="Plain report message"))
        self.assertEqual(summarize_changes(changes),
                         [("Note text may wrap differently", 3), ("Plain report message", 1)])

    def test_known_errors_get_plain_explanations(self):
        self.assertIn("perspective", explain_error(
            "UnsupportedFormat: Perspective transforms are not supported").lower())
        self.assertIsNone(explain_error("KeyError: 'width'"))

    def test_long_paths_keep_both_ends(self):
        path = "C:\\Users\\Someone\\" + "very-long-folder\\" * 10 + "board.pur"
        short = _short_path(path, lambda text: len(text) <= 60)
        self.assertLessEqual(len(short), 60)
        self.assertTrue(short.startswith("C:\\Users"))
        self.assertTrue(short.endswith("board.pur"))
        self.assertIn("\u2026", short)

    def test_file_sizes_read_naturally(self):
        self.assertEqual(_file_size(16_000), "16 KB")
        self.assertEqual(_file_size(3_711_501), "3.5 MB")
        self.assertEqual(_file_size(1_600_000_000), "1.49 GB")


if __name__ == "__main__":
    unittest.main()
