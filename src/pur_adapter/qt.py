"""The few Qt binary encodings PureRef stores in its SQLite tables.

PureRef serialises Qt values with QDataStream (big-endian) and stores the bytes
as Latin-1 text in SQLite. Every value starts with a QVariant header:

    uint32 type id | uint8 "is null" flag | payload

This module writes and reads only the encodings measured in native files, so
no Qt installation is needed.
"""

import struct
from .core import affine, multiply, inverse  # re-exported for older imports

# QVariant type ids used by PureRef.
QT_RECT_F = 20  # QRectF: x, y, width, height as doubles
QT_SIZE_F = 22  # QSizeF: width, height as doubles
QT_TRANSFORM = 80  # QTransform: nine doubles, row-vector order
QT_USER_TYPE = 1024  # custom type, followed by its C++ type name

_NOT_NULL = 0
_PAINTER_PATH_NAME = b"QPainterPath\0"
_BIG_RATIONAL_NAME = b"BigRational\0"

# QPainterPath element kinds.
MOVE_TO, LINE_TO = 0, 1

# Positions inside the nine-number row-vector matrix
# [m11, m12, m13, m21, m22, m23, m31, m32, m33].
SCALE_X, SHEAR_Y, PERSPECTIVE_X = 0, 1, 2
SHEAR_X, SCALE_Y, PERSPECTIVE_Y = 3, 4, 5
TRANSLATE_X, TRANSLATE_Y, PERSPECTIVE_W = 6, 7, 8


def binary(value):
    """Return the raw bytes behind a value read from the SQLite tables."""
    return value.encode("latin1") if isinstance(value, str) else bytes(value)


def _header(type_id):
    return struct.pack(">IB", type_id, _NOT_NULL)


def _user_type_header(type_name):
    return struct.pack(">IBI", QT_USER_TYPE, _NOT_NULL, len(type_name)) + type_name


# --- Transforms --------------------------------------------------------------------


def transform(values):
    """Encode nine matrix numbers as a QTransform value."""
    return (_header(QT_TRANSFORM) + struct.pack(">9d", *values)).decode("latin1")


def read_transform(value):
    """Decode a QTransform value into a list of nine numbers."""
    raw = binary(value)
    if len(raw) != 77 or raw[:5] != _header(QT_TRANSFORM):
        raise ValueError("Unsupported Qt transform encoding")
    return list(struct.unpack(">9d", raw[5:]))


def map_point(matrix, x, y):
    """Apply an affine row-vector matrix to one point."""
    return (
        x * matrix[SCALE_X] + y * matrix[SHEAR_X] + matrix[TRANSLATE_X],
        x * matrix[SHEAR_Y] + y * matrix[SCALE_Y] + matrix[TRANSLATE_Y],
    )


def has_perspective(matrix, tolerance=1e-12):
    return (
        abs(matrix[PERSPECTIVE_X]) > tolerance
        or abs(matrix[PERSPECTIVE_Y]) > tolerance
        or abs(matrix[PERSPECTIVE_W] - 1) > tolerance
    )


# --- Rectangles and sizes ----------------------------------------------------------


def rect(x, y, width, height):
    """Encode a QRectF value (used for the saved scene rectangle)."""
    return (_header(QT_RECT_F) + struct.pack(">4d", x, y, width, height)).decode(
        "latin1"
    )


def read_rect(value):
    raw = binary(value)
    if len(raw) != 37 or raw[:5] != _header(QT_RECT_F):
        raise ValueError("Unsupported Qt rectangle encoding")
    return struct.unpack(">4d", raw[5:])


def size(width, height):
    """Encode a QSizeF value. PureRef uses -1, -1 for an automatic note size."""
    return (_header(QT_SIZE_F) + struct.pack(">2d", width, height)).decode("latin1")


def read_size(value):
    raw = binary(value)
    if len(raw) != 21 or raw[:5] != _header(QT_SIZE_F):
        raise ValueError("Unrecognized note size encoding")
    return struct.unpack(">2d", raw[5:])


# --- Image bounds (a QPainterPath rectangle) ---------------------------------------


def image_bounds(width, height):
    """Bounds of an uncropped image, centred on the item origin."""
    return path_rect(-width / 2, -height / 2, width, height)


def path_rect(x, y, width, height):
    """Encode a rectangle as the five-point closed QPainterPath PureRef writes."""
    corners = [
        (x, y),
        (x + width, y),
        (x + width, y + height),
        (x, y + height),
        (x, y),
    ]
    raw = _user_type_header(_PAINTER_PATH_NAME) + struct.pack(">I", len(corners))
    for index, (corner_x, corner_y) in enumerate(corners):
        kind = MOVE_TO if index == 0 else LINE_TO
        raw += struct.pack(">Idd", kind, corner_x, corner_y)
    current_subpath_start, fill_rule = 0, 0
    raw += struct.pack(">II", current_subpath_start, fill_rule)
    return raw.decode("latin1")


def read_path_rect(value):
    """Return (x, y, width, height) when bounds are a plain rectangle, else None."""
    raw = binary(value)
    header = _user_type_header(_PAINTER_PATH_NAME)
    expected_length = len(header) + 4 + 5 * 20 + 8
    if len(raw) != expected_length or not raw.startswith(header):
        return None
    elements = [
        struct.unpack_from(">Idd", raw, len(header) + 4 + index * 20) for index in range(5)
    ]
    xs = sorted({x for _, x, _ in elements})
    ys = sorted({y for _, _, y in elements})
    if len(xs) != 2 or len(ys) != 2:
        return None
    candidate = path_rect(xs[0], ys[0], xs[1] - xs[0], ys[1] - ys[0])
    if binary(candidate) != raw:
        return None  # same corners but another winding or element layout
    return xs[0], ys[0], xs[1] - xs[0], ys[1] - ys[0]


# --- Sibling ordering (BigRational) ------------------------------------------------


def sort_order(index):
    """Encode position `index` (0-based) as the integer BigRational PureRef writes."""
    raw = _user_type_header(_BIG_RATIONAL_NAME)
    raw += struct.pack(">7I", 1, 0, index + 1, 1, 1, 0, 1) + struct.pack(">I", 1)
    return raw.decode("latin1")


def integer_order(value):
    """Read only the measured positive-integer form, preserving other forms."""
    raw = binary(value)
    if len(raw) == 53:
        rank = struct.unpack(">I", raw[-24:-20])[0]
        if rank > 0 and raw == binary(sort_order(rank - 1)):
            return rank
    raise ValueError("Ordering needs an unsupported BigRational encoding")
