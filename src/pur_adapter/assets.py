"""Image resources: linked files, relinking, embedding and lossless shrinking."""

import hashlib
import io
import os
from pathlib import Path
from PIL import Image, ImageChops
from . import qt
from .images import describe_image_file

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def linked_images(board):
    """Images whose pixels are not stored in the board, only a file path."""
    return [
        {"image_id": row["id"], "source": row["source"], "width": row["width"],
         "height": row["height"], "exists": bool(row["source"]) and Path(row["source"]).is_file()}
        for row in board._conn.execute(
            "SELECT id, source, width, height FROM images WHERE data IS NULL ORDER BY id"
        )
    ]


def _candidates(file_name, search_folders, max_files=200_000):
    """Files called `file_name` (case-insensitive) below the search folders."""
    wanted = file_name.casefold()
    seen = 0
    for folder in search_folders:
        for root, _, names in os.walk(folder):
            for name in names:
                seen += 1
                if seen > max_files:
                    return
                if name.casefold() == wanted:
                    yield Path(root) / name


def relink(board, search_folders):
    """Point missing linked images at files with the same name and size.

    A candidate must have the same name and, when the board knows them, the
    same pixel size; a matching checksum marks it as verified. Returns a report.
    """
    report = {"relinked": [], "not_found": []}
    for link in linked_images(board):
        if link["exists"] or not link["source"]:
            continue
        file_name = Path(link["source"].replace("\\", "/")).name
        match = None
        for candidate in _candidates(file_name, search_folders):
            data = candidate.read_bytes()
            try:
                width, height, _ = describe_image_file(data, candidate.suffix)
            except OSError:
                continue
            known_size = link["width"] and link["height"]
            if known_size and (width, height) != (link["width"], link["height"]):
                continue
            stored = board._row("images", link["image_id"])["checksum"]
            match = (candidate, stored == hashlib.md5(data).hexdigest())
            break
        if match is None:
            report["not_found"].append(link["image_id"])
            continue
        path, verified = match
        with board._conn:
            board._conn.execute("UPDATE images SET source=?, origin=? WHERE id=?",
                                (str(path.resolve()), str(path.resolve()), link["image_id"]))
        board._dirty = True
        report["relinked"].append({"image_id": link["image_id"], "path": str(path),
                                   "checksum_verified": verified})
    return report


def embed_linked(board):
    """Copy the files of linked images into the board. Returns a report."""
    report = {"embedded": [], "missing": []}
    for link in linked_images(board):
        if not link["exists"]:
            report["missing"].append({"image_id": link["image_id"], "source": link["source"]})
            continue
        path = Path(link["source"])
        data = path.read_bytes()
        width, height, image_format = describe_image_file(data, path.suffix)
        with board._conn:
            board._conn.execute(
                "UPDATE images SET data=?, checksum=?, width=?, height=?, format=?,"
                " source_type=1 WHERE id=?",
                (data, hashlib.md5(data).hexdigest(), width, height, image_format,
                 link["image_id"]),
            )
            if (link["width"], link["height"]) != (width, height):
                _fit_items_to_new_size(board, link["image_id"], link["width"],
                                       link["height"], width, height)
        board._dirty = True
        report["embedded"].append({"image_id": link["image_id"], "bytes": len(data)})
    return report


def _fit_items_to_new_size(board, image_id, old_width, old_height, width, height):
    """Items that showed the whole (unknown-size) image keep showing all of it."""
    old_bounds = qt.binary(qt.image_bounds(old_width, old_height))
    for row in board._conn.execute(
        "SELECT id, image_bounds FROM items_images WHERE image=?", (image_id,)
    ).fetchall():
        if qt.binary(row["image_bounds"]) == old_bounds:
            board._conn.execute(
                "UPDATE items_images SET image_bounds=?, image_transform=? WHERE id=?",
                (qt.image_bounds(width, height),
                 qt.transform(qt.affine(-width / 2, -height / 2)), row["id"]),
            )


def _same_pixels(first, second):
    with Image.open(io.BytesIO(first)) as a, Image.open(io.BytesIO(second)) as b:
        if a.size != b.size or a.mode != b.mode:
            return False
        return ImageChops.difference(a.convert("RGBA"), b.convert("RGBA")).getbbox() is None


def shrink_images(board, *, minimum_saving=1024):
    """Re-compress PNG images losslessly where it saves space.

    Pixels, mode and colour profile stay identical; only the PNG encoding
    changes, so exported asset bytes will differ from the original files.
    Animated PNGs and other formats are left alone. Returns a report.
    """
    report = {"shrunk": [], "bytes_saved": 0}
    rows = board._conn.execute(
        "SELECT id, data FROM images WHERE data IS NOT NULL ORDER BY id"
    ).fetchall()
    for row in rows:
        data = bytes(row["data"])
        if not data.startswith(PNG_SIGNATURE):
            continue
        with Image.open(io.BytesIO(data)) as image:
            if getattr(image, "n_frames", 1) > 1:
                continue
            output = io.BytesIO()
            options = {"optimize": True}
            for key in ("icc_profile", "dpi", "transparency", "gamma"):
                if key in image.info:
                    options[key] = image.info[key]
            image.save(output, format="PNG", **options)
        smaller = output.getvalue()
        if len(data) - len(smaller) < minimum_saving or not _same_pixels(data, smaller):
            continue
        with board._conn:
            board._conn.execute("UPDATE images SET data=?, checksum=? WHERE id=?",
                                (smaller, hashlib.md5(smaller).hexdigest(), row["id"]))
        board._dirty = True
        saved = len(data) - len(smaller)
        report["shrunk"].append({"image_id": row["id"], "before": len(data),
                                 "after": len(smaller)})
        report["bytes_saved"] += saved
    if report["shrunk"]:
        board._conn.execute("VACUUM")  # give the freed pages back
    return report
