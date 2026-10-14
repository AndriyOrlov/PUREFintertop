"""Draw a whole board into one picture: previews, thumbnails, host import checks.

This is an approximation of PureRef's own drawing, good enough for thumbnails
and visual checks: images (with crop, flip, grayscale, animation frame and
opacity), group backgrounds, note boxes with plain text, and pen drawings.
Text layout and antialiasing differ from Qt; perspective is drawn as affine.

Measured on native PureRef 2.1.3 thumbnails: 256x256 JPEG, background #181818,
the whole scene scaled to fit and centred.
"""

import io
import math
from PIL import Image, ImageDraw, ImageFont, ImageOps
from . import qt
from .core import affine, inverse, multiply
from .images import open_as_displayed
from .scene import drawing_order, world_properties
from .text import is_rich_text, plain_text

THUMBNAIL_SIZE = 256
CANVAS_BACKGROUND = (24, 24, 24, 255)
# Measured with native PureRef 2.1.3 (saved scene rectangles):
GROUP_PADDING = 10  # a group frame reaches 10 units past its children
NOTE_EMPTY_WIDTH = 37  # an automatic-size note is about 37 + 9.6 per character
NOTE_CHARACTER_WIDTH = 9.6
NOTE_EMPTY_HEIGHT = 42  # and 62 units tall for one line
NOTE_LINE_HEIGHT = 20
NOTE_PADDING = 8
DEFAULT_NOTE_BACKGROUND = (32, 32, 32, 255)
DEFAULT_TEXT_COLOR = (255, 255, 255, 255)


def parse_color(value, default):
    """'#AARRGGBB' -> (r, g, b, a); empty or invalid values give `default`."""
    if not value or len(value) != 9 or not value.startswith("#"):
        return default
    try:
        alpha, red, green, blue = (int(value[i : i + 2], 16) for i in range(1, 9, 2))
    except ValueError:
        return default
    return red, green, blue, alpha


# --- Local shapes of each item -------------------------------------------------------


def note_lines(text):
    """Lines a note shows. PureRef reads note text as HTML, so a bare newline
    in plain text is shown as a space; only <br> and paragraphs break lines."""
    if not is_rich_text(text):
        return [" ".join(text.split())]
    return plain_text(text).splitlines() or [""]


def _note_box(board, item):
    """Local rectangle (x, y, width, height) of a note, centred on its origin."""
    style = board.note_style(item.id)
    lines = note_lines(item.text or "")
    width = style["width"]
    if width <= 0:  # automatic width: one line per paragraph
        width = NOTE_EMPTY_WIDTH + NOTE_CHARACTER_WIDTH * max(len(line) for line in lines)
        line_count = len(lines)
    else:  # fixed width: text wraps
        characters_per_line = max(1, int((width - NOTE_EMPTY_WIDTH) / NOTE_CHARACTER_WIDTH))
        line_count = sum(max(1, math.ceil(len(line) / characters_per_line)) for line in lines)
    # A fixed height is only a minimum: PureRef grows the note to fit its text.
    height = max(style["height"], NOTE_EMPTY_HEIGHT + NOTE_LINE_HEIGHT * line_count)
    return -width / 2, -height / 2, width, height


def _image_box(board, item):
    """Local rectangle of the visible (cropped) image."""
    bounds = board._row("items_images", item.id)["image_bounds"]
    rect = qt.read_path_rect(bounds)
    if rect is not None:
        return rect
    asset = board._row("images", item.image_id)
    return -asset["width"] / 2, -asset["height"] / 2, asset["width"], asset["height"]


def _drawing_box(board, item):
    from .drawings import stroke_bounds

    left, top, right, bottom = stroke_bounds(board.drawing_strokes(item.id))
    return left, top, right - left, bottom - top


def _world_corners(rect, matrix):
    x, y, width, height = rect
    return [qt.map_point(matrix, px, py)
            for px, py in ((x, y), (x + width, y), (x + width, y + height), (x, y + height))]


def item_world_bounds(board, world=None):
    """{item_id: (left, top, right, bottom)} in scene coordinates.

    A group covers all its descendants plus a margin, like PureRef's frame.
    """
    world = world or world_properties(board)
    items = board.items
    kinds = {item.id: item.kind for item in items}
    children = {}
    for item in items:
        children.setdefault(item.parent_id, []).append(item.id)
    own_boxes = {}
    for item in items:
        shape = {"image": _image_box, "note": _note_box, "drawing": _drawing_box}.get(item.kind)
        if shape is None:
            continue
        corners = _world_corners(shape(board, item), world[item.id][0])
        xs, ys = [p[0] for p in corners], [p[1] for p in corners]
        own_boxes[item.id] = (min(xs), min(ys), max(xs), max(ys))

    bounds = dict(own_boxes)

    def covering_box(item_id, depth=0):
        """Box around an item and everything below it."""
        if depth > 256:
            raise ValueError("Invalid or excessive parent hierarchy")
        boxes = [own_boxes[item_id]] if item_id in own_boxes else []
        for child_id in children.get(item_id, []):
            child_box = covering_box(child_id, depth + 1)
            if child_box is not None:
                boxes.append(child_box)
        if not boxes:
            return None
        box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
               max(b[2] for b in boxes), max(b[3] for b in boxes))
        if kinds[item_id] == "group":
            box = (box[0] - GROUP_PADDING, box[1] - GROUP_PADDING,
                   box[2] + GROUP_PADDING, box[3] + GROUP_PADDING)
            bounds[item_id] = box
        return box

    for item in items:
        if item.kind == "group":
            covering_box(item.id)
    return bounds


def scene_rect(board):
    """(x, y, width, height) around everything on the board, or None if empty."""
    boxes = list(item_world_bounds(board).values())
    if not boxes:
        return None
    left = min(b[0] for b in boxes)
    top = min(b[1] for b in boxes)
    return left, top, max(b[2] for b in boxes) - left, max(b[3] for b in boxes) - top


# --- Drawing ------------------------------------------------------------------------


def _canvas_box(corners, canvas):
    """Integer box covering `corners`, clipped to the canvas; None if outside."""
    left = max(0, math.floor(min(x for x, _ in corners)))
    top = max(0, math.floor(min(y for _, y in corners)))
    right = min(canvas.width, math.ceil(max(x for x, _ in corners)))
    bottom = min(canvas.height, math.ceil(max(y for _, y in corners)))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _paste_transformed(canvas, picture, picture_to_canvas, opacity=1.0):
    """Draw `picture` on `canvas` through an affine row-vector matrix."""
    corners = _world_corners((0, 0, picture.width, picture.height), picture_to_canvas)
    box = _canvas_box(corners, canvas)
    if box is None:
        return
    # Work only inside the picture's own area of the canvas.
    picture_to_canvas = multiply(picture_to_canvas, affine(-box[0], -box[1]))
    area = (box[2] - box[0], box[3] - box[1])
    scale = math.sqrt(abs(picture_to_canvas[0] * picture_to_canvas[4]
                          - picture_to_canvas[1] * picture_to_canvas[3]))
    if scale < 0.5:
        # Shrink first: much faster and avoids aliasing on thumbnails.
        factor = max(1, int(1 / scale))
        factor = min(factor, picture.width, picture.height)
        if factor > 1:
            picture = picture.reduce(factor)
            picture_to_canvas = multiply(affine(scale=factor), picture_to_canvas)
    canvas_to_picture = inverse(picture_to_canvas)
    coefficients = (
        canvas_to_picture[qt.SCALE_X], canvas_to_picture[qt.SHEAR_X], canvas_to_picture[qt.TRANSLATE_X],
        canvas_to_picture[qt.SHEAR_Y], canvas_to_picture[qt.SCALE_Y], canvas_to_picture[qt.TRANSLATE_Y],
    )
    layer = picture.transform(area, Image.Transform.AFFINE, coefficients,
                              resample=Image.Resampling.BILINEAR)
    if opacity < 1:
        alpha = layer.getchannel("A").point(lambda value: round(value * opacity))
        layer.putalpha(alpha)
    canvas.alpha_composite(layer, dest=box[:2])


def _draw_image(board, item, canvas, item_to_canvas, opacity):
    asset = board._row("images", item.image_id)
    if asset is None or asset["data"] is None:
        return  # linked file that is not embedded: nothing to draw
    settings = board.image_settings(item.id)
    options = board._row("items_images", item.id)
    with open_as_displayed(bytes(asset["data"])) as image:
        frames = getattr(image, "n_frames", 1)
        if frames > 1:
            image.seek(min(max(0, settings["frame"] or 0), frames - 1))
        picture = image.convert("RGBA")
    if settings["grayscale"]:
        alpha = picture.getchannel("A")
        picture = ImageOps.grayscale(picture).convert("RGBA")
        picture.putalpha(alpha)
    bitmap_to_item = qt.read_transform(options["image_transform"])
    crop = settings["crop"]
    if crop is not None:
        left, top, width, height = (round(value) for value in crop)
        picture = picture.crop((left, top, left + width, top + height))
        bitmap_to_item = multiply(affine(left, top), bitmap_to_item)
    _paste_transformed(canvas, picture, multiply(bitmap_to_item, item_to_canvas), opacity)


def _font(size):
    try:
        return ImageFont.load_default(size=max(1, size))
    except TypeError:  # Pillow without scalable default font
        return ImageFont.load_default()


def _draw_box_with_text(canvas, corners, fill, text, text_color, scale, radius=0):
    area = _canvas_box(corners, canvas)
    if area is None:
        return
    xs, ys = [p[0] - area[0] for p in corners], [p[1] - area[1] for p in corners]
    box = (min(xs), min(ys), max(xs), max(ys))
    layer = Image.new("RGBA", (area[2] - area[0], area[3] - area[1]), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    if fill[3]:
        draw.rounded_rectangle(box, radius=radius * scale, fill=fill)
    if text and scale * NOTE_LINE_HEIGHT >= 4:
        font = _font(round(14 * scale))
        draw.multiline_text(
            (box[0] + NOTE_PADDING * scale, box[1] + NOTE_PADDING * scale),
            text, fill=text_color, font=font, spacing=round(4 * scale),
        )
    canvas.alpha_composite(layer, dest=area[:2])


def _draw_note(board, item, canvas, item_to_canvas, opacity, scale):
    style = board.note_style(item.id)
    fill = parse_color(style["background_color"], DEFAULT_NOTE_BACKGROUND)
    text_color = parse_color(style["text_color"], DEFAULT_TEXT_COLOR)
    fill = fill[:3] + (round(fill[3] * opacity),)
    text_color = text_color[:3] + (round(text_color[3] * opacity),)
    corners = _world_corners(_note_box(board, item), item_to_canvas)
    text = "\n".join(note_lines(item.text or ""))
    _draw_box_with_text(canvas, corners, fill, text, text_color, scale)


def _draw_drawing(board, item, canvas, item_to_canvas, opacity, scale):
    from .drawings import render_strokes

    pixels_per_unit = min(16, max(0.25, scale))
    try:
        png, (left, top, _, _), _ = render_strokes(
            board.drawing_strokes(item.id), pixels_per_unit, max_pixels=64_000_000
        )
    except ValueError:
        return  # empty drawing or over the pixel budget: skip in a preview
    with Image.open(io.BytesIO(png)) as image:
        picture = image.convert("RGBA")
    pixel_to_item = affine(left, top, 1 / pixels_per_unit)
    _paste_transformed(canvas, picture, multiply(pixel_to_item, item_to_canvas), opacity)


def render_scene(board, *, width=1024, height=None, background=CANVAS_BACKGROUND):
    """Render the board, scaled to fit `width` x `height` (default: keep aspect)."""
    world = world_properties(board)
    bounds = item_world_bounds(board, world)
    if not bounds:
        return Image.new("RGBA", (width, height or width), background)
    left = min(b[0] for b in bounds.values())
    top = min(b[1] for b in bounds.values())
    right = max(b[2] for b in bounds.values())
    bottom = max(b[3] for b in bounds.values())
    scene_width, scene_height = max(right - left, 1e-6), max(bottom - top, 1e-6)
    if height is None:
        height = max(1, round(width * scene_height / scene_width))
    scale = min(width / scene_width, height / scene_height)
    offset_x = (width - scene_width * scale) / 2 - left * scale
    offset_y = (height - scene_height * scale) / 2 - top * scale
    scene_to_canvas = [scale, 0, 0, 0, scale, 0, offset_x, offset_y, 1]
    canvas = Image.new("RGBA", (width, height), background)
    for item_id in drawing_order(board):
        item = board.get(item_id)
        item_to_scene, opacity = world[item_id]
        item_to_canvas = multiply(item_to_scene, scene_to_canvas)
        if item.kind == "image":
            _draw_image(board, item, canvas, item_to_canvas, opacity)
        elif item.kind == "note":
            _draw_note(board, item, canvas, item_to_canvas, opacity, scale)
        elif item.kind == "drawing":
            _draw_drawing(board, item, canvas, item_to_canvas, opacity, scale)
        elif item.kind == "group" and item_id in bounds:
            color = parse_color(board.group_style(item_id)["background_color"], (0, 0, 0, 0))
            box = bounds[item_id]
            corners = [(box[0], box[1]), (box[2], box[3])]
            corners = [qt.map_point(scene_to_canvas, x, y) for x, y in corners]
            fill = color[:3] + (round(color[3] * opacity),)
            _draw_box_with_text(canvas, corners, fill, "", fill, scale, radius=8)
    return canvas


def render_thumbnail(board):
    """JPEG bytes of a 256x256 thumbnail in the style PureRef writes."""
    picture = render_scene(board, width=THUMBNAIL_SIZE, height=THUMBNAIL_SIZE)
    output = io.BytesIO()
    picture.convert("RGB").save(output, format="JPEG", quality=85)
    return output.getvalue()
