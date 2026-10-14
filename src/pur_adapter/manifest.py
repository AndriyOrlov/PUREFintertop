"""Neutral JSON + assets contract for host applications such as FrameRef.

This module does not claim to decode FrameRef's private save format. A host
integration supplies selected video frames, PDF page previews and other media
as images, and calls this contract to import/export PureRef-compatible boards.
"""

from dataclasses import asdict
import json
from pathlib import Path
from .board import Board, atomic_write, finite
from .compatibility import clone_board
from .container import UnsupportedFormat
from .drawings import rasterize_drawing
from . import qt

PREVIEW_KINDS = {"video", "pdf", "link", "svg", "drawing"}


def asset_path(root, value):
    if not isinstance(value, str) or not value:
        raise ValueError("Missing asset path")
    root = Path(root).resolve()
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Asset path escapes package directory")
    return path


def import_manifest(manifest, *, base_dir=None):
    """Return (Board, report). Raster previews are required for unsupported media."""
    if isinstance(manifest, (str, Path)):
        path = Path(manifest)
        document = json.loads(path.read_text(encoding="utf-8"))
        root = path.parent if base_dir is None else Path(base_dir)
    else:
        document = manifest
        root = Path(base_dir or ".")
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("Expected manifest schema_version=1")
    items = document.get("items")
    if not isinstance(items, list) or len(items) > 100000:
        raise ValueError("Invalid items array")
    source_ids = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Every item must be an object")
        item_id = item.get("id")
        if not isinstance(item_id, (str, int)) or isinstance(item_id, bool):
            raise ValueError("Item IDs must be strings or integers")
        if item_id in source_ids:
            raise ValueError("Duplicate item ID")
        source_ids.add(item_id)
    for item in items:
        if item.get("parent_id") is not None and item["parent_id"] not in source_ids:
            raise ValueError("Missing parent item")
    board = Board()
    id_map = {}
    changes = []
    try:
        for item in items:
            kind = item.get("kind")
            name = item.get("name")
            position = {"x": item.get("x", 0), "y": item.get("y", 0)}
            if kind == "image" or kind in PREVIEW_KINDS:
                field = "path" if kind == "image" else "preview_path"
                path = asset_path(root, item.get(field))
                new_id = board.add_image(path, name=name, **position)
                if kind != "image":
                    changes.append(
                        {
                            "item_id": item["id"],
                            "code": "media_preview_used",
                            "kind": kind,
                            "path": item[field],
                        }
                    )
            elif kind == "note":
                new_id = board.add_note(
                    item.get("text", ""), name=name or "Note", **position
                )
            elif kind == "group":
                with board._conn:
                    new_id = board._add_item(name or "Group", **position)
                    board._conn.execute(
                        "INSERT INTO items_groups (id,lock_mode,background_color) VALUES (?,0,'')",
                        (new_id,),
                    )
            else:
                raise UnsupportedFormat(f"Unsupported manifest item kind: {kind!r}")
            native_image = item.get("native_image_properties")
            if native_image is not None:
                if kind != "image" or not isinstance(native_image, dict):
                    raise ValueError("Native image properties require an image")
                if set(native_image) != {"image_bounds", "image_transform", "flags"}:
                    raise ValueError("Incomplete native image properties")
                qt.read_transform(native_image["image_transform"])
                bounds = qt.binary(native_image["image_bounds"])
                if (
                    len(bounds) > 4 * 1024 * 1024
                    or b"QPainterPath\0" not in bounds[:100]
                ):
                    raise ValueError("Invalid native crop path")
                flags = native_image["flags"]
                if (
                    not isinstance(flags, int)
                    or isinstance(flags, bool)
                    or not 0 <= flags < 2**31
                ):
                    raise ValueError("Invalid native image flags")
                board._conn.execute(
                    "UPDATE items_images SET image_bounds=?,image_transform=?,flags=? WHERE id=?",
                    (
                        native_image["image_bounds"],
                        native_image["image_transform"],
                        flags,
                        new_id,
                    ),
                )
                board._conn.commit()
            id_map[item["id"]] = new_id
            updates = {
                key: item[key]
                for key in ("opacity", "locked", "z", "comment")
                if key in item
            }
            if updates:
                board.update(new_id, **updates)
            if "matrix" in item:
                matrix = item["matrix"]
                if not isinstance(matrix, list) or len(matrix) != 9:
                    raise ValueError("matrix must contain 9 row-major numbers")
                finite(**{str(index): value for index, value in enumerate(matrix)})
                board._conn.execute(
                    "UPDATE items SET transform=? WHERE id=?",
                    (qt.transform(matrix), new_id),
                )
                board._conn.commit()
        for item in items:
            parent = item.get("parent_id")
            if parent is not None:
                board.update(id_map[item["id"]], parent_id=id_map[parent])
        return board, {
            "schema_version": 1,
            "changes": changes,
            "unresolved": [],
            "id_map": id_map,
        }
    except Exception:
        board.close()
        raise


def export_manifest(board, directory, *, drawing_scale=4):
    """Export portable assets with local matrices and explicit parent IDs."""
    directory = Path(directory)
    if directory.exists():
        raise FileExistsError(directory)
    clone = clone_board(board)
    report = {"schema_version": 1, "rasterized_drawings": [], "unresolved": []}
    try:
        for item in clone.items:
            if item.kind == "drawing":
                rasterize_drawing(clone, item.id, scale=drawing_scale)
                report["rasterized_drawings"].append(item.id)
            elif item.kind == "unknown":
                raise UnsupportedFormat(f"Unsupported item {item.id}")
        for row in clone._conn.execute("SELECT id,data FROM images"):
            if row["data"] is None:
                raise UnsupportedFormat(
                    f'Image {row["id"]} is linked; embed it before export'
                )
        directory.mkdir(parents=True)
        assets = directory / "assets"
        clone.export_assets(assets)
        document = {
            "schema_version": 1,
            "coordinate_system": "y_down_row_vector_local",
            "items": [],
        }
        for item in clone.items:
            entry = asdict(item)
            matrix = clone._conn.execute(
                "SELECT transform FROM items WHERE id=?", (item.id,)
            ).fetchone()[0]
            entry["matrix"] = qt.read_transform(matrix)
            if item.kind == "image":
                row = clone._conn.execute(
                    "SELECT format,width,height FROM images WHERE id=?",
                    (item.image_id,),
                ).fetchone()
                suffix = (row["format"] or "bin").lower()
                if not suffix.isalnum():
                    suffix = "bin"
                entry["path"] = f"assets/image-{item.image_id}.{suffix}"
                entry["pixel_size"] = [row["width"], row["height"]]
                bounds = clone._conn.execute(
                    "SELECT image_bounds,image_transform,flags FROM items_images WHERE id=?",
                    (item.id,),
                ).fetchone()
                # Keep the source image and native crop description. A host can map it
                # deliberately instead of treating a cropped image as an uncropped one.
                entry["native_image_properties"] = {
                    key: bounds[key] for key in bounds.keys()
                }
            document["items"].append(entry)
        atomic_write(
            directory / "board.json",
            json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        atomic_write(
            directory / "report.json",
            json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        return report
    finally:
        clone.close()
