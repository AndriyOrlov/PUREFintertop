"""Neutral JSON + assets exchange with host applications such as FrameRef.

`export_manifest(board, folder)` writes `board.json`, `assets/` and `report.json`.
`import_manifest(path_or_dict)` builds a Board from the same JSON.

Schema version 2 keeps everything the adapter can read from a board: full
matrices, crops, flips, filters, animation settings, note text and style,
group style, vector pen strokes, comments and sibling order. Version 1
documents are still accepted. The JSON Schema is docs/manifest.schema.json.

Host-only media is mapped to what PureRef can show, and the mapping is
reported. The original description travels in the item's `extensions`, which
callers can keep next to the .pur with `sidecar.write_sidecar`:

    video, link, svg  -> image of `preview_path` (+ URL as the item comment)
    pdf               -> a group with one image per entry in `pages`
    audio, link       -> a note with title and URL when there is no preview
    arrow, line       -> a native pen drawing (arrow, solid or dashed)

This module does not read FrameRef's private save format.
"""

import json
from pathlib import Path
from .board import Board, atomic_write, finite
from .container import UnsupportedFormat
from .drawings import ARROW, DASHED, SOLID, Stroke, render_strokes
from .scene import drawing_order, world_properties
from .text import html_from_plain, is_rich_text, plain_text
from . import qt

SCHEMA_VERSION = 2
MAX_ITEMS = 100_000
PDF_PAGE_GAP = 20  # scene units between pages laid out side by side

PATH_ELEMENT_NAMES = {0: "move", 1: "line", 2: "curve", 3: "curve_data"}
PATH_ELEMENT_KINDS = {name: kind for kind, name in PATH_ELEMENT_NAMES.items()}
STYLE_NAMES = {SOLID: "solid", DASHED: "dashed", ARROW: "arrow"}
STYLE_CODES = {name: code for code, name in STYLE_NAMES.items()}

PREVIEW_KINDS = {"video", "pdf", "link", "svg", "drawing", "audio"}
# Host kinds PureRef cannot hold as such; their description goes to extensions.
HOST_ONLY_KINDS = {"video", "pdf", "link", "svg", "audio", "arrow", "line"}
_COMMON_FIELDS = {"id", "kind", "name", "parent_id", "x", "y", "matrix", "opacity",
                  "locked", "z", "comment", "order_index", "extensions",
                  # informative, written on export and ignored on import:
                  "world_matrix", "stacking_index"}
_KIND_FIELDS = {
    "image": {"path", "image"},
    "note": {"text", "text_format", "plain_text", "style"},
    "group": {"style"},
    "drawing": {"strokes", "preview_path", "local_bounds"},
    "video": {"preview_path", "url", "title"},
    "pdf": {"pages", "preview_path", "url", "title"},
    "link": {"preview_path", "url", "title"},
    "audio": {"preview_path", "url", "title"},
    "svg": {"preview_path", "url", "title"},
    "arrow": {"points", "color", "width", "dashed"},
    "line": {"points", "color", "width", "dashed"},
}
# Fields written by schema version 1 exports.
_VERSION_1_FIELDS = {"text", "image_id", "path", "pixel_size", "native_image_properties",
                     "preview_path"}


def asset_path(root, value):
    """Resolve a relative asset path, refusing anything outside the package."""
    if not isinstance(value, str) or not value:
        raise ValueError("Missing asset path")
    root = Path(root).resolve()
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Asset path escapes package directory")
    return path


# --- Export -------------------------------------------------------------------------


def _image_entry(board, item, asset_paths, report):
    settings = board.image_settings(item.id)
    options = board._row("items_images", item.id)
    asset = board._row("images", item.image_id) if item.image_id is not None else None
    path = asset_paths.get(item.image_id)
    if path is None:
        report["unresolved"].append(
            {"item_id": item.id, "code": "linked_image_not_embedded",
             "source": asset["source"] if asset else None}
        )
    return {
        "path": path,
        "image": {
            "pixel_size": list(settings["pixel_size"]) if settings["pixel_size"] else None,
            "format": asset["format"] if asset else None,
            "source": asset["source"] if asset else None,
            "embedded": settings["embedded"],
            "crop": list(settings["crop"]) if settings["crop"] else None,
            "flip_horizontal": settings["flip_horizontal"],
            "flip_vertical": settings["flip_vertical"],
            "grayscale": settings["grayscale"],
            "bilinear": settings["bilinear"],
            "frame": settings["frame"],
            "speed": settings["speed"],
            "playing": settings["playing"],
            # Exact native values; applied verbatim on import when present.
            "native": {
                "image_bounds": qt.binary(options["image_bounds"]).hex(),
                "image_transform": qt.read_transform(options["image_transform"]),
                "flags": options["flags"],
            },
        },
    }


def strokes_to_json(strokes):
    return [
        {
            "color": list(stroke.color),
            "width": stroke.width,
            "style": STYLE_NAMES.get(stroke.style, stroke.style),
            "fill_rule": stroke.fill_rule,
            "path": [[PATH_ELEMENT_NAMES[kind], x, y] for kind, x, y in stroke.elements],
            "extra_point": list(stroke.extra_point),
        }
        for stroke in strokes
    ]


def strokes_from_json(data):
    if not isinstance(data, list) or not data:
        raise ValueError("strokes must be a non-empty list")
    strokes = []
    for stroke in data:
        style = stroke.get("style", "solid")
        style = STYLE_CODES.get(style, style)
        elements = tuple(
            (PATH_ELEMENT_KINDS[kind] if isinstance(kind, str) else kind, x, y)
            for kind, x, y in stroke["path"]
        )
        strokes.append(Stroke(tuple(stroke["color"]), stroke["width"], elements,
                              stroke.get("fill_rule", 0),
                              tuple(stroke.get("extra_point", (0, 0))), style))
    return strokes


def _drawing_entry(board, item, assets_folder, drawing_scale):
    strokes = board.drawing_strokes(item.id)
    png, bounds, _ = render_strokes(strokes, drawing_scale)
    preview = f"assets/drawing-{item.id}.png"
    atomic_write(assets_folder / f"drawing-{item.id}.png", png)
    return {"strokes": strokes_to_json(strokes), "preview_path": preview,
            "local_bounds": list(bounds)}


def export_manifest(board, directory, *, drawing_scale=4, extensions=None,
                    scene_preview=True):
    """Write a lossless JSON description of the board plus its assets.

    `extensions` ({item_id: json_data}, e.g. from `sidecar.read_sidecar`) is
    copied into each item's `extensions`. Returns the report.
    """
    directory = Path(directory)
    if directory.exists():
        raise FileExistsError(directory)
    report = {"schema_version": SCHEMA_VERSION, "unresolved": []}
    extensions = extensions or {}
    staging = directory.with_name(directory.name + ".partial")
    if staging.exists():
        raise FileExistsError(staging)
    try:
        assets_folder = staging / "assets"
        assets_folder.mkdir(parents=True)
        asset_paths = {}
        for written in board.export_assets(assets_folder):
            image_id = int(Path(written).stem.split("-")[1])
            asset_paths[image_id] = "assets/" + Path(written).name
        world = world_properties(board)
        stacking = {item_id: index for index, item_id in enumerate(drawing_order(board))}
        sibling_rank = _sibling_ranks(board)
        items = []
        for item in board.items:
            entry = {
                "id": item.id,
                "kind": item.kind,
                "name": item.name,
                "parent_id": item.parent_id,
                "x": item.x,
                "y": item.y,
                "matrix": board.transform_matrix(item.id),
                "opacity": item.opacity,
                "locked": item.locked,
                "z": item.z,
                "comment": item.comment,
                "order_index": sibling_rank.get(item.id),
                "stacking_index": stacking[item.id],
                "world_matrix": world[item.id][0],
            }
            if item.kind == "image":
                entry.update(_image_entry(board, item, asset_paths, report))
            elif item.kind == "note":
                entry["text"] = item.text
                entry["text_format"] = "html" if is_rich_text(item.text or "") else "plain"
                entry["plain_text"] = plain_text(item.text or "")
                entry["style"] = board.note_style(item.id)
            elif item.kind == "group":
                entry["style"] = board.group_style(item.id)
            elif item.kind == "drawing":
                entry.update(_drawing_entry(board, item, assets_folder, drawing_scale))
            else:
                raise UnsupportedFormat(f"Unsupported item {item.id}")
            if item.id in extensions:
                entry["extensions"] = extensions[item.id]
            items.append(entry)
        document = {
            "schema_version": SCHEMA_VERSION,
            "coordinate_system": "y_down_row_vector_local",
            "board": {"format": board.format,
                      "application_version": board.application_version},
            "items": items,
        }
        if scene_preview and items:
            from .render import render_scene

            picture = render_scene(board, width=1600).convert("RGB")
            picture.save(staging / "preview.png")
            document["preview_path"] = "preview.png"
        atomic_write(staging / "board.json",
                     json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"))
        atomic_write(staging / "report.json",
                     json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
        staging.rename(directory)  # the package appears complete or not at all
    except BaseException:
        _remove_tree(staging)
        raise
    return report


def _sibling_ranks(board):
    """{item_id: position among its siblings} where the order can be read."""
    ranks = {}
    for parent_id in {item.parent_id for item in board.items}:
        try:
            ordered = board.ordered_children(parent_id)
        except ValueError:
            continue  # an ordering encoding this adapter cannot compare
        ranks.update({item_id: index for index, item_id in enumerate(ordered)})
    return ranks


def _remove_tree(folder):
    import shutil

    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)


# --- Import -------------------------------------------------------------------------


def _load_document(manifest, base_dir):
    if isinstance(manifest, (str, Path)):
        path = Path(manifest)
        document = json.loads(path.read_text(encoding="utf-8"))
        root = path.parent if base_dir is None else Path(base_dir)
    else:
        document = manifest
        root = Path(base_dir or ".")
    if not isinstance(document, dict) or document.get("schema_version") not in (1, 2):
        raise ValueError("Expected manifest schema_version 1 or 2")
    items = document.get("items")
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise ValueError("Invalid items array")
    return document, items, root


def _check_ids(items):
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Every item must be an object")
        item_id = item.get("id")
        if not isinstance(item_id, (str, int)) or isinstance(item_id, bool):
            raise ValueError("Item IDs must be strings or integers")
        if item_id in seen:
            raise ValueError("Duplicate item ID")
        seen.add(item_id)
    for item in items:
        if item.get("parent_id") is not None and item["parent_id"] not in seen:
            raise ValueError("Missing parent item")


def _color_tuple(value):
    """Accept '#AARRGGBB' or [r, g, b, a]."""
    if isinstance(value, str):
        from .render import parse_color

        color = parse_color(value, None)
        if color is None:
            raise ValueError("color must be #AARRGGBB or [r, g, b, a]")
        return color
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return tuple(value)
    raise ValueError("color must be #AARRGGBB or [r, g, b, a]")


class _Importer:
    def __init__(self, items, root, version, strict):
        self.items = items
        self.root = root
        self.version = version
        self.strict = strict
        self.board = Board()
        self.id_map = {}
        self.changes = []
        self.unresolved = []
        self.extensions = {}

    def note_problem(self, item, code, **details):
        if self.strict:
            raise ValueError(f"Item {item.get('id')!r}: {code}")
        self.unresolved.append({"item_id": item.get("id"), "code": code, **details})

    def run(self):
        for item in self.items:
            self.check_fields(item)
            new_id = self.create(item)
            if new_id is None:
                continue
            self.id_map[item["id"]] = new_id
            self.apply_common(item, new_id)
            host_data = dict(item.get("extensions") or {})
            if item.get("kind") in HOST_ONLY_KINDS:
                host_data.setdefault("kind", item["kind"])
                for key in ("url", "title", "pages"):
                    if key in item:
                        host_data.setdefault(key, item[key])
            if host_data:
                self.extensions[new_id] = host_data
        self.attach_parents()
        self.restore_sibling_order()
        return self.board

    def check_fields(self, item):
        known = _COMMON_FIELDS | _KIND_FIELDS.get(item.get("kind"), set())
        if self.version == 1:
            known |= _VERSION_1_FIELDS
        for field in sorted(set(item) - known):
            self.note_problem(item, "unknown_field", field=field)

    # Creating each kind ---------------------------------------------------------

    def create(self, item):
        kind = item.get("kind")
        name = item.get("name")
        position = {"x": item.get("x", 0), "y": item.get("y", 0)}
        if kind == "image":
            new_id = self.board.add_image(asset_path(self.root, item.get("path")),
                                          name=name, **position)
            self.apply_image_settings(item, new_id)
            return new_id
        if kind == "note":
            text = item.get("text", "")
            if item.get("text_format") == "plain" and not is_rich_text(text) and "\n" in text:
                text = html_from_plain(text)  # PureRef would otherwise join the lines
                self.changes.append({"item_id": item["id"], "code": "plain_text_to_html"})
            new_id = self.board.add_note(text, name=name or "Note", **position)
            if item.get("style"):
                self.apply_note_style(item["style"], new_id)
            return new_id
        if kind == "group":
            style = item.get("style") or {}
            if self.version == 1:  # version 1 groups had no style
                style = {"background_color": "", "locked_mode": False}
            new_id = self.board.add_group(name or "Group", **position)
            with self.board._conn:
                self.board._conn.execute(
                    "UPDATE items_groups SET background_color=?, lock_mode=? WHERE id=?",
                    (style.get("background_color", "#ff202020"),
                     bool(style.get("locked_mode", True)), new_id),
                )
            return new_id
        if kind == "drawing" and "strokes" in item:
            return self.board.add_drawing(strokes_from_json(item["strokes"]),
                                          name=name or "Drawing", **position)
        if kind in ("arrow", "line"):
            return self.create_arrow(item, name, position)
        if kind == "pdf" and item.get("pages"):
            return self.create_pdf(item, name, position)
        if kind in PREVIEW_KINDS:
            return self.create_preview(item, kind, name, position)
        if self.strict:
            raise UnsupportedFormat(f"Unsupported manifest item kind: {kind!r}")
        if item.get("preview_path"):
            return self.create_preview(item, kind, name, position)
        self.note_problem(item, "unsupported_kind", kind=kind)
        return None

    def create_preview(self, item, kind, name, position):
        if item.get("preview_path"):
            new_id = self.board.add_image(asset_path(self.root, item["preview_path"]),
                                          name=name or item.get("title"), **position)
            if item.get("url") and not item.get("comment"):
                self.board.update(new_id, comment=item["url"])
            self.changes.append({"item_id": item["id"], "code": "media_preview_used",
                                 "kind": kind, "path": item["preview_path"]})
            return new_id
        if kind in ("link", "audio") and (item.get("url") or item.get("title")):
            lines = [line for line in (item.get("title"), item.get("url")) if line]
            new_id = self.board.add_note(html_from_plain("\n".join(lines)),
                                         name=name or kind.title(), **position)
            self.changes.append({"item_id": item["id"], "code": "media_as_note", "kind": kind})
            return new_id
        raise ValueError(f"Item {item['id']!r}: {kind} needs preview_path")

    def create_pdf(self, item, name, position):
        """A PDF becomes a group of page images laid out left to right."""
        group = self.board.add_group(name or item.get("title") or "PDF", **position)
        cursor = 0.0
        for number, page in enumerate(item["pages"], start=1):
            if isinstance(page, dict):  # {"path": ..., "page": original page number}
                path, number = page.get("path"), page.get("page", number)
            else:
                path = page
            page_id = self.board.add_image(asset_path(self.root, path),
                                           name=f"Page {number}", parent_id=group)
            width = self.board.image_settings(page_id)["pixel_size"][0]
            self.board.update(page_id, x=cursor + width / 2)
            cursor += width + PDF_PAGE_GAP
        self.changes.append({"item_id": item["id"], "code": "pdf_pages_as_images",
                             "pages": len(item["pages"])})
        return group

    def create_arrow(self, item, name, position):
        points = item.get("points")
        if not isinstance(points, list) or len(points) < 2:
            raise ValueError(f"Item {item['id']!r}: an arrow needs two or more points")
        elements = [(0, *points[0])] + [(1, *point) for point in points[1:]]
        if item.get("dashed"):
            style = DASHED
        else:
            style = ARROW if item["kind"] == "arrow" else SOLID
        stroke = Stroke(_color_tuple(item.get("color", "#ffffffff")),
                        item.get("width", 2), tuple(elements), 0, (0, 0), style)
        new_id = self.board.add_drawing([stroke], name=name or item["kind"].title(), **position)
        self.changes.append({"item_id": item["id"], "code": "arrow_as_pen_drawing"})
        return new_id

    # Properties ---------------------------------------------------------------

    def apply_image_settings(self, item, new_id):
        image = item.get("image") or {}
        native = image.get("native") or item.get("native_image_properties")
        if native:
            self.apply_native_image(native, new_id)
            return
        options = {}
        if image.get("crop"):
            options["crop"] = tuple(image["crop"])
        for key in ("flip_horizontal", "flip_vertical", "grayscale", "bilinear"):
            if key in image:
                options[key] = bool(image[key])
        for key in ("frame", "speed"):
            if image.get(key) is not None:
                options[key] = image[key]
        if options:
            self.board.configure_image(new_id, **options)

    def apply_native_image(self, native, new_id):
        if set(native) != {"image_bounds", "image_transform", "flags"}:
            raise ValueError("Incomplete native image properties")
        bounds = native["image_bounds"]
        if self.version == 2:
            bounds = bytes.fromhex(bounds).decode("latin1")
        transform = native["image_transform"]
        if isinstance(transform, list):
            finite(**{str(index): value for index, value in enumerate(transform)})
            transform = qt.transform(transform)
        qt.read_transform(transform)
        raw_bounds = qt.binary(bounds)
        if len(raw_bounds) > 4 * 1024 * 1024 or b"QPainterPath\0" not in raw_bounds[:100]:
            raise ValueError("Invalid native crop path")
        flags = native["flags"]
        if not isinstance(flags, int) or isinstance(flags, bool) or not 0 <= flags < 2**31:
            raise ValueError("Invalid native image flags")
        with self.board._conn:
            self.board._conn.execute(
                "UPDATE items_images SET image_bounds=?, image_transform=?, flags=? WHERE id=?",
                (bounds, transform, flags, new_id),
            )

    def apply_note_style(self, style, new_id):
        """Store the style exactly: empty colours mean "PureRef default" and
        must not be replaced by explicit colours."""
        width, height = style.get("width", -1), style.get("height", -1)
        finite(width=width, height=height)
        if any(value != -1 and value <= 0 for value in (width, height)):
            raise ValueError("Note dimensions must be positive or -1 for automatic")
        colors = {}
        for key in ("background_color", "text_color"):
            value = style.get(key)
            colors[key] = self.board._color(value) if value else value
        if colors["background_color"] is None:
            colors["background_color"] = ""  # what PureRef stores for "default"
        note_style = style.get("style", 0)
        if not isinstance(note_style, int) or isinstance(note_style, bool):
            raise ValueError("Note style must be an integer")
        with self.board._conn:
            self.board._conn.execute(
                "UPDATE items_notes SET fixed_size=?, background_color=?, text_color=?,"
                " style=? WHERE id=?",
                (qt.size(width, height), colors["background_color"], colors["text_color"],
                 note_style, new_id),
            )

    def apply_common(self, item, new_id):
        updates = {key: item[key] for key in ("opacity", "locked", "z", "comment")
                   if key in item}
        if updates:
            self.board.update(new_id, **updates)
        if "matrix" in item:
            matrix = item["matrix"]
            if not isinstance(matrix, list) or len(matrix) != 9:
                raise ValueError("matrix must contain 9 row-major numbers")
            finite(**{str(index): value for index, value in enumerate(matrix)})
            with self.board._conn:
                self.board._conn.execute("UPDATE items SET transform=? WHERE id=?",
                                         (qt.transform(matrix), new_id))

    def attach_parents(self):
        for item in self.items:
            parent = item.get("parent_id")
            if parent is None or item["id"] not in self.id_map:
                continue
            if parent not in self.id_map:
                self.note_problem(item, "parent_not_imported", parent_id=parent)
                continue
            self.board.update(self.id_map[item["id"]], parent_id=self.id_map[parent])

    def restore_sibling_order(self):
        by_parent = {}
        for item in self.items:
            if item["id"] in self.id_map:
                by_parent.setdefault(item.get("parent_id"), []).append(item)
        for siblings in by_parent.values():
            ranks = [item.get("order_index") for item in siblings]
            if all(isinstance(rank, int) for rank in ranks) and len(siblings) > 1:
                ordered = sorted(siblings, key=lambda item: item["order_index"])
                self.board.reorder([self.id_map[item["id"]] for item in ordered])


def import_manifest(manifest, *, base_dir=None, strict=False):
    """Return (Board, report). The caller owns the board.

    Unknown fields and unsupported kinds are listed in `report['unresolved']`;
    with `strict=True` they raise instead. `report['extensions']` holds host
    data per new item ID, ready for `sidecar.write_sidecar`.
    """
    document, items, root = _load_document(manifest, base_dir)
    _check_ids(items)
    importer = _Importer(items, root, document["schema_version"], strict)
    try:
        board = importer.run()
    except Exception:
        importer.board.close()
        raise
    return board, {
        "schema_version": SCHEMA_VERSION,
        "changes": importer.changes,
        "unresolved": importer.unresolved,
        "id_map": importer.id_map,
        "extensions": importer.extensions,
    }
