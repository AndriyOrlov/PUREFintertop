"""PureRef <-> FrameRef converter: a window by default, a command line with paths.

    PureRef-FrameRef-Converter.exe                      opens the window
    PureRef-FrameRef-Converter.exe board.pur            opens it with that board
    PureRef-FrameRef-Converter.exe input output         converts without a window

The window follows the look of the author's Windows 11 calibration apps: dark
Fluent theme (Sun Valley ttk), accent blue, and green / yellow / red results.
"""

import argparse
import json
import os
import queue
import sys
import threading
from collections import Counter
from pathlib import Path

from .frameref import convert

ACCENT = "#60cdff"
GOOD = "#6ccb5f"
WARM = "#ffc83d"
BAD = "#ff6b6b"
MUTED = "#9a9a9a"
PAGE = "#1c1c1c"  # Sun Valley dark background
BOARD_SUFFIXES = (".pur", ".frameref")

# --- Words on screen ---------------------------------------------------------------

TEXTS = {
    "subtitle": "Pick a board — the direction is chosen automatically.",
    "board": "Board",
    "choose": "Choose file…",
    "nothing": "No board selected. Drop a .pur or main.frameref on the program icon, "
               "or choose a file.",
    "save_to": "Save to",
    "change": "Change…",
    "convert": "Convert",
    "converting": "Converting…",
    "open_result": "Show result",
    "open_report": "Open report",
    "ready": "Converted {items} items without simplifications.",
    "ready_changes": "Converted {items} items. Simplifications:",
    "failed": "Not converted. The original is unchanged.",
    "wait": "Conversion is still running.",
    "missing": "Choose an existing .pur or .frameref file.",
    "pureref_board": "PureRef board",
    "frameref_board": "FrameRef board",
    "folder_note": "FrameRef boards are folders: keep main.frameref and content/ together.",
    "credit": "Based on PureRef Interop by Andrii Orlov (DeadSue) · Apache-2.0",
}

# Report codes from frameref.py in short words. Unknown codes show the
# message from the report itself.
CHANGE_TEXTS = {
    "group_flattened": "Groups flattened; items keep their places",
    "crop_baked": "Crops or animation frames saved as PNG",
    "animation_state_lost": "Animation speed and playback are not transferred",
    "text_layout": "Note text may wrap differently",
    "drawing_rasterized": "Pen drawings became images",
    "comment_lost": "PureRef comments are not transferred",
    "swatch_to_image": "Colour swatches became images",
    "connector_to_curve": "Connections became static curves",
    "frameref_extras_lost": "FrameRef tags, ratings, borders and overlays are not transferred",
}

# Known reasons a conversion stops: a fragment of the error, then plain words.
ERROR_TEXTS = [
    ("Perspective", "The board uses a perspective distortion, which FrameRef does not have."),
    ("Embed linked", "Some images only link to files. Embed them first: pur-adapter embed."),
    ("is not supported; no output",
     "The FrameRef board has video, PDF, portals or drawings that PureRef cannot hold."),
    ("zoom/pan or cropOffset",
     "A FrameRef image uses its own zoom or offset, which is not supported yet."),
    ("Fractional-pixel crops", "Crops with fractional pixels are not supported yet."),
    ("Non-rectangular crops", "Non-rectangular crops are not supported yet."),
    ("FileExistsError", "That file or folder already exists; choose another name."),
    ("file_version", "This FrameRef file version is not supported yet."),
]


def explain_error(message):
    for fragment, explanation in ERROR_TEXTS:
        if fragment in message:
            return explanation
    return None


def summarize_changes(changes):
    """[(text, count)] with identical simplifications grouped together."""
    counts = Counter(change.get("code") or change["message"] for change in changes)
    messages = {change.get("code") or change["message"]: change["message"] for change in changes}
    return [(CHANGE_TEXTS.get(code, messages[code]), count)
            for code, count in counts.most_common()]


def unique_path(path):
    """`path`, or `name (2)`, `name (3)`… if that name is already taken."""
    path = Path(path)
    candidate, number = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
        number += 1
    return candidate


def default_output(source):
    """A new output path next to the board that does not exist yet."""
    source = Path(source)
    if source.suffix.lower() == ".pur":
        return unique_path(source.parent / (source.stem + "-FrameRef"))
    # main.frameref lives inside the board's own folder
    return unique_path(source.parent.parent / (source.parent.name + "-PureRef.pur"))


def _file_size(size):
    if size >= 2**30:
        return f"{size / 2**30:.2f} GB"
    if size >= 2**20:
        return f"{size / 2**20:.1f} MB"
    return f"{max(1, round(size / 1024))} KB"


def _short_path(path, fits):
    """Keep the start and the end of a long path, with "…" in the middle.

    `fits(text)` says whether a candidate is narrow enough to show.
    """
    text = str(path)
    keep = len(text)
    while keep > 10 and not fits(text):
        keep -= 2
        head = keep // 3
        text = str(path)[:head] + "…" + str(path)[-(keep - head):]
    return text


def report_path(output):
    output = Path(output)
    if output.suffix.lower() == ".pur":
        return output.with_name(output.name + ".conversion.json")
    return output / "conversion.json"


# --- Windows integration -----------------------------------------------------------


def _attach_parent_console():
    """The EXE has no console of its own; print into the one it was started from."""
    if sys.platform != "win32" or sys.stdout is not None:
        return  # already has somewhere to write (a console, a pipe or a file)
    import ctypes

    attach_parent_process = -1
    if ctypes.windll.kernel32.AttachConsole(attach_parent_process):
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")


def _sharp_text_on_scaled_screens():
    if sys.platform == "win32":
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system DPI aware
        except (AttributeError, OSError):
            pass


def _dark_title_bar(window):
    if sys.platform != "win32":
        return
    import ctypes

    window.update_idletasks()
    handle = ctypes.windll.user32.GetParent(window.winfo_id())
    use_dark_mode = ctypes.c_int(1)
    immersive_dark_mode = 20  # DWMWA_USE_IMMERSIVE_DARK_MODE
    ctypes.windll.dwmapi.DwmSetWindowAttribute(handle, immersive_dark_mode,
                                               ctypes.byref(use_dark_mode),
                                               ctypes.sizeof(use_dark_mode))


def _resource(name):
    """A file bundled next to the code (PyInstaller unpacks into _MEIPASS)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2] / "packaging"))
    return base / name


def _open_with_default_app(path):
    """Open a file or folder the way a double-click would, on any system."""
    import subprocess

    if sys.platform == "win32":
        # os.startfile exists only on Windows; pylint on Linux cannot see it.
        os.startfile(path)  # pylint: disable=no-member
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


def _show_in_explorer(path):
    path = Path(path)
    if sys.platform == "win32":
        import subprocess

        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        _open_with_default_app(path.parent)


# --- Window ------------------------------------------------------------------------


class ConverterWindow:
    def __init__(self, initial_board=None):
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk = tk, ttk
        self.words = TEXTS
        self.events = queue.Queue()
        self.busy = False
        self.last_output = None

        _sharp_text_on_scaled_screens()
        self.root = tk.Tk()
        self.root.title("PureRef ⇄ FrameRef")
        self.root.geometry("760x700")
        self.root.minsize(640, 600)
        icon = _resource("converter.ico")
        if icon.exists():
            self.root.iconbitmap(default=str(icon))
        self._apply_theme()
        _dark_title_bar(self.root)

        self.source = tk.StringVar()
        self.output = tk.StringVar()
        self._build()
        if initial_board:
            self._select(initial_board)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._poll)

    def _apply_theme(self):
        try:
            import sv_ttk

            sv_ttk.set_theme("dark")
        except ImportError:  # still usable with the plain theme
            pass
        style = self.ttk.Style()
        self.background = style.lookup("TFrame", "background") or PAGE
        self.root.configure(background=self.background)
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 22))
        style.configure("Muted.TLabel", foreground=MUTED, font=("Segoe UI", 10))
        style.configure("Heading.TLabel", font=("Segoe UI Semibold", 12))
        style.configure("Board.TLabel", font=("Segoe UI Semibold", 13))
        style.configure("Direction.TLabel", font=("Segoe UI Semibold", 13), foreground=ACCENT)
        style.configure("Status.TLabel", font=("Segoe UI Semibold", 12))

    def _card(self, parent, **pack):
        card = self.ttk.Frame(parent, style="Card.TFrame", padding=(18, 14))
        card.pack(fill="x", **pack)
        return card

    def _build(self):
        tk, ttk, words = self.tk, self.ttk, self.words
        page = ttk.Frame(self.root, padding=(28, 22, 28, 16))
        page.pack(fill="both", expand=True)

        # Packed first so it always keeps its place at the bottom.
        ttk.Label(page, text=words["credit"], style="Muted.TLabel").pack(
            side="bottom", anchor="w", pady=(12, 0))

        header = ttk.Frame(page)
        header.pack(fill="x")
        logo = _resource("converter.png")
        if logo.exists():
            self.logo = tk.PhotoImage(file=str(logo)).subsample(5)
            ttk.Label(header, image=self.logo).pack(side="left", padx=(0, 14))
        titles = ttk.Frame(header)
        titles.pack(side="left", fill="x")
        ttk.Label(titles, text="PureRef ⇄ FrameRef", style="Title.TLabel").pack(anchor="w")
        ttk.Label(titles, text=words["subtitle"], style="Muted.TLabel").pack(anchor="w")

        board_card = self.board_card = self._card(page, pady=(20, 0))
        top = ttk.Frame(board_card)
        top.pack(fill="x")
        ttk.Label(top, text=words["board"], style="Heading.TLabel").pack(side="left")
        self.choose_button = ttk.Button(top, text=words["choose"], command=self._browse)
        self.choose_button.pack(side="right")
        self.board_label = ttk.Label(board_card, text=words["nothing"], style="Muted.TLabel",
                                     wraplength=660, justify="left")
        self.board_label.pack(anchor="w", pady=(10, 0))
        self.path_label = ttk.Label(board_card, text="", style="Muted.TLabel")
        self.path_label.pack(anchor="w")
        self.direction_label = ttk.Label(board_card, text="", style="Direction.TLabel")
        self.direction_label.pack(anchor="w", pady=(4, 0))

        output_card = self._card(page, pady=(12, 0))
        top = ttk.Frame(output_card)
        top.pack(fill="x")
        ttk.Label(top, text=words["save_to"], style="Heading.TLabel").pack(side="left")
        self.change_button = ttk.Button(top, text=words["change"], command=self._choose_output,
                                        state="disabled")
        self.change_button.pack(side="right")
        ttk.Entry(output_card, textvariable=self.output, state="readonly").pack(
            fill="x", pady=(10, 4))
        ttk.Label(output_card, text=words["folder_note"], style="Muted.TLabel",
                  wraplength=660).pack(anchor="w")

        actions = ttk.Frame(page)
        actions.pack(fill="x", pady=(16, 0))
        self.convert_button = ttk.Button(actions, text=words["convert"], style="Accent.TButton",
                                         command=self._start, state="disabled")
        self.convert_button.pack(side="left", ipadx=18)
        self.progress = ttk.Progressbar(actions, mode="indeterminate", length=180)
        self.report_button = ttk.Button(actions, text=words["open_report"], state="disabled",
                                        command=self._open_report)
        self.report_button.pack(side="right")
        self.result_button = ttk.Button(actions, text=words["open_result"], state="disabled",
                                        command=lambda: _show_in_explorer(self.last_output))
        self.result_button.pack(side="right", padx=(0, 8))

        result_card = self.ttk.Frame(page, style="Card.TFrame", padding=(18, 14))
        result_card.pack(fill="both", expand=True, pady=(16, 0))
        self.status = ttk.Label(result_card, text="", style="Status.TLabel",
                                wraplength=660, justify="left")
        self.status.pack(anchor="w")
        card_background = self.background  # Fluent cards share the page colour
        self.details = tk.Text(result_card, height=6, wrap="word", relief="flat",
                               borderwidth=0, highlightthickness=0, font=("Segoe UI", 10),
                               background=card_background, foreground="#e6e6e6",
                               state="disabled", cursor="arrow")
        self.details.pack(fill="both", expand=True, pady=(8, 0))
        for tag, colour in (("good", GOOD), ("warm", WARM), ("bad", BAD), ("muted", MUTED)):
            self.details.tag_configure(tag, foreground=colour)

    # Choosing files ----------------------------------------------------------------

    def _browse(self):
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            parent=self.root, title=self.words["choose"],
            filetypes=[("PureRef / FrameRef", "*.pur *.frameref"), ("*", "*.*")])
        if path:
            self._select(path)

    def _select(self, path):
        path = Path(path)
        if not path.is_file() or path.suffix.lower() not in BOARD_SUFFIXES:
            self._show_status(self.words["missing"], BAD, [])
            return
        self.source.set(str(path))
        is_pureref = path.suffix.lower() == ".pur"
        kind = self.words["pureref_board"] if is_pureref else self.words["frameref_board"]
        size = path.stat().st_size if is_pureref else sum(
            f.stat().st_size for f in path.parent.rglob("*") if f.is_file())
        self.board_label.configure(text=f"{kind} · {path.name} · {_file_size(size)}",
                                   style="Board.TLabel")
        from tkinter import font

        path_font = font.Font(font=("Segoe UI", 10))
        self.root.update_idletasks()
        card_padding = 2 * 18 + 8
        available = max(200, self.board_card.winfo_width() - card_padding)
        self.path_label.configure(
            text=_short_path(path, lambda text: path_font.measure(text) <= available))
        self.direction_label.configure(
            text="PureRef  →  FrameRef" if is_pureref else "FrameRef  →  PureRef")
        self.output.set(str(default_output(path)))
        self.change_button.configure(state="normal")
        self.convert_button.configure(state="normal")
        self._show_status("", MUTED, [])

    def _choose_output(self):
        from tkinter import filedialog

        source = Path(self.source.get())
        if source.suffix.lower() == ".pur":
            folder = filedialog.askdirectory(parent=self.root, title=self.words["save_to"])
            if folder:
                self.output.set(str(unique_path(Path(folder) / (source.stem + "-FrameRef"))))
        else:
            target = filedialog.asksaveasfilename(
                parent=self.root, title=self.words["save_to"], defaultextension=".pur",
                initialfile=Path(self.output.get()).name, filetypes=[("PureRef", "*.pur")])
            if target:
                self.output.set(target)

    # Converting --------------------------------------------------------------------

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for button in (self.choose_button, self.change_button, self.convert_button):
            button.configure(state=state)
        if busy:
            self.progress.pack(side="left", padx=14)
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()

    def _start(self):
        source = Path(self.source.get())
        output = unique_path(Path(self.output.get()))  # never overwrite anything
        self.output.set(str(output))
        self._set_busy(True)
        self.result_button.configure(state="disabled")
        self.report_button.configure(state="disabled")
        self._show_status(self.words["converting"], ACCENT, [])

        def work():
            try:
                self.events.put(("done", convert(source, output), output))
            except Exception as problem:  # shown to the user, original untouched
                self.events.put(("error", f"{type(problem).__name__}: {problem}", None))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            kind, result, output = self.events.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll)
            return
        self._set_busy(False)
        if kind == "error":
            explanation = explain_error(result)
            lines = [(explanation, None)] if explanation else []
            self._show_status(self.words["failed"], BAD, lines + [(result, "technical")])
        else:
            self.last_output = output
            self.result_button.configure(state="normal")
            self.report_button.configure(state="normal")
            summary = summarize_changes(result["changes"])
            if summary:
                self._show_status(self.words["ready_changes"].format(items=result["items"]),
                                  WARM, summary)
            else:
                self._show_status(self.words["ready"].format(items=result["items"]), GOOD, [])
            self.output.set(str(default_output(self.source.get())))  # ready for another run
        self.root.after(100, self._poll)

    def _show_status(self, text, colour, lines):
        self.status.configure(text=text, foreground=colour)
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        for line, count in lines:
            if count is None:  # a reason the conversion stopped
                self.details.insert("end", line + "\n", "bad")
                continue
            if count == "technical":  # the raw error, for a bug report
                self.details.insert("end", "\n" + line + "\n", "muted")
                continue
            self.details.insert("end", "●  ", "warm")
            self.details.insert("end", line)
            if count > 1:
                self.details.insert("end", f"   ×{count}", "muted")
            self.details.insert("end", "\n")
        self.details.configure(state="disabled")

    def _open_report(self):
        path = report_path(self.last_output)
        if path.exists():
            _open_with_default_app(path)

    def _close(self):
        if self.busy:
            from tkinter import messagebox

            messagebox.showinfo("PureRef ⇄ FrameRef", self.words["wait"], parent=self.root)
            return
        self.root.destroy()

    def run(self):
        self.root.mainloop()
        return 0


# --- Entry point -------------------------------------------------------------------


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) == 1 and Path(arguments[0]).suffix.lower() in BOARD_SUFFIXES:
        return ConverterWindow(arguments[0]).run()  # a board dropped on the EXE
    if arguments:
        _attach_parent_console()
        parser = argparse.ArgumentParser(description="Convert PureRef .pur <-> FrameRef .frameref")
        parser.add_argument("source")
        parser.add_argument("output", help="New folder for FrameRef, new .pur file for PureRef")
        args = parser.parse_args(arguments)
        try:
            # ASCII escapes: a Windows console may not be able to show "→".
            print(json.dumps(convert(args.source, args.output), ensure_ascii=True, indent=2))
        except Exception as problem:  # command line: message and exit code
            print(f"{type(problem).__name__}: {problem}", file=sys.stderr)
            return 1
        return 0
    return ConverterWindow().run()


if __name__ == "__main__":
    raise SystemExit(main())
