"""Convert between PureRef boards and native FrameRef boards (file_version 2).

A FrameRef board is a folder: `main.frameref` (JSON) plus `content/` with the
image files. Every item is a `type` and a list of named, typed properties.

Measured against the installed FrameRef: an item's origin is its top-left
corner, while PureRef images and notes are centred on their origin. Both use
Qt row-vector matrices, so a placement converts by prepending a translation.

Nothing is overwritten: each direction writes to a new path, and every
simplification is listed in the report (`changes`, each with a `code`).
"""

import io
import json
import math
import re
import tempfile
import uuid
from pathlib import Path

from PIL import Image, ImageDraw
from . import qt
from .board import Board
from .container import UnsupportedFormat
from .core import affine, finite, multiply
from .drawings import SOLID, Stroke, render_strokes
from .images import open_as_displayed
from .manifest import MAX_ITEMS, asset_path
from .render import _note_box, parse_color
from .scene import drawing_order, world_properties
from .text import html_from_plain, is_rich_text

FILE_VERSION = 2
KEYS = ("m11", "m12", "m13", "m21", "m22", "m23", "m31", "m32", "m33")
IDENTITY = affine()

# FrameRef connector sides: top, right, bottom, left. For each: the anchor
# point as a fraction of (width, height) and the outward direction.
CONNECTOR_SIDES = {
    0: ((0.5, 0.0), (0, -1)),
    1: ((1.0, 0.5), (1, 0)),
    2: ((0.5, 1.0), (0, 1)),
    3: ((0.0, 0.5), (-1, 0)),
}
SWATCH_SIZE = 320  # pixels of the image that replaces a colour swatch
DRAWING_SCALE = 4  # pixels per scene unit when a pen drawing becomes a PNG
DEFAULT_NOTE_TEXT = (255, 255, 255, 255)
DEFAULT_NOTE_BACKGROUND = (32, 32, 32, 255)


# --- Reading FrameRef values -------------------------------------------------------


def properties(item):
    """A FrameRef item's properties as a plain {name: value} dictionary."""
    return {entry["name"]: entry["value"] for entry in item["properties"]}


def matrix(value):
    """A FrameRef QTransform ({m11: ..., m33: ...}) as nine numbers."""
    values = [value[key] for key in KEYS]
    finite(**dict(zip(KEYS, values)))
    if qt.has_perspective(values, tolerance=0):
        raise UnsupportedFormat("Perspective transforms are not supported by this converter")
    return values


def color(value):
    """A FrameRef colour ({r, g, b, a} or '#AARRGGBB') as (r, g, b, a)."""
    if isinstance(value, str):
        rgba = parse_color(value, None)
        if rgba is None:
            raise ValueError("Invalid FrameRef colour")
        return rgba
    rgba = tuple(value[channel] for channel in ("r", "g", "b", "a"))
    if any(not isinstance(channel, int) or not 0 <= channel <= 255 for channel in rgba):
        raise ValueError("Invalid FrameRef colour")
    return rgba


def hex_color(value):
    red, green, blue, alpha = color(value)
    return f"#{alpha:02x}{red:02x}{green:02x}{blue:02x}"


def _scale(x_factor, y_factor):
    return [x_factor, 0, 0, 0, y_factor, 0, 0, 0, 1]


# --- Writing FrameRef values -------------------------------------------------------


def prop(name, kind, value):
    return dict(action=0, name=name, type=kind, value=value)


def native_item(kind, width, height, transform, *, opacity=1, locked=False,
                z=0, label="", extra=()):
    """A FrameRef item with every property FrameRef writes for a new item."""
    transparent = dict(r=0, g=0, b=0, a=0)
    return dict(type=kind, properties=[
        prop("uid", "QUuid", "{" + str(uuid.uuid4()) + "}"),
        prop("width", "float", width),
        prop("height", "float", height),
        prop("itemTransform", "QTransform", dict(zip(KEYS, transform))),
        prop("zDepth", "float", z),
        prop("itemOpacity", "float", opacity),
        prop("cropOffset", "QPointF", dict(x=0, y=0)),
        prop("antialiased", "bool", True),
        prop("sourceURL", "QString", ""),
        prop("label", "QString", label),
        prop("centerHtml", "QString", ""),
        prop("cornerRadius", "int", 0),
        prop("borderWidth", "int", 0),
        prop("borderColor", "QColor", transparent),
        prop("borderStyle", "Qt::PenStyle", 0),
        prop("backgroundColor", "QColor", transparent),
        prop("rating", "int", 0),
        prop("tags", "QStringList", []),
        prop("createdAt", "qlonglong", 0),
        prop("drawGrayscale", "bool", False),
        prop("normalizedCropRegion", "QRectF", dict(x=0, y=0, width=1, height=1)),
        prop("isCropped", "bool", False),
        prop("locked", "bool", locked),
        *extra,
    ])


def _set_property(entry, name, value):
    for item_property in entry["properties"]:
        if item_property["name"] == name:
            item_property["value"] = value


# --- Report ------------------------------------------------------------------------


def _note_change(report, item, code, message):
    report["changes"].append(dict(item=str(item), code=code, message=message))


# --- FrameRef -> PureRef -----------------------------------------------------------


def _load(path):
    document = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if document.get("file_version") != FILE_VERSION:
        raise UnsupportedFormat("Expected a FrameRef file_version 2 board")
    items = document["board"]["items"]
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise ValueError("Invalid or excessive FrameRef item list")
    return document, items


def _place(board, item_id, item_matrix, values):
    """Apply placement and the common FrameRef properties to a PureRef item."""
    board.update(
        item_id,
        x=item_matrix[qt.TRANSLATE_X],
        y=item_matrix[qt.TRANSLATE_Y],
        matrix=[item_matrix[qt.SCALE_X], item_matrix[qt.SHEAR_Y],
                item_matrix[qt.SHEAR_X], item_matrix[qt.SCALE_Y]],
        opacity=values.get("itemOpacity", 1),
        locked=values.get("locked", False),
        z=values.get("zDepth", 0),
        comment=values.get("sourceURL") or None,
    )


def _add_image_item(board, values, content_folder, name, frameref_matrix):
    """FrameRef image: the bitmap is stretched to width x height from the top-left."""
    local_zoom = values.get("localZoomPan")
    crop_offset = values.get("cropOffset", dict(x=0, y=0))
    if (local_zoom and matrix(local_zoom) != IDENTITY) or crop_offset != dict(x=0, y=0):
        raise UnsupportedFormat("Local image zoom/pan or cropOffset is not yet supported")
    path = asset_path(content_folder, values["filePath"])
    item_id = board.add_image(path, name=name)
    pixel_width, pixel_height = board.image_settings(item_id)["pixel_size"]
    stretch = _scale(values["width"] / pixel_width, values["height"] / pixel_height)
    # PureRef pixel p sits at (p - centre) in item space; FrameRef draws it at
    # p * stretch. Moving the centre back first makes the two placements equal.
    centre_to_corner = affine(pixel_width / 2, pixel_height / 2)
    pureref_matrix = multiply(centre_to_corner, multiply(stretch, frameref_matrix))
    options = dict(grayscale=values.get("drawGrayscale", False),
                   bilinear=values.get("antialiased", True))
    if values.get("isCropped"):
        region = values["normalizedCropRegion"]
        options["crop"] = (region["x"] * pixel_width, region["y"] * pixel_height,
                           region["width"] * pixel_width, region["height"] * pixel_height)
    board.configure_image(item_id, **options)
    return item_id, pureref_matrix


def _add_text_item(board, values, name, frameref_matrix, report, uid):
    width, height = values["width"], values["height"]
    item_id = board.add_note(values.get("html", values.get("centerHtml", "")), name=name)
    board.style_note(item_id, width=width, height=height,
                     background_color=hex_color(values.get("backgroundColor", "#00202020")))
    _note_change(report, uid, "text_layout",
                 "Text layout and box height can differ between the applications.")
    return item_id, multiply(affine(width / 2, height / 2), frameref_matrix)


def _add_swatch_item(board, values, frameref_matrix, report, uid):
    rgba = color(values["color"])
    picture = Image.new("RGBA", (SWATCH_SIZE, SWATCH_SIZE), rgba)
    if values.get("showCaption", True):
        ImageDraw.Draw(picture).text((12, SWATCH_SIZE - 30), hex_color(values["color"]),
                                     fill="white")
    payload = io.BytesIO()
    picture.save(payload, format="PNG")
    item_id = board.add_image_bytes(payload.getvalue(), "Colour swatch", file_suffix=".png")
    stretch = _scale(values["width"] / SWATCH_SIZE, values["height"] / SWATCH_SIZE)
    centre_to_corner = affine(SWATCH_SIZE / 2, SWATCH_SIZE / 2)
    _note_change(report, uid, "swatch_to_image", "Colour swatch converted to a static image.")
    return item_id, multiply(centre_to_corner, multiply(stretch, frameref_matrix))


def _connector_stroke(values, items_by_uid):
    """A cubic curve between two side anchors, leaving each item outwards."""
    anchors, directions = [], []
    for end in ("from", "to"):
        endpoint = items_by_uid.get(values[end + "ItemUid"])
        if endpoint is None:
            raise ValueError("FrameRef connection references a missing item")
        side = values[end + "Side"]
        if side not in CONNECTOR_SIDES:
            raise UnsupportedFormat(f"Unknown FrameRef connector side: {side}")
        (x_fraction, y_fraction), (out_x, out_y) = CONNECTOR_SIDES[side]
        endpoint_matrix = matrix(endpoint["itemTransform"])
        anchors.append(qt.map_point(endpoint_matrix, x_fraction * endpoint["width"],
                                    y_fraction * endpoint["height"]))
        world_x = out_x * endpoint_matrix[qt.SCALE_X] + out_y * endpoint_matrix[qt.SHEAR_X]
        world_y = out_x * endpoint_matrix[qt.SHEAR_Y] + out_y * endpoint_matrix[qt.SCALE_Y]
        length = math.hypot(world_x, world_y)
        if length == 0:
            raise ValueError("Degenerate connector endpoint transform")
        directions.append((world_x / length, world_y / length))
    if values.get("direction", 0) != 0:
        raise UnsupportedFormat("Directional FrameRef connectors are not yet supported")
    start, end = anchors
    # FrameRef's exact routing is not stored in file_version 2: approximate it
    # with a cubic Bezier whose handles point away from each item.
    handle = max(40, math.dist(start, end) * 0.4)
    controls = [(x + dx * handle, y + dy * handle)
                for (x, y), (dx, dy) in zip(anchors, directions)]
    elements = ((0, *start), (2, *controls[0]), (3, *controls[1]), (3, *end))
    return Stroke(color(values["color"]), 2, elements, 0, (0, 0), SOLID)


def frameref_to_pur(source, output):
    """Write a new .pur (plus `<name>.pur.conversion.json`) from main.frameref."""
    source, output = Path(source), Path(output)
    if output.exists():
        raise FileExistsError(output)
    report = dict(direction="FrameRef → PureRef", source=str(source), output=str(output),
                  changes=[])
    _, items = _load(source)
    items_by_uid = {}
    with Board() as board:
        for item in items:
            values = properties(item)
            uid, kind = values.get("uid", ""), item["type"]
            if uid in items_by_uid:
                raise ValueError("Duplicate FrameRef UID")
            items_by_uid[uid] = values
            if kind == "EdgeItem":
                continue  # connectors are added once every endpoint is known
            frameref_matrix = matrix(values.get("itemTransform", dict(zip(KEYS, IDENTITY))))
            finite(width=values["width"], height=values["height"])
            if values["width"] <= 0 or values["height"] <= 0:
                raise ValueError("FrameRef item size must be positive")
            name = values.get("label") or ""  # unnamed stays unnamed in PureRef
            if kind == "ImageItem":
                item_id, pureref_matrix = _add_image_item(
                    board, values, source.parent / "content", name, frameref_matrix)
            elif kind in ("TextItem", "NoteItem"):
                item_id, pureref_matrix = _add_text_item(
                    board, values, name, frameref_matrix, report, uid)
            elif kind == "ColorSwatchItem":
                item_id, pureref_matrix = _add_swatch_item(
                    board, values, frameref_matrix, report, uid)
            else:
                raise UnsupportedFormat(f"FrameRef {kind} is not supported; no output was written")
            _place(board, item_id, pureref_matrix, values)
            has_border = values.get("borderWidth") and values.get("borderStyle")
            if values.get("tags") or values.get("rating") or has_border or values.get("centerHtml"):
                _note_change(report, uid, "frameref_extras_lost",
                             "FrameRef tags, rating, borders or overlays are not represented in PureRef.")
        for item in items:
            if item["type"] != "EdgeItem":
                continue
            values = properties(item)
            item_id = board.add_drawing([_connector_stroke(values, items_by_uid)],
                                        name="FrameRef connection")
            _place(board, item_id, IDENTITY, values)
            _note_change(report, values.get("uid", ""), "connector_to_curve",
                         "Connection converted to a static curve with side anchors; "
                         "routing is approximate and live bindings are lost.")
        report["items"] = len(board.items)
        _save_pur_with_report(board, output, report)
    return report


def _save_pur_with_report(board, output, report):
    """Publish the board and its report together; leave nothing half-written."""
    report_path = output.with_name(output.name + ".conversion.json")
    if report_path.exists():
        raise FileExistsError(report_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".conversion-", dir=output.parent) as temp:
        staged_board = Path(temp) / output.name
        board.save(staged_board)
        staged_report = Path(temp) / report_path.name
        staged_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        staged_report.rename(report_path)
        try:
            staged_board.rename(output)
        except BaseException:
            report_path.unlink()  # only the report we created a moment ago
            raise


# --- PureRef -> FrameRef -----------------------------------------------------------


def _image_entry(board, item, content, report):
    """Copy (or bake) one PureRef image; return its FrameRef geometry and extras."""
    settings = board.image_settings(item.id)
    asset = board._row("images", item.image_id)
    if not settings["embedded"]:
        raise UnsupportedFormat("Embed linked PureRef images before conversion")
    payload = bytes(asset["data"])
    width, height = settings["pixel_size"]
    bitmap_to_item = qt.read_transform(board._row("items_images", item.id)["image_transform"])
    matrix(dict(zip(KEYS, bitmap_to_item)))
    extension = (asset["format"] or "png").lower()
    if extension == "jpeg":
        extension = "jpg"
    if settings["cropped"] or settings["frame"]:
        if settings["crop"] is None:
            raise UnsupportedFormat("Non-rectangular crops are not supported")
        left, top, width, height = settings["crop"]
        if any(value != round(value) for value in (left, top, width, height)):
            raise UnsupportedFormat("Fractional-pixel crops are not supported")
        with open_as_displayed(payload) as picture:
            picture.seek(settings["frame"] or 0)
            picture = picture.convert("RGBA").crop(
                (int(left), int(top), int(left + width), int(top + height)))
            baked = io.BytesIO()
            picture.save(baked, format="PNG")
            payload = baked.getvalue()
        bitmap_to_item = multiply(affine(left, top), bitmap_to_item)
        extension = "png"
        _note_change(report, item.id, "crop_baked", "Crop or animation frame baked into a static PNG.")
    if settings["speed"] != 1 or settings["playing"]:
        _note_change(report, item.id, "animation_state_lost",
                     "Animation playback state/speed is not transferred.")
    filename = f"image-{item.id}.{extension}"
    (content / filename).write_bytes(payload)
    extra = [prop("filePath", "QString", filename),
             prop("downloadUrl", "QString", ""),
             prop("localZoomPan", "QTransform", dict(zip(KEYS, IDENTITY)))]
    return "ImageItem", width, height, bitmap_to_item, extra, settings


def _with_text_color(html, color_hex):
    """Add a text colour without breaking a full HTML document.

    PureRef stores notes as complete documents whose <body> carries the font
    size; wrapping such a document in a <div> loses that style.
    """
    body = re.search(r"<body([^>]*)>", html, re.IGNORECASE)
    if body is None:
        return f'<div style="color:{color_hex}">{html}</div>'
    attributes = body.group(1)
    style = re.search(r'style\s*=\s*"([^"]*)"', attributes, re.IGNORECASE)
    if style:
        styled = attributes.replace(style.group(0),
                                    f'style="{style.group(1).rstrip()} color:{color_hex};"')
    else:
        styled = attributes + f' style="color:{color_hex};"'
    return html[: body.start(1)] + styled + html[body.end(1):]


def _note_entry(board, item, report):
    left, top, width, height = _note_box(board, item)
    text = item.text or ""
    if not is_rich_text(text):
        text = html_from_plain(text)
    style = board.note_style(item.id)
    red, green, blue, _ = parse_color(style["text_color"], DEFAULT_NOTE_TEXT)
    text = _with_text_color(text, f"#{red:02x}{green:02x}{blue:02x}")
    background = parse_color(style["background_color"], DEFAULT_NOTE_BACKGROUND)
    extra = [prop("html", "QString", text),
             prop("clipText", "bool", False),
             prop("textAlignment", "QFlags<Qt::AlignmentFlag>", 0),
             prop("backgroundColor", "QColor", dict(zip(("r", "g", "b", "a"), background)))]
    _note_change(report, item.id, "text_layout",
                 "Text layout and automatic note size can differ between the applications.")
    return "TextItem", width, height, affine(left, top), extra


def _drawing_entry(board, item, content, report):
    png, (left, top, _, _), (width, height) = render_strokes(
        board.drawing_strokes(item.id), DRAWING_SCALE)
    filename = f"drawing-{item.id}.png"
    (content / filename).write_bytes(png)
    pixel_to_item = affine(left, top, 1 / DRAWING_SCALE)
    extra = [prop("filePath", "QString", filename),
             prop("localZoomPan", "QTransform", dict(zip(KEYS, IDENTITY)))]
    _note_change(report, item.id, "drawing_rasterized",
                 "Pen drawing rasterized to PNG; vector editing is lost.")
    return "ImageItem", width, height, pixel_to_item, extra


def _board_centre(entries):
    corners = []
    for entry in entries:
        values = properties(entry)
        entry_matrix = matrix(values["itemTransform"])
        corners.extend(qt.map_point(entry_matrix, x, y) for x, y in
                       ((0, 0), (values["width"], 0), (0, values["height"]),
                        (values["width"], values["height"])))
    if not corners:
        return [0, 0]
    return [(min(point[axis] for point in corners) + max(point[axis] for point in corners)) / 2
            for axis in (0, 1)]


def pur_to_frameref(source, output):
    """Write a new FrameRef board folder (main.frameref + content/) from a .pur."""
    source, output = Path(source), Path(output)
    if output.exists():
        raise FileExistsError(output)
    report = dict(direction="PureRef → FrameRef", source=str(source), output=str(output),
                  changes=[])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".conversion-", dir=output.parent) as temp:
        folder = Path(temp) / "board"
        content = folder / "content"
        content.mkdir(parents=True)
        entries = []
        with Board.load(source) as board:
            world = world_properties(board)
            for stacking_rank, item_id in enumerate(drawing_order(board)):
                item = board.get(item_id)
                item_to_world, opacity = world[item_id]
                matrix(dict(zip(KEYS, item_to_world)))  # refuses perspective
                settings = None
                if item.kind == "group":
                    _note_change(report, item.id, "group_flattened",
                                 "Group hierarchy flattened; children keep their world positions.")
                    continue
                if item.kind == "image":
                    kind, width, height, to_item, extra, settings = _image_entry(
                        board, item, content, report)
                elif item.kind == "note":
                    kind, width, height, to_item, extra = _note_entry(board, item, report)
                elif item.kind == "drawing":
                    kind, width, height, to_item, extra = _drawing_entry(
                        board, item, content, report)
                else:
                    raise UnsupportedFormat(f"Unsupported PureRef item kind: {item.kind}")
                entry = native_item(kind, width, height, multiply(to_item, item_to_world),
                                    opacity=opacity, locked=item.locked, z=stacking_rank,
                                    label=item.name, extra=extra)
                if settings is not None:
                    _set_property(entry, "drawGrayscale", settings["grayscale"])
                    _set_property(entry, "antialiased", settings["bilinear"])
                if item.comment:
                    _note_change(report, item.id, "comment_lost", "PureRef comment is not transferred.")
                entries.append(entry)
        centre = _board_centre(entries)
        document = dict(file_version=FILE_VERSION, board=dict(
            items=entries,
            items_center=dict(zip(("x", "y"), centre)),
            viewport=dict(m11=1, m12=0, m21=0, m22=1, dx=0, dy=0, x=0, y=0, name="viewport"),
        ))
        (folder / "main.frameref").write_text(
            json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        report["items"] = len(entries)
        (folder / "conversion.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        folder.rename(output)
    return report


def convert(source, output):
    """Convert in the direction the source file implies."""
    source = Path(source)
    if source.suffix.lower() == ".pur":
        return pur_to_frameref(source, output)
    if source.suffix.lower() == ".frameref":
        return frameref_to_pur(source, output)
    raise ValueError("Choose a .pur or .frameref file")
