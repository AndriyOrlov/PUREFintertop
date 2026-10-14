"""Command line interface. Every command prints JSON; errors go to stderr (exit 1).

Outputs are never overwritten unless `--overwrite` is given.
"""

import argparse
import json
from pathlib import Path
import sys
from . import __version__
from .board import Board, atomic_write


def save_report(path, report, overwrite=False):
    atomic_write(path, json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"), overwrite)


def _board_and_output(commands, name, help_text):
    command = commands.add_parser(name, help=help_text)
    command.add_argument("input")
    command.add_argument("output")
    command.add_argument("--overwrite", action="store_true")
    return command


def parser():
    root = argparse.ArgumentParser(
        prog="pur-adapter", description="Read, create, edit and repair PureRef .pur boards"
    )
    root.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = root.add_subparsers(dest="command", required=True)

    inspect = commands.add_parser("inspect", help="Print a board as JSON")
    inspect.add_argument("input")

    create = commands.add_parser("create", help="Build a board from a JSON manifest")
    create.add_argument("manifest")
    create.add_argument("output")
    create.add_argument("--format", choices=("modern", "legacy"), default="modern")
    create.add_argument("--strict", action="store_true",
                        help="refuse unknown fields and kinds instead of reporting them")
    create.add_argument("--overwrite", action="store_true")

    patch = _board_and_output(commands, "patch", "Edit one item and save a separate board")
    patch.add_argument("--item", type=int, required=True)
    patch.add_argument("--set", dest="properties", required=True,
                       help='JSON object, e.g. {"x":100,"rotation":20}')

    raster = _board_and_output(commands, "rasterize", "Turn pen drawings into images")
    selection = raster.add_mutually_exclusive_group(required=True)
    selection.add_argument("--item", type=int)
    selection.add_argument("--all", action="store_true")
    raster.add_argument("--scale", type=float, default=4)

    legacy = _board_and_output(commands, "export-legacy",
                               "Flatten and save a PureRef 1.x compatible board")
    legacy.add_argument("--scale", type=float, default=4)

    package = commands.add_parser("export-package", help="Write board.json + assets for a host")
    package.add_argument("input")
    package.add_argument("directory")
    package.add_argument("--no-preview", action="store_true")

    assets = commands.add_parser("extract-assets", help="Write the original embedded image files")
    assets.add_argument("input")
    assets.add_argument("directory")

    render = commands.add_parser("render", help="Draw the board into a PNG or JPEG")
    render.add_argument("input")
    render.add_argument("output")
    render.add_argument("--width", type=int, default=2048)
    render.add_argument("--overwrite", action="store_true")

    repair = commands.add_parser("repair", help="Recover a damaged board into a new folder")
    repair.add_argument("input")
    repair.add_argument("directory")

    merge = commands.add_parser("merge", help="Combine boards into one")
    merge.add_argument("inputs", nargs="+")
    merge.add_argument("--output", required=True)
    merge.add_argument("--overwrite", action="store_true")

    split = commands.add_parser("split", help="Save every top-level group as its own board")
    split.add_argument("input")
    split.add_argument("directory")
    split.add_argument("--overwrite", action="store_true")

    arrange = _board_and_output(commands, "arrange", "Lay items out in rows")
    arrange.add_argument("--parent", type=int, help="arrange the children of this item")
    arrange.add_argument("--gap", type=float, default=10)
    arrange.add_argument("--sort", choices=("name", "height", "none"), default="name")

    report = commands.add_parser("report", help="Write an HTML report with charts")
    report.add_argument("input")
    report.add_argument("output")
    report.add_argument("--no-round-trip", action="store_true",
                        help="skip the host round-trip check (faster on huge boards)")
    report.add_argument("--overwrite", action="store_true")

    search = commands.add_parser("search", help="Find items by name, comment or text")
    search.add_argument("input")
    search.add_argument("query")

    linked = commands.add_parser("linked", help="List images stored only as file paths")
    linked.add_argument("input")

    relink = _board_and_output(commands, "relink", "Find moved linked image files")
    relink.add_argument("--search", action="append", required=True,
                        help="folder to search; can be given several times")

    _board_and_output(commands, "embed", "Copy linked image files into the board")
    _board_and_output(commands, "shrink", "Re-compress PNG images losslessly")
    _board_and_output(commands, "dedupe", "Store identical images once")
    return root


def _modify_and_save(args, change):
    with Board.load(args.input) as board:
        report = change(board)
        board.save(args.output, overwrite=args.overwrite)
    return {"output": args.output, "report": report}


def run(args):
    """Execute one parsed command and return its JSON-friendly result."""
    if args.command == "create":
        from .compatibility import export_legacy
        from .manifest import import_manifest
        from .sidecar import write_sidecar

        board, report = import_manifest(args.manifest, strict=args.strict)
        with board:
            if args.format == "legacy":
                report["compatibility"] = export_legacy(board, args.output,
                                                        overwrite=args.overwrite)
            else:
                board.save(args.output, overwrite=args.overwrite)
                if report["extensions"]:
                    report["sidecar"] = str(write_sidecar(board, args.output,
                                                          report["extensions"]))
        report["extensions"] = {str(key): value for key, value in report["extensions"].items()}
        return {"output": args.output, "report": report}

    if args.command == "merge":
        from .operations import merge_boards

        with Board.load(args.inputs[0]) as target:
            for path in args.inputs[1:]:
                with Board.load(path) as source:
                    merge_boards(target, source)
            target.save(args.output, overwrite=args.overwrite)
            return {"output": args.output, "items": len(target.items)}

    if args.command == "repair":
        from .repair import repair

        return repair(args.input, args.directory)

    if args.command == "patch":
        properties = json.loads(args.properties)
        if not isinstance(properties, dict):
            raise ValueError("--set must be a JSON object")
        with Board.load(args.input) as board:
            board.update(args.item, **properties)
            board.save(args.output, overwrite=args.overwrite)
        return {"output": args.output, "item": args.item}

    if args.command == "rasterize":
        from .drawings import rasterize_drawing

        def rasterize(board):
            chosen = ([item.id for item in board.items if item.kind == "drawing"]
                      if args.all else [args.item])
            return [rasterize_drawing(board, item_id, scale=args.scale) for item_id in chosen]

        return _modify_and_save(args, rasterize)

    if args.command == "arrange":
        from .operations import arrange_grid

        return _modify_and_save(args, lambda board: {"moved": arrange_grid(
            board, parent_id=args.parent, gap=args.gap, sort=args.sort)})

    if args.command in ("relink", "embed", "shrink", "dedupe"):
        from . import assets, operations

        actions = {
            "relink": lambda board: assets.relink(board, args.search),
            "embed": assets.embed_linked,
            "shrink": assets.shrink_images,
            "dedupe": lambda board: {"removed": operations.deduplicate_images(board)},
        }
        return _modify_and_save(args, actions[args.command])

    with Board.load(args.input) as board:
        if args.command == "inspect":
            return board.to_dict()
        if args.command == "export-legacy":
            from .compatibility import export_legacy

            report_path = Path(args.output).with_suffix(".report.json")
            if report_path.exists() and not args.overwrite:
                raise FileExistsError(report_path)
            report = export_legacy(board, args.output, overwrite=args.overwrite,
                                   drawing_scale=args.scale)
            save_report(report_path, report, args.overwrite)
            return report
        if args.command == "export-package":
            from .manifest import export_manifest
            from .sidecar import read_sidecar

            extensions, sidecar_report = read_sidecar(board, args.input)
            report = export_manifest(board, args.directory, extensions=extensions,
                                     scene_preview=not args.no_preview)
            report["sidecar"] = sidecar_report
            return report
        if args.command == "extract-assets":
            return {"assets": board.export_assets(args.directory)}
        if args.command == "render":
            from .render import render_scene

            picture = render_scene(board, width=args.width)
            output = Path(args.output)
            if output.exists() and not args.overwrite:
                raise FileExistsError(output)
            image_format = "JPEG" if output.suffix.lower() in (".jpg", ".jpeg") else "PNG"
            picture.convert("RGB").save(output, format=image_format)
            return {"output": str(output), "size": list(picture.size)}
        if args.command == "report":
            from .report import write_board_report

            path = write_board_report(board, args.output, overwrite=args.overwrite,
                                      check_round_trip=not args.no_round_trip)
            return {"output": str(path)}
        if args.command == "split":
            from .operations import split_by_group

            paths = split_by_group(board, args.directory, overwrite=args.overwrite)
            return {"boards": [str(path) for path in paths]}
        if args.command == "search":
            from .operations import search

            return {"matches": search(board, args.query)}
        if args.command == "linked":
            from .assets import linked_images

            return {"linked": linked_images(board)}
    raise ValueError(f"Unknown command {args.command}")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = run(args)
    except (ValueError, KeyError, OSError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0
