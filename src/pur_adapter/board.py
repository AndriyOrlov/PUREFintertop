"""Public local API. Loaded modern databases retain unedited native columns."""

from importlib.resources import files
from pathlib import Path
import hashlib
import io
import math
import os
import re
import sqlite3
import struct
import tempfile
from PIL import Image
from .container import (
    InvalidPurFile,
    UnsupportedFormat,
    pack_container,
    unpack_container,
)
from . import qt
from .core import Item, finite


def atomic_write(path, data, overwrite=False):
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temp, path)
        else:
            os.link(
                temp, path
            )  # atomic exclusive publication; prevents overwrite races
            os.unlink(temp)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class Board:
    def __init__(self):
        self._init(files("pur_adapter").joinpath("data/empty.pur").read_bytes())
        self._raw = None
        self.format = "modern"
        self._legacy = None

    def _init(self, data):
        self._conn, self.application_version, self._thumbnail = unpack_container(data)
        self._conn.row_factory = sqlite3.Row
        names = {
            r[0]
            for r in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        required = {
            "items",
            "images",
            "items_images",
            "items_notes",
            "items_groups",
            "items_drawings",
            "metadata",
        }
        if not required <= names:
            raise UnsupportedFormat("Missing native scene tables")
        expected_columns = {
            "items": {
                "id",
                "name",
                "parent",
                "transform",
                "opacity",
                "locked",
                "z",
                "sort_order",
                "comment",
            },
            "images": {
                "id",
                "width",
                "height",
                "format",
                "source",
                "origin",
                "checksum",
                "data",
                "source_type",
            },
            "items_images": {
                "id",
                "image",
                "image_bounds",
                "image_transform",
                "flags",
                "playback_frame",
                "playback_speed",
                "playback_state",
            },
            "items_notes": {
                "id",
                "text",
                "fixed_size",
                "background_color",
                "text_color",
                "style",
            },
            "items_groups": {"id", "lock_mode", "background_color"},
            "items_drawings": {"id", "strokes"},
            "metadata": {"id", "thumbnail"},
        }
        for table, required_columns in expected_columns.items():
            columns = {
                row[1] for row in self._conn.execute(f'PRAGMA table_info("{table}")')
            }
            if not required_columns <= columns:
                self._conn.close()
                raise UnsupportedFormat(f"Unsupported native schema in {table}")
        self._raw = data
        self._dirty = False

    @classmethod
    def load(cls, path, *, max_bytes=256 * 1024 * 1024):
        path = Path(path)
        if path.stat().st_size > max_bytes:
            raise InvalidPurFile("File exceeds configured input limit")
        data = path.read_bytes()
        if data[:4] == b"\0\0\0\x08":
            from .legacy import load_legacy

            return load_legacy(path, data)
        if data[:4] != b"\0\0\0\x06":
            raise UnsupportedFormat("Unrecognized PureRef format")
        obj = cls.__new__(cls)
        obj._init(data)
        obj.format = "modern"
        obj._legacy = None
        return obj

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @property
    def items(self):
        return [
            self.get(row[0])
            for row in self._conn.execute("SELECT id FROM items ORDER BY id")
        ]

    def get(self, item_id):
        if not isinstance(item_id, int) or isinstance(item_id, bool):
            raise ValueError("item_id must be an integer")
        row = self._conn.execute(
            "SELECT * FROM items WHERE id=?", (item_id,)
        ).fetchone()
        if row is None:
            raise KeyError(item_id)
        kind = "unknown"
        text = None
        image_id = None
        for k in ("images", "notes", "groups", "drawings"):
            special = self._conn.execute(
                f"SELECT * FROM items_{k} WHERE id=?", (item_id,)
            ).fetchone()
            if special is not None:
                kind = {
                    "images": "image",
                    "notes": "note",
                    "groups": "group",
                    "drawings": "drawing",
                }[k]
                if k == "images":
                    image_id = special["image"]
                if k == "notes":
                    text = special["text"]
                break
        m = qt.read_transform(row["transform"])
        return Item(
            row["id"],
            kind,
            row["name"] or "",
            m[6],
            m[7],
            tuple([m[0], m[1], m[3], m[4]]),
            None if row["parent"] == -1 else row["parent"],
            row["opacity"],
            bool(row["locked"]),
            row["z"],
            text,
            image_id,
            row["comment"],
        )

    def _next_id(self, table="items"):
        return self._conn.execute(
            f"SELECT COALESCE(MAX(id),-1)+1 FROM {table}"
        ).fetchone()[0]

    def _add_item(self, name, x, y, parent_id=None):
        finite(x=x, y=y)
        if not isinstance(name, str):
            raise ValueError("name must be text")
        if parent_id is not None:
            self.get(parent_id)
        item_id = self._next_id()
        self._conn.execute(
            "INSERT INTO items (comment,opacity,z,locked,parent,sort_order,transform,name,id) VALUES (NULL,1,?,0,?,?,?,?,?)",
            (
                float(item_id + 1),
                -1 if parent_id is None else parent_id,
                qt.sort_order(item_id),
                qt.transform(qt.affine(x, y)),
                name,
                item_id,
            ),
        )
        self._dirty = True
        return item_id

    def add_image(self, path, *, x=0, y=0, name=None, parent_id=None):
        path = Path(path)
        data = path.read_bytes()
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            fmt = image.format
        checksum = hashlib.md5(data).hexdigest()
        row = self._conn.execute(
            "SELECT id FROM images WHERE checksum=? AND data=?", (checksum, data)
        ).fetchone()
        image_id = row[0] if row else self._next_id("images")
        with self._conn:
            item_id = self._add_item(
                path.stem if name is None else name, x, y, parent_id
            )
            if not row:
                self._conn.execute(
                    "INSERT INTO images (width,checksum,format,source,height,data,source_type,origin,id) VALUES (?,?,?,?,?,?,1,?,?)",
                    (
                        width,
                        checksum,
                        fmt,
                        str(path.resolve()),
                        height,
                        data,
                        str(path.resolve()),
                        image_id,
                    ),
                )
            self._conn.execute(
                "INSERT INTO items_images (playback_speed,playback_frame,playback_state,image_bounds,image_transform,flags,image,id) VALUES (1,0,0,?,?,1,?,?)",
                (
                    qt.image_bounds(width, height),
                    qt.transform(qt.affine(-width / 2, -height / 2)),
                    image_id,
                    item_id,
                ),
            )
        return item_id

    def add_note(self, text, *, x=0, y=0, name="Note", parent_id=None):
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        with self._conn:
            item_id = self._add_item(name, x, y, parent_id)
            fixed = struct.pack(">IB2d", 22, 0, -1, -1).decode("latin1")
            self._conn.execute(
                "INSERT INTO items_notes (fixed_size,background_color,text_color,style,text,id) VALUES (?,'',NULL,0,?,?)",
                (fixed, text, item_id),
            )
        return item_id

    def update(self, item_id, **properties):
        item = self.get(item_id)
        allowed = {
            "x",
            "y",
            "scale",
            "rotation",
            "matrix",
            "name",
            "text",
            "opacity",
            "locked",
            "z",
            "parent_id",
            "comment",
        }
        if set(properties) - allowed:
            raise ValueError(
                "Unknown properties: " + ", ".join(sorted(set(properties) - allowed))
            )
        numeric = {
            k: v
            for k, v in properties.items()
            if k in {"x", "y", "scale", "rotation", "opacity", "z"}
        }
        finite(**numeric)
        if "scale" in properties and properties["scale"] <= 0:
            raise ValueError("scale must be positive")
        if "opacity" in properties and not 0 <= properties["opacity"] <= 1:
            raise ValueError("opacity must be 0..1")
        for k in ("name", "text"):
            if k in properties and not isinstance(properties[k], str):
                raise ValueError(f"{k} must be text")
        if "text" in properties and item.kind != "note":
            raise ValueError("text is only supported for notes")
        if "comment" in properties and properties["comment"] is not None and not isinstance(properties["comment"], str):
            raise ValueError("comment must be text or None")
        if "locked" in properties and not isinstance(properties["locked"], bool):
            raise ValueError("locked must be boolean")
        if "parent_id" in properties:
            parent = properties["parent_id"]
            seen = {item_id}
            while parent is not None:
                if parent in seen:
                    raise ValueError("Parent cycle")
                seen.add(parent)
                parent = self.get(parent).parent_id
        row = self._conn.execute(
            "SELECT transform FROM items WHERE id=?", (item_id,)
        ).fetchone()
        m = qt.read_transform(row[0])
        if "matrix" in properties:
            if "scale" in properties or "rotation" in properties:
                raise ValueError("Use matrix or scale/rotation")
            if not isinstance(properties["matrix"], (list, tuple)):
                raise ValueError("matrix must be a list or tuple of four numbers")
            a = list(properties["matrix"])
            if len(a) != 4:
                raise ValueError("matrix must contain four numbers")
            finite(**{str(i): v for i, v in enumerate(a)})
            m[0], m[1], m[3], m[4] = a
        elif "scale" in properties or "rotation" in properties:
            # Do not flatten perspective, reflection or shear implicitly.
            if m[2] != 0 or m[5] != 0 or m[8] != 1 or m[0] * m[4] - m[1] * m[3] <= 0:
                raise ValueError("Use matrix for a reflected or perspective transform")
            if not math.isclose(
                m[0] * m[3] + m[1] * m[4], 0, abs_tol=1e-8
            ) or not math.isclose(math.hypot(m[0], m[1]), math.hypot(m[3], m[4])):
                raise ValueError("Use matrix for a sheared or nonuniform transform")
            a = qt.affine(
                scale=properties.get("scale", math.hypot(m[0], m[1])),
                rotation=properties.get(
                    "rotation", math.degrees(math.atan2(m[1], m[0]))
                ),
            )
            m[0], m[1], m[3], m[4] = a[0], a[1], a[3], a[4]
        m[6] = properties.get("x", m[6])
        m[7] = properties.get("y", m[7])
        with self._conn:
            self._conn.execute(
                "UPDATE items SET transform=? WHERE id=?", (qt.transform(m), item_id)
            )
            for key in ("name", "opacity", "locked", "z", "parent_id", "comment"):
                if key in properties:
                    column = "parent" if key == "parent_id" else key
                    value = properties[key]
                    if key == "parent_id" and value is None:
                        value = -1
                    self._conn.execute(
                        f"UPDATE items SET {column}=? WHERE id=?", (value, item_id)
                    )
            if "text" in properties:
                self._conn.execute(
                    "UPDATE items_notes SET text=? WHERE id=?",
                    (properties["text"], item_id),
                )
        self._dirty = True
        return self.get(item_id)

    @staticmethod
    def _color(value):
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{8}", value):
            raise ValueError("color must be #AARRGGBB")
        return value.lower()

    def add_group(self, name="Group", *, x=0, y=0, parent_id=None,
                  color="#ff202020", locked_mode=True):
        color = self._color(color)
        if not isinstance(locked_mode, bool):
            raise ValueError("locked_mode must be boolean")
        with self._conn:
            item_id = self._add_item(name, x, y, parent_id)
            self._conn.execute(
                "INSERT INTO items_groups (id,lock_mode,background_color) VALUES (?,?,?)",
                (item_id, locked_mode, color),
            )
        return item_id

    def add_drawing(self, strokes, *, x=0, y=0, name="Drawing", parent_id=None):
        from .drawings import encode_strokes
        payload = encode_strokes(strokes)
        with self._conn:
            item_id = self._add_item(name, x, y, parent_id)
            self._conn.execute(
                "INSERT INTO items_drawings (id,strokes) VALUES (?,?)",
                (item_id, payload.decode("latin1")),
            )
        return item_id

    def drawing_strokes(self, item_id):
        from .drawings import parse_strokes
        if self.get(item_id).kind != "drawing":
            raise ValueError("Expected a drawing item")
        row = self._conn.execute("SELECT strokes FROM items_drawings WHERE id=?", (item_id,)).fetchone()
        return parse_strokes(row[0])

    def style_note(self, item_id, *, background_color="#ff202020",
                   text_color="#ffffffff", width=-1, height=-1):
        if self.get(item_id).kind != "note":
            raise ValueError("Expected a note item")
        background_color = self._color(background_color)
        text_color = self._color(text_color)
        finite(width=width, height=height)
        if any(value != -1 and value <= 0 for value in (width, height)):
            raise ValueError("Note dimensions must be positive or -1 for automatic")
        fixed = struct.pack(">IB2d", 22, 0, width, height).decode("latin1")
        with self._conn:
            self._conn.execute(
                "UPDATE items_notes SET fixed_size=?,background_color=?,text_color=? WHERE id=?",
                (fixed, background_color, text_color, item_id),
            )
        self._dirty = True

    def note_style(self, item_id):
        if self.get(item_id).kind != "note":
            raise ValueError("Expected a note item")
        row = self._conn.execute("SELECT * FROM items_notes WHERE id=?", (item_id,)).fetchone()
        raw = qt.binary(row["fixed_size"])
        if len(raw) != 21 or raw[:5] != struct.pack(">IB", 22, 0):
            raise UnsupportedFormat("Unrecognized note size encoding")
        width, height = struct.unpack(">2d", raw[5:])
        return dict(width=width, height=height, background_color=row["background_color"],
                    text_color=row["text_color"], style=row["style"])

    def ordered_children(self, parent_id=None):
        rows = self._conn.execute("SELECT id,sort_order FROM items WHERE parent=?",
                                  (-1 if parent_id is None else parent_id,)).fetchall()
        return [row["id"] for row in sorted(rows, key=lambda row: qt.integer_order(row["sort_order"]))]

    def reorder(self, item_ids):
        ids = list(item_ids)
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("Provide each sibling exactly once")
        parent = self.get(ids[0]).parent_id
        if any(self.get(item_id).parent_id != parent for item_id in ids):
            raise ValueError("Ordering cannot mix parents")
        siblings = {row[0] for row in self._conn.execute("SELECT id FROM items WHERE parent=?",
                                                        (-1 if parent is None else parent,))}
        if set(ids) != siblings:
            raise ValueError("Provide each sibling exactly once")
        with self._conn:
            self._conn.executemany("UPDATE items SET sort_order=? WHERE id=?",
                                   [(qt.sort_order(index), item_id) for index, item_id in enumerate(ids)])
        self._dirty = True

    def image_options(self, item_id):
        if self.get(item_id).kind != "image":
            raise ValueError("Expected an image item")
        row = dict(self._conn.execute("SELECT * FROM items_images WHERE id=?", (item_id,)).fetchone())
        row["image_transform"] = qt.read_transform(row["image_transform"])
        row["image_bounds"] = qt.binary(row["image_bounds"]).hex()
        return row

    def configure_image(self, item_id, *, crop=None, flip_horizontal=None,
                        flip_vertical=None, grayscale=None, bilinear=None,
                        frame=None, speed=None):
        item = self.get(item_id)
        if item.kind != "image":
            raise ValueError("Expected an image item")
        asset = self._conn.execute("SELECT * FROM images WHERE id=?", (item.image_id,)).fetchone()
        row = self._conn.execute("SELECT * FROM items_images WHERE id=?", (item_id,)).fetchone()
        updates = {}
        if crop is not None:
            if not isinstance(crop, (tuple, list)) or len(crop) != 4:
                raise ValueError("crop must contain x, y, width, height")
            x, y, width, height = crop
            finite(x=x, y=y, width=width, height=height)
            if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > asset["width"] or y + height > asset["height"]:
                raise ValueError("Crop must fit inside the bitmap")
            updates["image_bounds"] = qt.path_rect(x - asset["width"] / 2, y - asset["height"] / 2, width, height)
        matrix = qt.read_transform(row["image_transform"])
        if flip_horizontal is not None or flip_vertical is not None:
            if any(abs(matrix[i]) > 1e-12 for i in (1, 2, 3, 5)) or matrix[8] != 1 or matrix[0] == 0 or matrix[4] == 0:
                raise UnsupportedFormat("Flip requires an axis-aligned image transform")
            for value, axis, indices in ((flip_horizontal, 0, (0, 3, 6)), (flip_vertical, 4, (1, 4, 7))):
                if value is not None:
                    if not isinstance(value, bool):
                        raise ValueError("flip must be boolean")
                    if (matrix[axis] < 0) != value:
                        for index in indices:
                            matrix[index] *= -1
            updates["image_transform"] = qt.transform(matrix)
        flags = row["flags"]
        for value, bit in ((grayscale, 2), (bilinear, 1)):
            if value is not None:
                if not isinstance(value, bool):
                    raise ValueError("image flags must be boolean")
                flags = flags | bit if value else flags & ~bit
        if grayscale is not None or bilinear is not None:
            updates["flags"] = flags
        if frame is not None:
            if not isinstance(frame, int) or isinstance(frame, bool) or frame < 0:
                raise ValueError("frame must be a nonnegative integer")
            if asset["data"] is None:
                raise UnsupportedFormat("Frame selection requires an embedded image")
            with Image.open(io.BytesIO(asset["data"])) as image:
                if frame >= getattr(image, "n_frames", 1):
                    raise ValueError("frame is outside the animation")
            updates["playback_frame"] = frame
        if speed is not None:
            finite(speed=speed)
            if speed <= 0:
                raise ValueError("speed must be positive")
            updates["playback_speed"] = speed
        if updates:
            columns = ",".join(f"{key}=?" for key in updates)
            with self._conn:
                self._conn.execute(f"UPDATE items_images SET {columns} WHERE id=?", (*updates.values(), item_id))
            self._dirty = True

    def duplicate(self, item_id, **properties):
        self.get(item_id)
        new_id = self._next_id()
        with self._conn:
            for table in (
                "items",
                "items_images",
                "items_notes",
                "items_groups",
                "items_drawings",
            ):
                row = self._conn.execute(
                    f"SELECT * FROM {table} WHERE id=?", (item_id,)
                ).fetchone()
                if row is None:
                    continue
                values = dict(row)
                values["id"] = new_id
                if table == "items":
                    values["sort_order"] = qt.sort_order(new_id)
                    values["z"] = float(new_id + 1)
                columns = ",".join('"' + k + '"' for k in values)
                marks = ",".join("?" for _ in values)
                self._conn.execute(
                    f"INSERT INTO {table} ({columns}) VALUES ({marks})",
                    tuple(values.values()),
                )
            if properties:
                self.update(new_id, **properties)
        self._dirty = True
        return new_id

    def remove(self, item_id):
        self.get(item_id)
        ids = [
            r[0]
            for r in self._conn.execute(
                "WITH RECURSIVE branch(id) AS (SELECT id FROM items WHERE id=? UNION SELECT i.id FROM items i JOIN branch b ON i.parent=b.id) SELECT id FROM branch",
                (item_id,),
            )
        ]
        with self._conn:
            for key in ids:
                for table in (
                    "items_images",
                    "items_notes",
                    "items_groups",
                    "items_drawings",
                    "items",
                ):
                    self._conn.execute(f"DELETE FROM {table} WHERE id=?", (key,))
            self._conn.execute(
                "DELETE FROM images WHERE id NOT IN (SELECT image FROM items_images)"
            )
        self._dirty = True

    def save(self, path, *, overwrite=False, format=None):
        target = format or self.format
        if target not in ("modern", "legacy"):
            raise ValueError("format must be modern or legacy")
        if target == self.format and self._raw is not None and not self._dirty:
            data = self._raw
        elif target == "legacy":
            from .legacy import dump_legacy

            data = dump_legacy(self)
        else:
            data = pack_container(self._conn, self.application_version if self.format == "modern" else None, self._thumbnail)
        atomic_write(path, data, overwrite)
        return Path(path)

    def export_assets(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        result = []
        for row in self._conn.execute("SELECT id,format,data FROM images ORDER BY id"):
            if row["data"] is None:
                continue  # linked data is not implicitly read from disk
            suffix = (row["format"] or "bin").lower()
            if not suffix.isalnum():
                suffix = "bin"
            path = directory / f'image-{row["id"]}.{suffix}'
            atomic_write(path, bytes(row["data"]))
            result.append(str(path))
        return result

    def to_dict(self):
        from dataclasses import asdict
        from .profiles import compatibility_status

        return {
            "schema_version": 1,
            "format": self.format,
            "application_version": self.application_version,
            "compatibility": compatibility_status(self),
            "items": [asdict(item) for item in self.items],
            "images": [
                {
                    k: r[k]
                    for k in (
                        "id",
                        "width",
                        "height",
                        "format",
                        "source",
                        "origin",
                        "checksum",
                    )
                }
                | {"embedded": r["data"] is not None}
                for r in self._conn.execute("SELECT * FROM images ORDER BY id")
            ],
        }
