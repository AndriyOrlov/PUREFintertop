"""Drive an installed PureRef through its documented command line.

Only the official commands are used (https://www.pureref.com/handbook/automation/):
`load`, `save`, `exportScene`, `clearScene` and `exit`. PureRef is never
downloaded or redistributed by this project.

Measured on Windows 2.1.3: `save` only overwrites an existing file, so the
helpers copy the board to its destination first and save it in place.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

_DEFAULT_LOCATIONS = {
    "win32": [r"C:\Program Files\PureRef\PureRef.exe",
              r"C:\Program Files (x86)\PureRef\PureRef.exe"],
    "darwin": ["/Applications/PureRef.app/Contents/MacOS/PureRef"],
}
_PROBLEM_MARKERS = ("[Error]", "[Critical]", "Could not save", "Could not open",
                    "Unknown command")


class PureRefError(RuntimeError):
    pass


def find_pureref():
    """Path of an installed PureRef, from $PUREREF, PATH or the usual folders."""
    candidates = [os.environ.get("PUREREF"), shutil.which("PureRef"), shutil.which("pureref")]
    candidates += _DEFAULT_LOCATIONS.get(sys.platform, [])
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def run(*commands, program=None, timeout=180):
    """Run PureRef with `-c` commands, then exit. Returns its log text."""
    program = program or find_pureref()
    if program is None:
        raise PureRefError("PureRef is not installed (set PUREREF to its executable)")
    arguments = [str(program)]
    for command in list(commands) + ["exit"]:
        arguments += ["-c", command]
    process = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
    log = (process.stdout or "") + (process.stderr or "")
    if process.returncode or any(marker in log for marker in _PROBLEM_MARKERS):
        raise PureRefError(f"PureRef reported a problem:\n{log}")
    return log


def warnings_in(log):
    return [line for line in log.splitlines() if "[Warning]" in line]


def resave(source, target, **options):
    """Let PureRef open `source` and save it as `target`. Returns the log."""
    target = Path(target).resolve()
    shutil.copyfile(source, target)
    return run(f"load;{target}", f"save;{target}", **options)


def render(source, png, width=1000, height=1000, **options):
    """PureRef's own picture of a board (no canvas background or borders)."""
    png = Path(png).resolve()
    log = run(f"load;{Path(source).resolve()}",
              f"exportScene;{png};{width};{height};false;false;true", **options)
    if not png.exists():
        raise PureRefError(f"PureRef did not write {png}:\n{log}")
    return log


def import_images(paths, target, *, template, **options):
    """Let PureRef build a board from image files (to see how it stores them)."""
    target = Path(target).resolve()
    shutil.copyfile(template, target)
    joined = ",".join(str(Path(path).resolve()).replace(",", ",,") for path in paths)
    return run(f"load;{target}", "clearScene", f"load;{joined}", f"save;{target}", **options)
