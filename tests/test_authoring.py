import tempfile
import unittest
from pathlib import Path
from pur_adapter import Board, UnsupportedFormat
from pur_adapter.drawings import Stroke, encode_strokes, parse_strokes, render_strokes
from pur_adapter.compatibility import simplify_board
from pur_adapter.layout import capture_layout, restore_layout, shuffle_images

ASSET = Path(__file__).resolve().parents[1] / "examples/assets/reference.png"


class Authoring(unittest.TestCase):
    def test_group_drawing_note_and_comment_roundtrip(self):
        with Board() as board, tempfile.TemporaryDirectory() as folder:
            group = board.add_group("Group", x=100, y=200, locked_mode=False)
            note = board.add_note("<p>caption</p>", parent_id=group)
            board.style_note(note, width=180, height=40)
            board.update(group, comment="Group comment")
            stroke = Stroke((220, 60, 80, 200), 3, ((0, 0, 0), (1, 80, 30)), 0, (0, 0), 2)
            drawing = board.add_drawing([stroke], parent_id=group)
            path = Path(folder) / "board.pur"
            board.save(path)
            with Board.load(path) as restored:
                self.assertEqual(restored.get(group).comment, "Group comment")
                self.assertEqual(restored.note_style(note)["width"], 180)
                self.assertEqual(restored.drawing_strokes(drawing), [stroke])
                self.assertEqual(restored.get(drawing).parent_id, group)

    def test_invalid_drawing_leaves_board_unchanged(self):
        with Board() as board:
            before = capture_layout(board)
            bad = Stroke((0, 0, 0, 255), 2, ((1, 1, 2),), 0, (0, 0), 0)
            with self.assertRaises(ValueError):
                board.add_drawing([bad])
            self.assertEqual(capture_layout(board), before)

    def test_ordering_requires_all_siblings(self):
        with Board() as board:
            a, b = board.add_note("a"), board.add_note("b")
            board.reorder([b, a])
            self.assertEqual(board.ordered_children(), [b, a])
            before = capture_layout(board)
            for invalid in ([a], [a, a], [b, b]):
                with self.assertRaises(ValueError):
                    board.reorder(invalid)
            self.assertEqual(capture_layout(board), before)

    def test_image_options_and_comment_reload(self):
        with Board() as board, tempfile.TemporaryDirectory() as folder:
            item = board.add_image(ASSET)
            board.configure_image(item, crop=(2, 3, 10, 12), flip_horizontal=True, grayscale=True, bilinear=False)
            board.update(item, comment="look here")
            path = Path(folder) / "options.pur"
            board.save(path)
            with Board.load(path) as restored:
                options = restored.image_options(item)
                self.assertEqual(options["flags"], 2)
                self.assertLess(options["image_transform"][0], 0)
                self.assertEqual(restored.get(item).comment, "look here")

    def test_invalid_image_options_are_atomic(self):
        with Board() as board:
            item = board.add_image(ASSET)
            before = board.image_options(item)
            with self.assertRaises(ValueError):
                board.configure_image(item, crop=(0, 0, 10, 10), frame=9)
            self.assertEqual(board.image_options(item), before)

    def test_measured_styles_roundtrip_and_render(self):
        strokes = [Stroke((50, 120, 200, 180), 3, ((0, 0, n * 15), (1, 60, n * 15)), 0, (0, 0), n) for n in range(3)]
        self.assertEqual(parse_strokes(encode_strokes(strokes)), strokes)
        png, bounds, size = render_strokes(strokes)
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertGreater(size[0], 240)
        unknown = Stroke((0, 0, 0, 255), 3, ((0, 0, 0),), 0, (0, 0), 99)
        with self.assertRaises(UnsupportedFormat):
            render_strokes([unknown])

    def test_comment_becomes_note_in_legacy(self):
        with Board() as board:
            group = board.add_group()
            board.update(group, comment="preserve this")
            simple, report = simplify_board(board)
            with simple:
                self.assertEqual([item.text for item in simple.items], ["preserve this"])
                self.assertIn("comment_to_note", [change["code"] for change in report["changes"]])


class Layout(unittest.TestCase):
    def board(self):
        board = Board()
        for n in range(2):
            group = board.add_group(f"g{n}", x=500*n)
            for index in range(5):
                image = board.add_image(ASSET, name=f"{n}-{index}", x=100*index, parent_id=group)
                board.add_note("caption", y=90, parent_id=image)
        return board

    def test_seeded_shuffle_exact_restore(self):
        with self.board() as board:
            snapshot = capture_layout(board)
            report = shuffle_images(board, seed=17)
            self.assertGreater(report["moved"], 0)
            self.assertEqual(capture_layout(board)["content_sha256"], snapshot["content_sha256"])
            restore_layout(board, snapshot)
            self.assertEqual(capture_layout(board), snapshot)

    def test_content_and_cycles_rejected_atomically(self):
        with self.board() as board:
            snapshot = capture_layout(board)
            snapshot["items"][0]["parent"] = snapshot["items"][0]["id"]
            before = capture_layout(board)
            with self.assertRaises(ValueError):
                restore_layout(board, snapshot)
            self.assertEqual(capture_layout(board), before)
            snapshot = capture_layout(board)
            board.update(board.items[-1].id, text="changed")
            with self.assertRaises(ValueError):
                restore_layout(board, snapshot)

    def test_unknown_sort_order_fails_before_shuffle_mutation(self):
        with self.board() as board:
            board._conn.execute("UPDATE items SET sort_order='opaque' WHERE id=1")
            before = capture_layout(board)
            with self.assertRaises(ValueError):
                shuffle_images(board, seed=22)
            self.assertEqual(capture_layout(board), before)
