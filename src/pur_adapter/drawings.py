"""Native pen drawings: read, write and turn them into positioned PNG images.

`items_drawings.strokes` holds a QVariant of `QList<GraphicsDrawItem::Stroke>`.
Each stroke is: a version marker (100), a QColor, the pen width, a
QPainterPath (typed points, current-subpath index, fill rule), one extra point
and a pen style. The renderer supports the measured solid, dashed and arrow
styles; unknown encodings raise UnsupportedFormat so nothing is silently lost.
"""

from dataclasses import dataclass
import colorsys
import hashlib
import io
import math
import struct
from PIL import Image, ImageDraw
from .container import InvalidPurFile, UnsupportedFormat
from . import qt
from .core import finite


# Pen styles measured in PureRef 2.1.3. Rectangles and ellipses drawn with the
# shape tools are stored as ordinary paths in one of these styles.
SOLID, DASHED, ARROW = 0, 1, 2
PEN_STYLES = (SOLID, DASHED, ARROW)

# QPainterPath element kinds. A cubic curve is CURVE_TO followed by two
# CURVE_DATA points (second control point, end point).
MOVE_TO, LINE_TO, CURVE_TO, CURVE_DATA = 0, 1, 2, 3
PATH_ELEMENT_KINDS = (MOVE_TO, LINE_TO, CURVE_TO, CURVE_DATA)

STROKE_LIST_TYPE = b"QList<GraphicsDrawItem::Stroke>\0"
STROKE_VERSION = 100  # written before each stroke by PureRef 2.x
MAX_STROKES = 10_000
MAX_PATH_ELEMENTS = 100_000

# QColor specifications (first byte of a serialised QColor).
QCOLOR_RGB, QCOLOR_HSV, QCOLOR_CMYK, QCOLOR_HSL = 1, 2, 3, 4
CHANNEL_MAX = 65535  # QColor stores 16-bit channels
HUE_SCALE = 36000  # hue in hundredths of a degree


@dataclass(frozen=True)
class Stroke:
    color: tuple[int, int, int, int]
    width: float
    elements: tuple[tuple[int, float, float], ...]
    fill_rule: int
    extra_point: tuple[float, float]
    style: int


class Reader:
    def __init__(self, data):
        self.data = memoryview(qt.binary(data))
        self.position = 0

    def take(self, count):
        if count < 0 or self.position + count > len(self.data):
            raise InvalidPurFile("Truncated drawing payload")
        value = self.data[self.position : self.position + count].tobytes()
        self.position += count
        return value

    def unpack(self, format):
        return struct.unpack(">" + format, self.take(struct.calcsize(">" + format)))


def encode_strokes(strokes):
    """Encode the measured version-100 RGB pen subset, validating before mutation."""
    strokes = list(strokes)
    if not 1 <= len(strokes) <= MAX_STROKES:
        raise ValueError("Provide between 1 and 10000 strokes")
    raw = bytearray(
        struct.pack(">IBI", qt.QT_USER_TYPE, 0, len(STROKE_LIST_TYPE))
        + STROKE_LIST_TYPE
        + struct.pack(">I", len(strokes))
    )
    for stroke in strokes:
        if len(stroke.color) != 4 or any(
            not isinstance(c, int) or isinstance(c, bool) or not 0 <= c <= 255
            for c in stroke.color
        ):
            raise ValueError("Stroke color must contain four 8-bit RGBA channels")
        finite(width=stroke.width)
        if (
            not 0 < stroke.width <= 10000
            or stroke.style not in PEN_STYLES
            or stroke.fill_rule not in (0, 1)
        ):
            raise ValueError("Unsupported stroke width, style or fill rule")
        if not 1 <= len(stroke.elements) <= MAX_PATH_ELEMENTS or len(stroke.extra_point) != 2:
            raise ValueError("Invalid stroke geometry")
        for kind, x, y in stroke.elements:
            if kind not in PATH_ELEMENT_KINDS:
                raise ValueError("Invalid painter path kind")
            finite(x=x, y=y)
        finite(x=stroke.extra_point[0], y=stroke.extra_point[1])
        flatten_path(stroke.elements, 1)  # rejects malformed curves early
        red, green, blue, alpha = stroke.color
        to_16_bit = 257  # 255 * 257 == 65535
        unused_channel = 0
        raw.extend(
            struct.pack(
                ">BB5HdI",
                STROKE_VERSION,
                QCOLOR_RGB,
                alpha * to_16_bit,
                red * to_16_bit,
                green * to_16_bit,
                blue * to_16_bit,
                unused_channel,
                stroke.width,
                len(stroke.elements),
            )
        )
        for element in stroke.elements:
            raw.extend(struct.pack(">Idd", *element))
        current_subpath_start = 0
        raw.extend(
            struct.pack(">II2di", current_subpath_start, stroke.fill_rule,
                        *stroke.extra_point, stroke.style)
        )
    return bytes(raw)


def read_color(reader):
    """Read a QColor and return (red, green, blue, alpha) in 0..255."""
    spec = reader.unpack("B")[0]
    alpha, c1, c2, c3, c4 = reader.unpack("5H")
    if spec == QCOLOR_RGB:
        channels = (c1 / CHANNEL_MAX, c2 / CHANNEL_MAX, c3 / CHANNEL_MAX)
    elif spec == QCOLOR_HSV:
        hue = (c1 % HUE_SCALE) / HUE_SCALE
        channels = colorsys.hsv_to_rgb(hue, c2 / CHANNEL_MAX, c3 / CHANNEL_MAX)
    elif spec == QCOLOR_HSL:
        hue = (c1 % HUE_SCALE) / HUE_SCALE
        channels = colorsys.hls_to_rgb(hue, c3 / CHANNEL_MAX, c2 / CHANNEL_MAX)
    elif spec == QCOLOR_CMYK:
        black = c4 / CHANNEL_MAX
        channels = tuple((1 - value / CHANNEL_MAX) * (1 - black) for value in (c1, c2, c3))
    else:
        raise UnsupportedFormat(f"Unsupported QColor specification: {spec}")
    return tuple(round(channel * 255) for channel in channels) + (round(alpha / 257),)


def parse_strokes(payload):
    """Decode `items_drawings.strokes` into a list of Stroke."""
    reader = Reader(payload)
    type_id, is_null, name_size = reader.unpack("IBI")
    if type_id != qt.QT_USER_TYPE or is_null or name_size > 200:
        raise UnsupportedFormat("Unsupported stroke QVariant")
    if reader.take(name_size) != STROKE_LIST_TYPE:
        raise UnsupportedFormat("Unknown drawing payload type")
    count = reader.unpack("I")[0]
    if count > MAX_STROKES:
        raise InvalidPurFile("Too many pen strokes")
    strokes = []
    for _ in range(count):
        version = reader.unpack("B")[0]
        if version < STROKE_VERSION:
            reader.position -= 1  # older strokes start directly with the colour
        elif version != STROKE_VERSION:
            raise UnsupportedFormat(f"Unsupported stroke version: {version}")
        color = read_color(reader)
        width = reader.unpack("d")[0]
        if not math.isfinite(width) or not 0 < width <= 10000:
            raise InvalidPurFile("Invalid pen width")
        point_count = reader.unpack("I")[0]
        if point_count > MAX_PATH_ELEMENTS:
            raise InvalidPurFile("Too many drawing elements")
        elements = tuple(reader.unpack("Idd") for _ in range(point_count))
        for kind, x, y in elements:
            if kind not in PATH_ELEMENT_KINDS or not math.isfinite(x) or not math.isfinite(y):
                raise InvalidPurFile("Invalid painter path element")
        current_start, fill_rule = reader.unpack("II")
        if fill_rule not in (0, 1) or current_start > point_count:
            raise InvalidPurFile("Invalid painter path metadata")
        extra = reader.unpack("dd")
        style = reader.unpack("i")[0] if version >= STROKE_VERSION else SOLID
        strokes.append(Stroke(color, width, elements, fill_rule, extra, style))
    if reader.position != len(reader.data):
        raise UnsupportedFormat("Unrecognized drawing trailing data")
    return strokes


def flatten_path(elements, scale):
    """Sample cubic curves into subpaths for Pillow's line renderer."""
    paths = []
    points = []
    index = 0
    while index < len(elements):
        kind, x, y = elements[index]
        if kind == MOVE_TO:
            if points:
                paths.append(points)
            points = [(x, y)]
        elif kind == LINE_TO:
            if not points:
                raise InvalidPurFile("Line without an initial move")
            points.append((x, y))
        elif kind == CURVE_TO:
            if not points or index + 2 >= len(elements):
                raise InvalidPurFile("Incomplete cubic curve")
            second, end = elements[index + 1 : index + 3]
            if second[0] != CURVE_DATA or end[0] != CURVE_DATA:
                raise InvalidPurFile("Invalid cubic control points")
            start, control_1, control_2, finish = points[-1], (x, y), second[1:], end[1:]
            hull = (start, control_1, control_2, finish)
            length = sum(math.dist(a, b) for a, b in zip(hull, hull[1:]))
            steps = max(4, min(4096, math.ceil(length * scale / 2)))  # ~2 px per step
            for step in range(1, steps + 1):
                t = step / steps
                u = 1 - t
                points.append(
                    tuple(
                        u**3 * start[axis]
                        + 3 * u * u * t * control_1[axis]
                        + 3 * u * t * t * control_2[axis]
                        + t**3 * finish[axis]
                        for axis in (0, 1)
                    )
                )
            index += 2
        else:
            raise InvalidPurFile("Unpaired cubic data element")
        index += 1
    if points:
        paths.append(points)
    return paths


def stroke_bounds(strokes):
    """(left, top, right, bottom) around all strokes, including pen width.

    Curve control points are included; they always enclose the curve.
    Arrow heads reach 4 pen widths past the path end.
    """
    boxes = []
    for stroke in strokes:
        if stroke.style == ARROW:
            radius = stroke.width * 4 + 1
        else:
            radius = stroke.width / 2 + 1
        boxes.extend((x - radius, y - radius, x + radius, y + radius) for _, x, y in stroke.elements)
    if not boxes:
        raise ValueError("Drawing has no visible strokes")
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def render_strokes(strokes, scale=4, max_pixels=16_000_000):
    if (
        not isinstance(scale, (int, float))
        or isinstance(scale, bool)
        or not math.isfinite(scale)
        or not 0 < scale <= 16
    ):
        raise ValueError("scale must be finite, greater than 0 and at most 16")
    if not strokes or not any(s.elements for s in strokes):
        raise ValueError("Drawing has no visible strokes")
    if any(s.style not in PEN_STYLES for s in strokes):
        raise UnsupportedFormat("Unrecognized pen style")
    min_x, min_y, max_x, max_y = stroke_bounds(strokes)
    left = math.floor(min_x)
    top = math.floor(min_y)
    width = math.ceil((max_x - left) * scale)
    height = math.ceil((max_y - top) * scale)
    if width * height > max_pixels:
        raise ValueError("Raster exceeds pixel budget")
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for stroke in strokes:
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        pen_width = max(1, round(stroke.width * scale))
        radius = pen_width / 2
        for path in flatten_path(stroke.elements, scale):
            points = [((x - left) * scale, (y - top) * scale) for x, y in path]
            if len(points) > 1:
                if stroke.style == DASHED:
                    draw_dashes(draw, points, stroke.color, pen_width)
                else:
                    draw.line(points, fill=stroke.color, width=pen_width, joint="curve")
                if stroke.style == ARROW:
                    draw_arrow(draw, points, stroke.color, pen_width)
            for x, y in (points[0], points[-1]):
                draw.ellipse(
                    (x - radius, y - radius, x + radius, y + radius), fill=stroke.color
                )
        image = Image.alpha_composite(image, layer)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return (
        output.getvalue(),
        (left, top, left + width / scale, top + height / scale),
        (width, height),
    )


def draw_dashes(draw, points, color, width):
    """Draw a dashed polyline: a dash of 3 pen widths, then a gap of 2.

    The dash pattern continues across corners, like Qt's DashLine.
    """
    dash_length = 3 * width
    cycle_length = 5 * width  # one dash plus one gap
    position_in_cycle = 0.0
    tiny = 1e-9

    def point_along(start, end, segment_length, distance):
        fraction = distance / segment_length
        return tuple(a + (b - a) * fraction for a, b in zip(start, end))

    for start, end in zip(points, points[1:]):
        segment_length = math.dist(start, end)
        travelled = 0.0
        while segment_length - travelled > tiny:
            in_dash = position_in_cycle < dash_length
            phase_end = dash_length if in_dash else cycle_length
            step = min(phase_end - position_in_cycle, segment_length - travelled)
            if in_dash:
                draw.line(
                    [
                        point_along(start, end, segment_length, travelled),
                        point_along(start, end, segment_length, travelled + step),
                    ],
                    fill=color,
                    width=width,
                )
            travelled += step
            if step >= phase_end - position_in_cycle:
                # Snap exactly onto the next dash/gap boundary. Accumulating
                # floating-point steps instead could stall just short of it.
                position_in_cycle = 0.0 if phase_end == cycle_length else dash_length
            else:
                position_in_cycle += step


def draw_arrow(draw, points, color, width):
    tip = points[-1]
    previous = next((point for point in reversed(points[:-1]) if point != tip), None)
    if previous is None:
        return
    length = math.dist(previous, tip)
    dx, dy = ((tip[i] - previous[i]) / length for i in (0, 1))
    back = (tip[0] - 4 * width * dx, tip[1] - 4 * width * dy)
    draw.polygon(
        [
            tip,
            (back[0] - 2 * width * dy, back[1] + 2 * width * dx),
            (back[0] + 2 * width * dy, back[1] - 2 * width * dx),
        ],
        fill=color,
    )


def rasterize_drawing(board, item_id, *, scale=4):
    """Replace one drawing in place. Item ID, parent, layer and opacity survive."""
    item = board.get(item_id)
    if item.kind != "drawing":
        raise ValueError("Expected a drawing item")
    payload = board._conn.execute(
        "SELECT strokes FROM items_drawings WHERE id=?", (item_id,)
    ).fetchone()[0]
    png, bounds, size = render_strokes(parse_strokes(payload), scale)
    left, top, right, bottom = bounds
    center = ((left + right) / 2, (top + bottom) / 2)
    old = qt.read_transform(
        board._conn.execute(
            "SELECT transform FROM items WHERE id=?", (item_id,)
        ).fetchone()[0]
    )
    new = qt.multiply(qt.affine(center[0], center[1], 1 / scale), old)
    correction = qt.multiply(old, qt.inverse(new))
    children = list(
        board._conn.execute("SELECT id,transform FROM items WHERE parent=?", (item_id,))
    )
    image_id = board._next_id("images")
    with board._conn:
        board._conn.execute(
            "INSERT INTO images (id,width,height,checksum,format,source,origin,data,source_type) VALUES (?,?,?,?,?,?,?, ?,1)",
            (
                image_id,
                *size,
                hashlib.md5(png).hexdigest(),
                "PNG",
                f"drawing-{item_id}.png",
                "rasterized drawing",
                png,
            ),
        )
        board._conn.execute("DELETE FROM items_drawings WHERE id=?", (item_id,))
        board._conn.execute(
            "INSERT INTO items_images (id,image,playback_speed,playback_frame,playback_state,flags,image_bounds,image_transform) VALUES (?,?,1,0,0,1,?,?)",
            (
                item_id,
                image_id,
                qt.image_bounds(*size),
                qt.transform(qt.affine(-size[0] / 2, -size[1] / 2)),
            ),
        )
        board._conn.execute(
            "UPDATE items SET transform=? WHERE id=?", (qt.transform(new), item_id)
        )
        for child in children:
            matrix = qt.multiply(qt.read_transform(child["transform"]), correction)
            board._conn.execute(
                "UPDATE items SET transform=? WHERE id=?",
                (qt.transform(matrix), child["id"]),
            )
    board._dirty = True
    return {
        "item_id": item_id,
        "image_id": image_id,
        "local_bounds": bounds,
        "pixel_size": size,
        "renderer": "Pillow measured pen paths",
        "scale": scale,
        "approximate_antialiasing": True,
    }
