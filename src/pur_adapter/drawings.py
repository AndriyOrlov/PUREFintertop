"""Decode native pen strokes and replace them with positioned PNG assets.

The renderer supports measured solid, dashed and arrow paths. Unknown encodings
raise UnsupportedFormat, so compatibility export cannot silently erase them.
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
    if not 1 <= len(strokes) <= 10000:
        raise ValueError("Provide between 1 and 10000 strokes")
    name = b"QList<GraphicsDrawItem::Stroke>\0"
    raw = bytearray(struct.pack(">IBI", 1024, 0, len(name)) + name + struct.pack(">I", len(strokes)))
    for stroke in strokes:
        if len(stroke.color) != 4 or any(not isinstance(c, int) or isinstance(c, bool) or not 0 <= c <= 255 for c in stroke.color):
            raise ValueError("Stroke color must contain four 8-bit RGBA channels")
        finite(width=stroke.width)
        if not 0 < stroke.width <= 10000 or stroke.style not in (0, 1, 2) or stroke.fill_rule not in (0, 1):
            raise ValueError("Unsupported stroke width, style or fill rule")
        if not 1 <= len(stroke.elements) <= 100000 or len(stroke.extra_point) != 2:
            raise ValueError("Invalid stroke geometry")
        for kind, x, y in stroke.elements:
            if kind not in (0, 1, 2, 3):
                raise ValueError("Invalid painter path kind")
            finite(x=x, y=y)
        finite(x=stroke.extra_point[0], y=stroke.extra_point[1])
        flatten_path(stroke.elements, 1)
        red, green, blue, alpha = stroke.color
        raw.extend(struct.pack(">BB5HdI", 100, 1, alpha * 257, red * 257, green * 257, blue * 257, 0,
                               stroke.width, len(stroke.elements)))
        for element in stroke.elements:
            raw.extend(struct.pack(">Idd", *element))
        raw.extend(struct.pack(">II2di", 0, stroke.fill_rule, *stroke.extra_point, stroke.style))
    return bytes(raw)


def read_color(reader):
    spec = reader.unpack("B")[0]
    alpha, c1, c2, c3, c4 = reader.unpack("5H")
    if spec == 1:
        channels = (c1 / 65535, c2 / 65535, c3 / 65535)
    elif spec == 2:
        channels = colorsys.hsv_to_rgb((c1 % 36000) / 36000, c2 / 65535, c3 / 65535)
    elif spec == 4:
        channels = colorsys.hls_to_rgb((c1 % 36000) / 36000, c3 / 65535, c2 / 65535)
    elif spec == 3:
        channels = tuple(
            (1 - value / 65535) * (1 - c4 / 65535) for value in (c1, c2, c3)
        )
    else:
        raise UnsupportedFormat(f"Unsupported QColor specification: {spec}")
    return tuple(round(channel * 255) for channel in channels) + (round(alpha / 257),)


def parse_strokes(payload):
    reader = Reader(payload)
    kind, null, name_size = reader.unpack("IBI")
    if kind != 1024 or null or name_size > 200:
        raise UnsupportedFormat("Unsupported stroke QVariant")
    name = reader.take(name_size)
    if name != b"QList<GraphicsDrawItem::Stroke>\0":
        raise UnsupportedFormat("Unknown drawing payload type")
    count = reader.unpack("I")[0]
    if count > 10000:
        raise InvalidPurFile("Too many pen strokes")
    strokes = []
    for _ in range(count):
        version = reader.unpack("B")[0]
        if version < 100:
            reader.position -= 1
        elif version != 100:
            raise UnsupportedFormat(f"Unsupported stroke version: {version}")
        color = read_color(reader)
        width = reader.unpack("d")[0]
        if not math.isfinite(width) or not 0 < width <= 10000:
            raise InvalidPurFile("Invalid pen width")
        point_count = reader.unpack("I")[0]
        if point_count > 100000:
            raise InvalidPurFile("Too many drawing elements")
        elements = tuple(reader.unpack("Idd") for _ in range(point_count))
        for kind, x, y in elements:
            if kind not in (0, 1, 2, 3) or not math.isfinite(x) or not math.isfinite(y):
                raise InvalidPurFile("Invalid painter path element")
        current_start, fill_rule = reader.unpack("II")
        if fill_rule not in (0, 1) or current_start > point_count:
            raise InvalidPurFile("Invalid painter path metadata")
        extra = reader.unpack("dd")
        style = reader.unpack("i")[0] if version >= 100 else 0
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
        if kind == 0:
            if points:
                paths.append(points)
            points = [(x, y)]
        elif kind == 1:
            if not points:
                raise InvalidPurFile("Line without an initial move")
            points.append((x, y))
        elif kind == 2:
            if not points or index + 2 >= len(elements):
                raise InvalidPurFile("Incomplete cubic curve")
            second, end = elements[index + 1 : index + 3]
            if second[0] != 3 or end[0] != 3:
                raise InvalidPurFile("Invalid cubic control points")
            p0 = points[-1]
            p1 = (x, y)
            p2 = second[1:]
            p3 = end[1:]
            length = sum(math.dist(a, b) for a, b in zip((p0, p1, p2), (p1, p2, p3)))
            steps = max(4, min(4096, math.ceil(length * scale / 2)))
            for step in range(1, steps + 1):
                t = step / steps
                u = 1 - t
                points.append(
                    tuple(
                        u**3 * p0[axis]
                        + 3 * u * u * t * p1[axis]
                        + 3 * u * t * t * p2[axis]
                        + t**3 * p3[axis]
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
    if any(s.style not in (0, 1, 2) for s in strokes):
        raise UnsupportedFormat("Unrecognized pen style")
    bounds = []
    for stroke in strokes:
        radius = stroke.width * 4 + 1 if stroke.style == 2 else stroke.width / 2 + 1
        bounds.extend(
            (x - radius, y - radius, x + radius, y + radius)
            for _, x, y in stroke.elements
        )
    left = math.floor(min(b[0] for b in bounds))
    top = math.floor(min(b[1] for b in bounds))
    width = math.ceil((max(b[2] for b in bounds) - left) * scale)
    height = math.ceil((max(b[3] for b in bounds) - top) * scale)
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
                if stroke.style == 1:
                    draw_dashes(draw, points, stroke.color, pen_width)
                else:
                    draw.line(points, fill=stroke.color, width=pen_width, joint="curve")
                if stroke.style == 2:
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
    on, period, phase = 3 * width, 5 * width, 0.0
    for start, end in zip(points, points[1:]):
        length = math.dist(start, end)
        if length == 0:
            continue
        offset = 0.0
        while offset < length:
            position = phase % period
            visible = position < on
            remaining = (on if visible else period) - position
            step = min(remaining, length - offset)
            if step < 1e-9:
                phase += 1e-8
                continue
            if visible:
                segment = [tuple(a + (b - a) * t / length for a, b in zip(start, end))
                           for t in (offset, offset + step)]
                draw.line(segment, fill=color, width=width)
            offset += step
            phase += step


def draw_arrow(draw, points, color, width):
    tip = points[-1]
    previous = next((point for point in reversed(points[:-1]) if point != tip), None)
    if previous is None:
        return
    length = math.dist(previous, tip)
    dx, dy = ((tip[i] - previous[i]) / length for i in (0, 1))
    back = (tip[0] - 4 * width * dx, tip[1] - 4 * width * dy)
    draw.polygon([tip, (back[0] - 2 * width * dy, back[1] + 2 * width * dx),
                  (back[0] + 2 * width * dy, back[1] - 2 * width * dx)], fill=color)


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
