import tempfile
import unittest
from pathlib import Path
from pur_adapter import Board
from pur_adapter.stress import run_scenario, verify_numbered_board


class ThousandImageAcceptance(unittest.TestCase):
    def test_full_board_survives_shuffle_restore_and_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            report = run_scenario(folder)
            self.assertEqual(report["images"], 1000)
            self.assertEqual(report["group_counts"], [100] * 10)
            self.assertEqual(report["drawings"], 20)
            self.assertEqual(report["items"], 2050)
            self.assertEqual(report["moved"], 1000)
            self.assertTrue(report["exact_layout_restored"])
            self.assertTrue(report["original_file_unchanged"])
            with Board.load(Path(folder) / "restored.pur") as board:
                self.assertEqual(verify_numbered_board(board)["captions"], 1000)
