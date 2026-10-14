"""Version-independent scene model and row-vector geometry.

Storage encodings belong to codecs, never to this module.
"""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Item:
    id: int
    kind: str
    name: str
    x: float
    y: float
    matrix: tuple
    parent_id: int | None
    opacity: float
    locked: bool
    z: float
    text: str | None = None
    image_id: int | None = None
    comment: str | None = None


def finite(**values):
    for key, value in values.items():
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            raise ValueError(f"{key} must be a finite number")


def affine(x=0, y=0, scale=1, rotation=0):
    a = math.radians(rotation)
    c, s = scale * math.cos(a), scale * math.sin(a)
    return [c, s, 0, -s, c, 0, x, y, 1]


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
