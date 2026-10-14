"""Scene rendering, thumbnails and the saved scene rectangle.

Expected rectangles come from native PureRef 2.1.3 saves (docs/NATIVE_WINDOWS.md).
"""

import io
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from pur_adapter import Board, qt
from pur_adapter.render import render_scene, render_thumbnail, scene_rect

ROOT = Path(__file__).resolve().parents[1]


class Rendering(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.square = self.folder / "square.png"
        Image.new("RGB", (100, 100), (200, 0, 0)).save(self.square)

    def tearDown(self):
        self.temp.cleanup()

    def test_scene_rectangles_match_native_pureref(self):
        with Board() as board:
            board.add_image(self.square)
            self.assertEqual(scene_rect(board), (-50, -50, 100, 100))
        with Board() as board:
            group = board.add_group("G")
            board.add_image(self.square, parent_id=group)
            self.assertEqual(scene_rect(board), (-60, -60, 120, 120))
        with Board() as board:
            board.add_note("Hello")
            self.assertEqual(scene_rect(board), (-42.5, -31, 85, 62))
        with Board() as board:
            note = board.add_note("Hi")
            board.style_note(note, width=300, height=80)
            self.assertEqual(scene_rect(board), (-150, -40, 300, 80))

    def test_render_draws_images_where_they_are(self):
        with Board() as board:
            board.add_image(self.square, x=0)
            note = board.add_note("Hello", x=300)
            board.style_note(note, background_color="#ff00ff00")
            picture = render_scene(board, width=400)
        self.assertEqual(picture.getpixel((20, picture.height // 2))[:3], (200, 0, 0))
        self.assertEqual(picture.getpixel((picture.width - 5, picture.height // 2))[:3], (0, 255, 0))

    def test_flip_crop_and_grayscale_are_drawn(self):
        quad = Image.new("RGB", (100, 100), (0, 0, 255))
        quad.paste((255, 0, 0), (0, 0, 50, 100))  # left half red
        quad.save(self.folder / "halves.png")
        with Board() as board:
            item = board.add_image(self.folder / "halves.png")
            board.configure_image(item, flip_horizontal=True)
            flipped = render_scene(board, width=100)
            self.assertEqual(flipped.getpixel((75, 50))[:3], (255, 0, 0))
            board.configure_image(item, crop=(0, 0, 50, 100), grayscale=True)
            cropped = render_scene(board, width=50)
            red, green, blue, _ = cropped.getpixel((25, 50))
            self.assertTrue(red == green == blue)

    def test_thumbnail_is_a_square_jpeg(self):
        with Board.load(ROOT / "fixtures/native-drawing-2.1.3.pur") as board:
            thumbnail = render_thumbnail(board)
        with Image.open(io.BytesIO(thumbnail)) as image:
            self.assertEqual((image.format, image.size), ("JPEG", (256, 256)))

    def test_saving_an_edited_board_refreshes_its_preview(self):
        with Board() as board:
            empty_thumbnail = board._thumbnail
            board.add_image(self.square)
            board.save(self.folder / "board.pur")
        with Board.load(self.folder / "board.pur") as board:
            self.assertNotEqual(board._thumbnail, empty_thumbnail)
            stored = board._conn.execute("SELECT thumbnail, scene_rect FROM metadata").fetchone()
            self.assertEqual(bytes(stored[0]), board._thumbnail)
            self.assertEqual(qt.read_rect(stored[1]), (-50, -50, 100, 100))

    def test_preview_can_be_left_alone(self):
        with Board() as board:
            original = board._thumbnail
            board.add_image(self.square)
            board.save(self.folder / "board.pur", refresh_preview=False)
        with Board.load(self.folder / "board.pur") as board:
            self.assertEqual(board._thumbnail, original)


if __name__ == "__main__":
    unittest.main()
