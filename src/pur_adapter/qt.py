"""Small measured subset of Qt QVariant/QDataStream, without a Qt dependency."""

import math
import struct


def binary(value):
    return value.encode("latin1") if isinstance(value, str) else bytes(value)


def transform(values):
    return (struct.pack(">IB9d", 80, 0, *values)).decode("latin1")


def read_transform(value):
    raw = binary(value)
    if len(raw) != 77 or raw[:5] != struct.pack(">IB", 80, 0):
        raise ValueError("Unsupported Qt transform encoding")
    return list(struct.unpack(">9d", raw[5:]))


def affine(x=0, y=0, scale=1, rotation=0):
    a = math.radians(rotation)
    c, s = scale * math.cos(a), scale * math.sin(a)
    return [c, s, 0, -s, c, 0, x, y, 1]


def image_bounds(width, height):
    points = [
        (-width / 2, -height / 2),
        (width / 2, -height / 2),
        (width / 2, height / 2),
        (-width / 2, height / 2),
        (-width / 2, -height / 2),
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


def rect(x, y, width, height):
    return struct.pack(">IB4d", 20, 0, x, y, width, height).decode("latin1")


def multiply(left, right):
    """Compose row-vector transforms: local first, parent second."""
    return [
        sum(left[row * 3 + k] * right[k * 3 + column] for k in range(3))
        for row in range(3)
        for column in range(3)
    ]


def inverse(matrix):
    a, b, c, d, e, f, g, h, i = matrix
    cofactors = [
        e * i - f * h,
        c * h - b * i,
        b * f - c * e,
        f * g - d * i,
        a * i - c * g,
        c * d - a * f,
        d * h - e * g,
        b * g - a * h,
        a * e - b * d,
    ]
    determinant = a * cofactors[0] + b * cofactors[3] + c * cofactors[6]
    if abs(determinant) < 1e-12:
        raise ValueError("Singular transform")
    return [value / determinant for value in cofactors]
