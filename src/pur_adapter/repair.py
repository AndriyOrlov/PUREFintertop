"""Recover what can be recovered from a damaged .pur file.

`repair(path, folder)` never changes the damaged file. It tries, in order:

1. Checksum only: the content is intact, so the board is saved again with a
   correct checksum.
2. Damaged database: every row that can still be read is copied into a fresh
   board; items whose image or parent was lost are reported.
3. Last resort: pictures are cut straight out of the raw bytes (PNG, JPEG,
   GIF, WebP) and laid out on a new board.

Large images in modern files are stored across SQLite overflow pages, so
step 3 finds small images and legacy (1.x) images most reliably. The board's
own thumbnail is a JPEG too and is usually among the pictures found.
"""

import hashlib
import io
import json
import sqlite3
import struct
from pathlib import Path
from PIL import Image
from .board import NATIVE_SCHEMA, NO_PARENT, Board, atomic_write
from .container import InvalidPurFile, UnsupportedFormat, read_header

MAX_CARVED_IMAGES = 10_000
_JPEG_END_TRIES = 64


def repair(path, folder):
    """Write `recovered.pur`, `images/` and `report.json` into a new folder."""
    path, folder = Path(path), Path(folder)
    if folder.exists():
        raise FileExistsError(folder)
    folder.mkdir(parents=True)
    data = path.read_bytes()
    report = {"source": str(path), "status": None, "notes": []}
    try:
        with Board.load(path):
            report["status"] = "healthy"
            report["notes"].append("The file opens normally; nothing was changed.")
    except Exception as problem:  # any failure to open means "damaged"
        report["problem"] = f"{type(problem).__name__}: {problem}"
        board = None
        if data[:4] == b"\0\0\0\x06":
            board = _recover_modern(data, report)
        elif data[:4] == b"\0\0\0\x08":
            board = _recover_legacy(data, report)
        carved = carve_images(data, folder / "images")
        report["carved_images"] = len(carved)
        if board is None and carved:
            board = Board(use_temp_file=len(data) > 128 * 1024**2)
            _lay_out(board, carved)
            report["status"] = "images_only"
        if board is not None:
            with board:
                board.save(folder / "recovered.pur", format="modern")
            report["output"] = str(folder / "recovered.pur")
        elif report["status"] is None:
            report["status"] = "nothing_recovered"
    atomic_write(folder / "report.json", json.dumps(report, indent=2).encode("utf-8"))
    return report


# --- Step 1 and 2: modern files ------------------------------------------------------


def _database_bytes(data):
    """The relocated SQLite bytes, ignoring the checksum."""
    header = read_header(io.BytesIO(data))
    if header.database_size < header.header_size or len(data) < header.database_size:
        raise InvalidPurFile("Header sizes do not fit the file")
    tail = data[header.database_size : header.database_size + header.header_size]
    return tail + data[header.header_size : header.database_size]


def _recover_modern(data, report):
    try:
        database = _database_bytes(data)
    except (InvalidPurFile, struct.error) as exc:
        report["notes"].append(f"Header unreadable: {exc}")
        return None
    damaged = sqlite3.connect(":memory:")
    try:
        damaged.deserialize(database)
    except sqlite3.Error as exc:
        report["notes"].append(f"Database unreadable: {exc}")
        damaged.close()
        return None
    try:
        healthy = damaged.execute("PRAGMA quick_check").fetchall() == [("ok",)]
    except sqlite3.Error as exc:  # damage can break the check itself
        report["notes"].append(f"Integrity check failed: {exc}")
        healthy = False
    damaged.row_factory = sqlite3.Row
    board = Board()
    try:
        counts = _copy_readable_rows(damaged, board)
    finally:
        damaged.close()
    lost = _drop_broken_references(board)
    report["status"] = "checksum_repaired" if healthy and not lost else "partially_recovered"
    report["rows_recovered"] = counts
    report["broken_references"] = lost
    return board


def _copy_readable_rows(damaged, board):
    """Copy rows of the scene tables; unreadable rows are skipped one by one."""
    counts = {}
    with board._conn:
        board._conn.execute("DELETE FROM metadata")
        for table in NATIVE_SCHEMA:
            try:
                damaged_columns = [r[1] for r in damaged.execute(f'PRAGMA table_info("{table}")')]
            except sqlite3.Error:
                continue
            target_columns = {r[1] for r in board._conn.execute(f'PRAGMA table_info("{table}")')}
            columns = [column for column in damaged_columns if column in target_columns]
            if not columns:
                continue
            column_list = ", ".join(f'"{column}"' for column in columns)
            placeholders = ", ".join("?" for _ in columns)
            rows = _readable_rows(damaged, table, column_list)
            board._conn.executemany(
                f"INSERT OR IGNORE INTO {table} ({column_list}) VALUES ({placeholders})",
                [tuple(row) for row in rows],
            )
            counts[table] = len(rows)
    board._dirty = True
    return counts


def _readable_rows(damaged, table, column_list):
    try:
        return damaged.execute(f"SELECT {column_list} FROM {table}").fetchall()
    except sqlite3.Error:
        pass
    rows = []
    try:
        highest = damaged.execute(f"SELECT MAX(rowid) FROM {table}").fetchone()[0] or 0
    except sqlite3.Error:
        highest = 100_000
    for rowid in range(highest + 1):
        try:
            row = damaged.execute(f"SELECT {column_list} FROM {table} WHERE rowid=?",
                                  (rowid,)).fetchone()
        except sqlite3.Error:
            continue
        if row is not None:
            rows.append(row)
    return rows


def _drop_broken_references(board):
    """Remove half-recovered items so the board opens cleanly in PureRef."""
    lost = []
    connection = board._conn
    with connection:
        for row in connection.execute(
            "SELECT id FROM items_images WHERE image IS NULL"
            " OR image NOT IN (SELECT id FROM images)"
        ).fetchall():
            lost.append({"item_id": row[0], "problem": "image data lost"})
            connection.execute("DELETE FROM items_images WHERE id=?", (row[0],))
            connection.execute("DELETE FROM items WHERE id=?", (row[0],))
        kind_ids = " UNION ".join(f"SELECT id FROM {table}" for table in
                                  ("items_images", "items_notes", "items_groups", "items_drawings"))
        for row in connection.execute(
            f"SELECT id FROM items WHERE id NOT IN ({kind_ids})"
        ).fetchall():
            lost.append({"item_id": row[0], "problem": "item details lost"})
            connection.execute("DELETE FROM items WHERE id=?", (row[0],))
        for row in connection.execute(
            "SELECT id FROM items WHERE parent != ? AND parent NOT IN (SELECT id FROM items)",
            (NO_PARENT,),
        ).fetchall():
            lost.append({"item_id": row[0], "problem": "parent lost; moved to the top level"})
            connection.execute("UPDATE items SET parent=? WHERE id=?", (NO_PARENT, row[0]))
        connection.execute(
            "INSERT OR IGNORE INTO metadata (id, thumbnail) VALUES (0, X'')"
        )
    return lost


# --- Step 1 for legacy files -------------------------------------------------------


def _recover_legacy(data, report):
    from .legacy import build_board_from_codec, parse_with_codec

    try:
        parsed = parse_with_codec(data)
        board = build_board_from_codec(parsed, application_version=None)
    except (InvalidPurFile, UnsupportedFormat, ValueError) as exc:
        report["notes"].append(f"Legacy scene unreadable: {exc}")
        return None
    report["status"] = "checksum_repaired"
    report["notes"].append("Recovered from a 1.x file and saved in the modern format.")
    return board


# --- Step 3: carving pictures out of raw bytes -------------------------------------


def _png_end(data, start):
    position = start + 8
    while position + 12 <= len(data):
        length, kind = struct.unpack_from(">I4s", data, position)
        position += 12 + length
        if kind == b"IEND":
            return position
        if length > len(data):
            return None
    return None


def _webp_end(data, start):
    if data[start + 8 : start + 12] != b"WEBP":
        return None
    return start + 8 + struct.unpack_from("<I", data, start + 4)[0]


def _decodes(blob):
    try:
        with Image.open(io.BytesIO(blob)) as image:
            image.load()
        return True
    except Exception:  # any decoder error means "not a whole picture"
        return False


def _search_end(data, start, marker, extra):
    """Try the next few end markers until the bytes decode as a picture."""
    position = start
    for _ in range(_JPEG_END_TRIES):
        position = data.find(marker, position + 1)
        if position < 0:
            return None
        end = position + len(marker) + extra
        if _decodes(data[start:end]):
            return end
    return None


_SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "png", _png_end),
    (b"RIFF", "webp", _webp_end),
    (b"\xff\xd8\xff", "jpg", lambda data, start: _search_end(data, start, b"\xff\xd9", 0)),
    (b"GIF89a", "gif", lambda data, start: _search_end(data, start, b"\x00\x3b", 0)),
    (b"GIF87a", "gif", lambda data, start: _search_end(data, start, b"\x00\x3b", 0)),
]


def carve_images(data, folder):
    """Save every complete picture found in `data`; return the file paths."""
    found = []
    seen = set()
    for signature, suffix, find_end in _SIGNATURES:
        position = data.find(signature)
        while position >= 0 and len(found) < MAX_CARVED_IMAGES:
            end = find_end(data, position)
            if end and end <= len(data):
                blob = data[position:end]
                digest = hashlib.md5(blob).hexdigest()
                if digest not in seen and _decodes(blob):
                    seen.add(digest)
                    found.append((position, suffix, blob))
            position = data.find(signature, position + 1)
    paths = []
    if found:
        folder.mkdir(parents=True, exist_ok=True)
    for number, (_, suffix, blob) in enumerate(sorted(found), start=1):
        path = folder / f"recovered-{number:04d}.{suffix}"
        atomic_write(path, blob)
        paths.append(path)
    return paths


def _lay_out(board, paths, columns=10, gap=20):
    sizes = []
    for path in paths:
        with Image.open(path) as image:
            sizes.append(image.size)
    cell = max(max(width, height) for width, height in sizes) + gap
    for index, path in enumerate(paths):
        board.add_image(path, x=(index % columns) * cell, y=(index // columns) * cell)
