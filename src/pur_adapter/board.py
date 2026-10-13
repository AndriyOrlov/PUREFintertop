"""Public local API. Loaded modern databases retain unedited native columns."""

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
import hashlib
import io
import math
import os
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


def finite(**values):
    for key, value in values.items():
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            raise ValueError(f"{key} must be a finite number")


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
            for key in ("name", "opacity", "locked", "z", "parent_id"):
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
            data = pack_container(self._conn, self.application_version, self._thumbnail)
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

        return {
            "schema_version": 1,
            "format": self.format,
            "application_version": self.application_version,
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
