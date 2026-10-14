"""The committed conformance suite must match what the adapter reads."""

import json
import unittest
from pathlib import Path
from pur_adapter import Board, InvalidPurFile, UnsupportedFormat
from pur_adapter.conformance import describe

SUITE = Path(__file__).resolve().parents[1] / "conformance"
ERRORS = {"InvalidPurFile": InvalidPurFile, "UnsupportedFormat": UnsupportedFormat}


def as_json(value):
    return json.loads(json.dumps(value))


class ConformanceSuite(unittest.TestCase):
    def test_every_case_matches_its_expected_description(self):
        cases = sorted((SUITE / "cases").iterdir())
        self.assertGreaterEqual(len(cases), 10)
        for case in cases:
            with self.subTest(case=case.name), Board.load(case / "board.pur") as board:
                expected = json.loads((case / "expected.json").read_text(encoding="utf-8"))
                self.assertEqual(as_json(describe(board)), expected)

    def test_native_resaves_keep_the_scene(self):
        for resaved in sorted(SUITE.glob("cases/*/native-resaved.pur")):
            expected = json.loads((resaved.parent / "expected.json").read_text(encoding="utf-8"))
            with self.subTest(case=resaved.parent.name), Board.load(resaved) as board:
                native = as_json(describe(board))
            self.assertEqual(native["drawing_order"], expected["drawing_order"])
            for before, after in zip(expected["items"], native["items"]):
                for key in set(before) - {"z", "order_index"}:  # PureRef renumbers these
                    self.assertEqual(after[key], before[key], f"{resaved.parent.name} {key}")

    def test_invalid_files_fail_with_the_documented_error(self):
        expected = json.loads((SUITE / "invalid/expected-errors.json").read_text())
        for name, error in expected.items():
            with self.subTest(file=name), self.assertRaises(ERRORS[error]):
                Board.load(SUITE / "invalid" / name).close()


if __name__ == "__main__":
    unittest.main()
