"""Reversible layout operations that guard against restoring onto changed content."""

import base64
import hashlib
import json
import random
from . import qt
from .core import finite

SCENE_TABLES = (
    "images",
    "items",
    "items_images",
    "items_notes",
    "items_groups",
    "items_drawings",
)
LAYOUT_COLUMNS = {"parent", "transform", "z", "sort_order"}


def content_hash(board):
    tables = {}
    for table in SCENE_TABLES:
        rows = []
        for row in board._conn.execute(f"SELECT * FROM {table} ORDER BY id"):
            value = {}
            for key in row.keys():
                if table == "items" and key in LAYOUT_COLUMNS:
                    continue
                field = row[key]
                if isinstance(field, bytes):
                    field = {"bytes": base64.b64encode(field).decode("ascii")}
                value[key] = field
            rows.append(value)
        tables[table] = rows
    encoded = json.dumps(
        tables, sort_keys=True, ensure_ascii=True, allow_nan=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def capture_layout(board):
    return {
        "schema_version": 1,
        "content_sha256": content_hash(board),
        "items": [
            dict(
                id=row["id"],
                parent=row["parent"],
                z=row["z"],
                transform=base64.b64encode(qt.binary(row["transform"])).decode("ascii"),
                sort_order=base64.b64encode(qt.binary(row["sort_order"])).decode(
                    "ascii"
                ),
            )
            for row in board._conn.execute("SELECT * FROM items ORDER BY id")
        ],
    }


def restore_layout(board, snapshot):
    if snapshot.get("schema_version") != 1 or snapshot.get(
        "content_sha256"
    ) != content_hash(board):
        raise ValueError("Layout snapshot does not match board content")
    current = {item.id for item in board.items}
    rows = snapshot.get("items", [])
    ids = [row["id"] for row in rows]
    if (
        any(
            not isinstance(item_id, int) or isinstance(item_id, bool) for item_id in ids
        )
        or len(ids) != len(set(ids))
        or set(ids) != current
    ):
        raise ValueError("Snapshot must contain each current item exactly once")
    parents = {}
    prepared = []
    for row in rows:
        parent = row["parent"]
        if (
            not isinstance(parent, int)
            or isinstance(parent, bool)
            or parent != -1
            and parent not in current
        ):
            raise ValueError("Unknown snapshot parent")
        finite(z=row["z"])
        matrix = base64.b64decode(row["transform"], validate=True)
        finite(**{str(i): value for i, value in enumerate(qt.read_transform(matrix))})
        order = base64.b64decode(row["sort_order"], validate=True)
        if not 20 <= len(order) <= 4096:
            raise ValueError("Invalid ordering payload size")
        parents[row["id"]] = parent
        prepared.append(
            (
                parent,
                matrix.decode("latin1"),
                row["z"],
                order.decode("latin1"),
                row["id"],
            )
        )
    for item_id in parents:
        seen = set()
        while item_id != -1:
            if item_id in seen:
                raise ValueError("Snapshot parent cycle")
            seen.add(item_id)
            item_id = parents[item_id]
    with board._conn:
        board._conn.executemany(
            "UPDATE items SET parent=?,transform=?,z=?,sort_order=? WHERE id=?",
            prepared,
        )
    board._dirty = True


def shuffle_images(board, *, seed):
    images = [item for item in board.items if item.kind == "image"]
    parents = {item.parent_id for item in images}
    for parent in parents:
        if parent is not None and board.get(parent).kind != "group":
            raise ValueError("Shuffle supports root images and direct group children")
        board.ordered_children(
            parent
        )  # Validate opaque ordering before changing any position.
    slots = [(item.x, item.y, item.parent_id, item.z) for item in images]
    random.Random(seed).shuffle(slots)
    moved, prepared = 0, []
    for item, slot in zip(images, slots):
        x, y, parent, z = slot
        matrix = qt.read_transform(
            board._conn.execute(
                "SELECT transform FROM items WHERE id=?", (item.id,)
            ).fetchone()[0]
        )
        matrix[6:8] = [x, y]
        moved += (item.x, item.y, item.parent_id, item.z) != slot
        prepared.append(
            (qt.transform(matrix), -1 if parent is None else parent, z, item.id)
        )
    with board._conn:
        board._conn.executemany(
            "UPDATE items SET transform=?,parent=?,z=? WHERE id=?", prepared
        )
        for parent in sorted(parents, key=lambda value: -1 if value is None else value):
            ids = [
                row[0]
                for row in board._conn.execute(
                    "SELECT id FROM items WHERE parent=? ORDER BY id",
                    (-1 if parent is None else parent,),
                )
            ]
            random.Random(seed + (parent or 0)).shuffle(ids)
            board._conn.executemany(
                "UPDATE items SET sort_order=? WHERE id=?",
                [(qt.sort_order(index), item_id) for index, item_id in enumerate(ids)],
            )
    board._dirty = True
    return {"seed": seed, "images": len(images), "moved": moved}
