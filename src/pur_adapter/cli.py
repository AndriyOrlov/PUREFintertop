"""Small JSON-friendly command line interface for scripts and integrations."""

import argparse
import json
from pathlib import Path
import sys
from .board import Board, atomic_write
from .compatibility import export_legacy
from .drawings import rasterize_drawing
from .manifest import export_manifest, import_manifest


def save_report(path, report, overwrite=False):
    atomic_write(
        path,
        json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"),
        overwrite,
    )


def parser():
    root = argparse.ArgumentParser(
        description="Read, create and edit PureRef .pur boards"
    )
    commands = root.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Print a board as JSON")
    inspect.add_argument("input")
    create = commands.add_parser(
        "create", help="Create a board from a neutral JSON manifest"
    )
    create.add_argument("manifest")
    create.add_argument("output")
    create.add_argument("--format", choices=("modern", "legacy"), default="modern")
    create.add_argument("--overwrite", action="store_true")
    patch = commands.add_parser("patch", help="Edit one item and save a separate board")
    patch.add_argument("input")
    patch.add_argument("output")
    patch.add_argument("--item", type=int, required=True)
    patch.add_argument(
        "--set",
        dest="properties",
        required=True,
        help='JSON object, e.g. {"x":100,"rotation":20}',
    )
    patch.add_argument("--overwrite", action="store_true")
    raster = commands.add_parser(
        "rasterize", help="Convert freehand pen drawings to positioned PNG images"
    )
    raster.add_argument("input")
    raster.add_argument("output")
    selection = raster.add_mutually_exclusive_group(required=True)
    selection.add_argument("--item", type=int)
    selection.add_argument("--all", action="store_true")
    raster.add_argument("--scale", type=float, default=4)
    raster.add_argument("--overwrite", action="store_true")
    legacy = commands.add_parser(
        "export-legacy", help="Flatten groups and export a compatibility board"
    )
    legacy.add_argument("input")
    legacy.add_argument("output")
    legacy.add_argument("--scale", type=float, default=4)
    legacy.add_argument("--overwrite", action="store_true")
    package = commands.add_parser(
        "export-package", help="Export neutral JSON and portable image assets"
    )
    package.add_argument("input")
    package.add_argument("directory")
    assets = commands.add_parser(
        "extract-assets", help="Extract embedded original image bytes"
    )
    assets.add_argument("input")
    assets.add_argument("directory")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "create":
            board, report = import_manifest(args.manifest)
            with board:
                if args.format == "legacy":
                    report["compatibility"] = export_legacy(
                        board, args.output, overwrite=args.overwrite
                    )
                else:
                    board.save(args.output, overwrite=args.overwrite)
            print(
                json.dumps(
                    {"output": args.output, "report": report}, ensure_ascii=False
                )
            )
            return 0
        with Board.load(args.input) as board:
            if args.command == "inspect":
                report = board.to_dict()
            elif args.command == "patch":
                properties = json.loads(args.properties)
                if not isinstance(properties, dict):
                    raise ValueError("--set must be a JSON object")
                board.update(args.item, **properties)
                board.save(args.output, overwrite=args.overwrite)
                report = {"output": args.output, "item": args.item}
            elif args.command == "rasterize":
                selection = (
                    [item.id for item in board.items if item.kind == "drawing"]
                    if args.all
                    else [args.item]
                )
                changes = [
                    rasterize_drawing(board, item_id, scale=args.scale)
                    for item_id in selection
                ]
                board.save(args.output, overwrite=args.overwrite)
                report = {"output": args.output, "changes": changes}
            elif args.command == "export-legacy":
                report_path = Path(args.output).with_suffix(".report.json")
                if report_path.exists() and not args.overwrite:
                    raise FileExistsError(report_path)
                report = export_legacy(
                    board,
                    args.output,
                    overwrite=args.overwrite,
                    drawing_scale=args.scale,
                )
                save_report(report_path, report, args.overwrite)
            elif args.command == "export-package":
                report = export_manifest(board, args.directory)
            else:
                report = {"assets": board.export_assets(args.directory)}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, OSError) as exc:
        print(
            json.dumps(
                {"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False
            ),
            file=sys.stderr,
        )
        return 1
