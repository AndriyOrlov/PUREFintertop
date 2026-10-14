"""Charts and the HTML board report."""

import tempfile
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from PIL import Image
from pur_adapter import Board, charts
from pur_adapter.report import board_report, collect, write_board_report

ROOT = Path(__file__).resolve().parents[1]


class Charts(unittest.TestCase):
    def test_line_chart_is_valid_svg_with_legend_and_log_axes(self):
        svg = charts.line_chart(
            [charts.Series("target", [(1, 1), (100, 100)], charts.MUTED, dashed=True),
             charts.Series("measured", [(1, 1.2), (10, 9), (100, 130)], markers=True)],
            x_label="size", y_label="time", log_x=True, log_y=True)
        root = ElementTree.fromstring(svg)
        texts = [element.text for element in root.iter("{http://www.w3.org/2000/svg}text")]
        self.assertIn("target", texts)
        self.assertIn("measured", texts)
        self.assertIn("100", texts)  # a decade tick on the log axis
        self.assertIn('stroke-dasharray="6 4"', svg)

    def test_bar_chart_scales_to_data_and_hides_far_thresholds(self):
        svg = charts.bar_chart([(2, "#ff0000"), (1, "#00ff00")],
                               thresholds=[(1.5, charts.GOOD), (50, charts.BAD)])
        ElementTree.fromstring(svg)
        self.assertIn(charts.GOOD, svg)
        self.assertNotIn(charts.BAD, svg)  # 50 is far above the data
        self.assertIn('fill="#ff0000"', svg)

    def test_grades_follow_the_calibration_colours(self):
        self.assertEqual(charts.grade(0.5, 1, 3), charts.GOOD)
        self.assertEqual(charts.grade(2, 1, 3), charts.WARM)
        self.assertEqual(charts.grade(4, 1, 3), charts.BAD)

    def test_charts_without_data_still_render(self):
        ElementTree.fromstring(charts.line_chart([charts.Series("empty", [])]))
        ElementTree.fromstring(charts.bar_chart([]))


class BoardReport(unittest.TestCase):
    def test_report_summarises_the_board(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            Image.new("RGB", (300, 200), (200, 20, 20)).save(folder / "red.png")
            Image.new("RGB", (40, 40), (20, 20, 200)).save(folder / "blue.jpg")
            with Board() as board:
                group = board.add_group("Refs")
                board.add_image(folder / "red.png", parent_id=group)
                board.add_image(folder / "blue.jpg", x=300)
                board.add_note("note")
                data = collect(board)
                red, blue = (image["color"] for image in data["images"])
                self.assertEqual(red, "#c81414")
                # JPEG is lossy: the average may be off by a unit or two.
                channels = [int(blue[i:i + 2], 16) for i in (1, 3, 5)]
                for actual, expected in zip(channels, (20, 20, 200)):
                    self.assertLessEqual(abs(actual - expected), 3)
                path = write_board_report(board, folder / "report.html")
            page = path.read_text(encoding="utf-8")
        for expected in ("Host round trip", "lossless", "Image sizes", "Pixel dimensions",
                         "Stored data by format", "data:image/jpeg;base64", "red.png"):
            self.assertIn(expected, page)

    def test_report_of_a_native_board(self):
        with Board.load(ROOT / "fixtures/native-drawing-2.1.3.pur") as board:
            page = board_report(board, preview=False)
        self.assertIn("modern · 2.1.3", page)
        self.assertIn(charts.GOOD, page)


if __name__ == "__main__":
    unittest.main()
