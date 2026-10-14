"""Small measured subset of Qt QVariant/QDataStream, without a Qt dependency."""

import struct
from .core import affine, multiply, inverse


def binary(value):
    return value.encode("latin1") if isinstance(value, str) else bytes(value)


def transform(values):
    return (struct.pack(">IB9d", 80, 0, *values)).decode("latin1")


def read_transform(value):
    raw = binary(value)
    if len(raw) != 77 or raw[:5] != struct.pack(">IB", 80, 0):
        raise ValueError("Unsupported Qt transform encoding")
    return list(struct.unpack(">9d", raw[5:]))


def image_bounds(width, height):
    return path_rect(-width / 2, -height / 2, width, height)


def path_rect(x, y, width, height):
    points = [
        (x, y),
        (x + width, y),
        (x + width, y + height),
        (x, y + height),
        (x, y),
    ]
    raw = struct.pack(">IBI", 1024, 0, 13) + b"QPainterPath\0" + struct.pack(">I", 5)
    for i, (x, y) in enumerate(points):
        raw += struct.pack(">Idd", int(i > 0), x, y)
    raw += struct.pack(">II", 0, 0)
    return raw.decode("latin1")


def sort_order(index):
    raw = struct.pack(">IBI", 1024, 0, 12) + b"BigRational\0"
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


def rect(x, y, width, height):
    return struct.pack(">IB4d", 20, 0, x, y, width, height).decode("latin1")
