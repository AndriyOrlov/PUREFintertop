"""Run the acceptance boards through an installed official PureRef executable.

Linux requires a working X display and the executable's Qt library paths.
This script does not download or redistribute PureRef.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from PIL import Image, ImageChops
from pur_adapter import Board
from pur_adapter.stress import verify_numbered_board


def verify(program, release, boards, output, timeout=120):
    program, boards, output = (
        Path(program).resolve(),
        Path(boards).resolve(),
        Path(output).resolve(),
    )
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for case in ("ordered", "shuffled", "restored"):
        path = output / f"{case}-{release}.png"
        if path.exists():
            raise FileExistsError(path)
        commands = [
            "load;" + str(boards / f"{case}.pur"),
            f"exportScene;{path};4096;1850;true;true;true",
            "exit",
        ]
        args = [str(program)]
        for command in commands:
            args.extend(["-c", command])
        process = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        log = process.stdout + process.stderr
        (output / f"{case}-{release}.log").write_text(log)
        if (
            process.returncode
            or any(
                marker in log
                for marker in (
                    "[Error]",
                    "Could not save",
                    "Could not open",
                    "Unknown command",
                )
            )
            or not path.exists()
        ):
            raise RuntimeError(
                f"PureRef {release} failed {case}; inspect the native log"
            )
        with Image.open(path) as image:
            results.append(
                dict(
                    case=case,
                    file=path.name,
                    size=list(image.size),
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
    with (
        Image.open(output / f"ordered-{release}.png") as before,
        Image.open(output / f"restored-{release}.png") as after,
    ):
        identical = (
            before.size == after.size
            and ImageChops.difference(
                before.convert("RGB"), after.convert("RGB")
            ).getbbox()
            is None
        )
    if not identical:
        raise AssertionError("Native ordered and restored render pixels differ")
    saved = output / f"native-resaved-{release}.pur"
    if saved.exists():
        raise FileExistsError(saved)
    shutil.copyfile(boards / "restored.pur", saved)
    original = saved.read_bytes()
    process = subprocess.run(
        [
            str(program),
            "-c",
            "load;" + str(saved),
            "-c",
            "save;" + str(saved),
            "-c",
            "exit",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    log = process.stdout + process.stderr
    (output / f"resave-{release}.log").write_text(log)
    if process.returncode or "[Error]" in log or saved.read_bytes() == original:
        raise RuntimeError("Native resave failed or did not write a native file")
    with Board.load(saved) as board:
        counts = verify_numbered_board(board)
        if board.application_version != release:
            raise AssertionError("Resaved file has an unexpected native release")
    report = dict(
        release=release,
        native_ordered_restored_pixels_identical=identical,
        exports=results,
        native_resaved_counts=counts,
        native_resaved_sha256=hashlib.sha256(saved.read_bytes()).hexdigest(),
    )
    (output / f"native-result-{release}.json").write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--program", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--boards", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    print(
        json.dumps(
            verify(args.program, args.release, args.boards, args.output, args.timeout),
            indent=2,
        )
    )
