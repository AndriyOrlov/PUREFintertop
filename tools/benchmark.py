"""Measure open / save time and peak memory against board size; draw the charts.

    python tools/benchmark.py WORK_FOLDER [--sizes 10 50 200 600 1400]

Boards of the given sizes (MB) are built from incompressible noise images in
WORK_FOLDER (deleted afterwards). Each measurement runs in a fresh process so
its peak memory is its own. Results go to docs/benchmarks.json and the charts
to docs/charts/.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE_SIDE = 2048  # one noise PNG is ~12 MB


def peak_memory_mb():
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess
        process.restype = wintypes.HANDLE
        info = ctypes.windll.psapi.GetProcessMemoryInfo
        info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        info(process(), ctypes.byref(counters), counters.cb)
        return counters.PeakWorkingSetSize / 2**20
    import resource  # pylint: disable=import-error  (Unix only)

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 2**20 if sys.platform == "darwin" else peak / 1024


def child(board_path, mode):
    """One measurement: open, read every item, edit one, save, report JSON."""
    from pur_adapter import Board

    limit = 0 if mode == "temp_file" else 64 * 1024**3
    started = time.perf_counter()
    with Board.load(board_path, in_memory_limit=limit) as board:
        count = len(board.items)
        opened = time.perf_counter()
        board.update(board.items[0].id, name="benchmark edit")
        board.save(Path(board_path).with_suffix(f".{mode}.pur"), overwrite=True,
                   refresh_preview=False)
        saved = time.perf_counter()
    print(json.dumps({"items": count, "open_s": opened - started, "save_s": saved - opened,
                      "peak_mb": peak_memory_mb()}))


def build(folder, megabytes):
    from PIL import Image
    from pur_adapter import Board

    path = folder / f"board-{megabytes}mb.pur"
    images = max(1, round(megabytes / 12))
    with Board(use_temp_file=True) as board:
        for index in range(images):
            picture = folder / "noise.png"
            Image.frombytes("RGB", (IMAGE_SIDE, IMAGE_SIDE),
                            os.urandom(IMAGE_SIDE * IMAGE_SIDE * 3)).save(picture, compress_level=1)
            board.add_image(picture, x=index * (IMAGE_SIDE + 50), name=f"noise {index}")
        board.save(path, overwrite=True, refresh_preview=False)
    return path


def measure(sizes, folder):
    results = []
    for megabytes in sizes:
        path = build(folder, megabytes)
        actual_mb = path.stat().st_size / 2**20
        for mode in ("in_memory", "temp_file"):
            if mode == "in_memory" and actual_mb > 900:
                continue  # needs several times the file size in RAM
            output = subprocess.run([sys.executable, __file__, "--child", str(path), mode],
                                    capture_output=True, text=True, check=True).stdout
            row = json.loads(output) | {"mode": mode, "file_mb": actual_mb}
            results.append(row)
            print(f"{actual_mb:8.0f} MB  {mode:10}  open {row['open_s']:6.2f}s  "
                  f"save {row['save_s']:6.2f}s  peak {row['peak_mb']:7.0f} MB", flush=True)
        for leftover in folder.glob("board-*"):
            leftover.unlink()
    return results


def draw(results):
    sys.path.insert(0, str(ROOT / "src"))
    from pur_adapter import charts

    charts_folder = ROOT / "docs/charts"
    charts_folder.mkdir(parents=True, exist_ok=True)

    def series(mode, key, color, name, dashed=False):
        points = [(row["file_mb"], row[key]) for row in results if row["mode"] == mode]
        return charts.Series(name, points, color, dashed=dashed, markers=True)

    sizes = [row["file_mb"] for row in results]
    span = (min(sizes) * 0.8, max(sizes) * 1.25)
    reference = charts.Series("file size", [(s, s) for s in span], charts.MUTED, dashed=True)
    memory = charts.line_chart(
        [reference,
         series("in_memory", "peak_mb", charts.WARM, "in memory"),
         series("temp_file", "peak_mb", charts.GOOD, "temporary file")],
        x_label="board size, MB", y_label="peak memory, MB", log_x=True, log_y=True,
        x_range=span, y_range=(10, max(row["peak_mb"] for row in results) * 2), legend="left",
    )
    (charts_folder / "memory.svg").write_text(memory, encoding="utf-8")
    speed = charts.line_chart(
        [series("in_memory", "open_s", charts.WARM, "open, in memory", dashed=True),
         series("temp_file", "open_s", charts.GOOD, "open, temporary file", dashed=True),
         series("in_memory", "save_s", charts.WARM, "edit + save, in memory"),
         series("temp_file", "save_s", charts.GOOD, "edit + save, temporary file")],
        x_label="board size, MB", y_label="seconds", log_x=True, log_y=True, x_range=span,
        y_range=(min(row["open_s"] for row in results) * 0.7,
                 max(row["save_s"] for row in results) * 2), legend="left",
    )
    (charts_folder / "speed.svg").write_text(speed, encoding="utf-8")


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--child":
        child(sys.argv[2], sys.argv[3])
        return 0
    options = argparse.ArgumentParser()
    options.add_argument("folder", type=Path)
    options.add_argument("--sizes", type=int, nargs="+", default=[10, 50, 200, 600, 1400])
    options.add_argument("--draw-only", action="store_true")
    args = options.parse_args()
    results_path = ROOT / "docs/benchmarks.json"
    if not args.draw_only:
        args.folder.mkdir(parents=True, exist_ok=True)
        try:
            results = measure(args.sizes, args.folder)
        finally:
            shutil.rmtree(args.folder, ignore_errors=True)
        machine = {"platform": sys.platform, "python": sys.version.split()[0]}
        results_path.write_text(json.dumps({"machine": machine, "results": results}, indent=2),
                                encoding="utf-8")
    draw(json.loads(results_path.read_text(encoding="utf-8"))["results"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
