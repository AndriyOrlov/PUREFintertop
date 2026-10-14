"""Host data that a .pur file cannot hold, kept in a file next to the board.

Native PureRef 2.1.3 deletes unknown tables and columns when it saves
("Encountered unknown table ... it will be dropped on save"). Data such as a
FrameRef video trim, an audio clip or a PDF page number therefore lives in

    board.pur.host.json

next to the board. PureRef keeps item IDs when it resaves a board, so entries
are stored per item ID together with a fingerprint (kind, name and image
checksum). If the user changed the board in PureRef, entries whose item no
longer matches are reported as stale instead of being attached to the wrong
item.
"""

import hashlib
import json
from pathlib import Path
from .board import atomic_write

SIDECAR_SUFFIX = ".host.json"
SCHEMA_VERSION = 1


def sidecar_path(board_path):
    board_path = Path(board_path)
    return board_path.with_name(board_path.name + SIDECAR_SUFFIX)


def fingerprint(board, item_id):
    """What identifies an item well enough to notice it was replaced."""
    item = board.get(item_id)
    identity = {"kind": item.kind, "name": item.name}
    if item.kind == "image" and item.image_id is not None:
        row = board._row("images", item.image_id)
        identity["image_checksum"] = row["checksum"] if row else None
    elif item.kind == "note":
        identity["text_sha256"] = hashlib.sha256((item.text or "").encode()).hexdigest()
    return identity


def write_sidecar(board, board_path, entries, *, host="host", overwrite=True):
    """Store {item_id: json_data} next to `board_path`. Empty data removes the file."""
    path = sidecar_path(board_path)
    if not entries:
        if path.exists():
            path.unlink()
        return None
    document = {
        "schema_version": SCHEMA_VERSION,
        "host": host,
        "board": Path(board_path).name,
        "items": [
            {"item_id": item_id, "fingerprint": fingerprint(board, item_id), "data": data}
            for item_id, data in sorted(entries.items())
        ],
    }
    atomic_write(path, json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"),
                 overwrite)
    return path


def read_sidecar(board, board_path):
    """Return ({item_id: data}, report). Missing file -> ({}, empty report)."""
    path = sidecar_path(board_path)
    report = {"sidecar": str(path), "attached": 0, "stale": []}
    if not path.exists():
        return {}, report
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported sidecar schema_version")
    entries = {}
    for entry in document.get("items", []):
        item_id = entry.get("item_id")
        try:
            current = fingerprint(board, item_id)
        except (KeyError, ValueError):
            current = None
        if current is None or current != entry.get("fingerprint"):
            report["stale"].append({"item_id": item_id, "reason": "item changed or removed"})
            continue
        entries[item_id] = entry.get("data")
        report["attached"] += 1
    return entries, report
