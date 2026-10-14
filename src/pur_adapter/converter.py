"""Single-file Windows converter: GUI by default, CLI when paths are provided."""
import argparse
import json
from pathlib import Path
import queue
import sys
import threading

from .frameref import convert


def main():
    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(description='Convert PureRef .pur ↔ FrameRef .frameref')
        parser.add_argument('source')
        parser.add_argument('output', help='New folder for FrameRef, new .pur file for PureRef')
        args = parser.parse_args()
        try:
            print(json.dumps(convert(args.source, args.output), ensure_ascii=True, indent=2))
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    if sys.platform == 'win32' and getattr(sys, 'frozen', False):
        import ctypes
        ctypes.windll.kernel32.FreeConsole()
    return gui()


def gui():
    import os
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title('PureRef ↔ FrameRef')
    root.geometry('700x490')
    root.minsize(620, 400)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='PureRef ↔ FrameRef', font=('Segoe UI', 21, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='Виберіть дошку — напрямок конвертації визначиться автоматично.').pack(anchor='w', pady=(8,20))
    source = tk.StringVar()
    status = tk.StringVar(value='Підтримуються .pur та .frameref (версія формату 2).')
    row = ttk.Frame(frame)
    row.pack(fill='x')
    ttk.Entry(row, textvariable=source).pack(side='left', fill='x', expand=True, padx=(0,8))

    def browse():
        path = filedialog.askopenfilename(parent=root, title='Виберіть дошку',
                   filetypes=[('PureRef / FrameRef', '*.pur *.frameref'), ('Усі файли','*.*')])
        if path:
            source.set(path)
    browse_button = ttk.Button(row, text='Вибрати файл…', command=browse)
    browse_button.pack(side='right')
    ttk.Label(frame, textvariable=status, wraplength=640).pack(anchor='w', pady=(15,12))
    ttk.Label(frame, text='Результат збережеться окремо. Файли FrameRef потрібно переносити разом із папкою content.',
              wraplength=640).pack(anchor='w')
    log = tk.Text(frame, height=9, wrap='word', font=('Segoe UI',10), state='disabled')
    log.pack(fill='both', expand=True, pady=(16,12))
    events = queue.Queue()
    last_output = [None]

    def show_log(text):
        log.configure(state='normal')
        log.delete('1.0','end')
        log.insert('1.0',text)
        log.configure(state='disabled')

    def start():
        path = Path(source.get().strip())
        if not path.is_file() or path.suffix.lower() not in ('.pur','.frameref'):
            messagebox.showerror('Виберіть дошку', 'Потрібен існуючий файл .pur або .frameref.', parent=root)
            return
        if path.suffix.lower() == '.pur':
            parent = filedialog.askdirectory(parent=root, title='Куди зберегти папку FrameRef?')
            if not parent:
                return
            output = Path(parent) / (path.stem + '-FrameRef')
        else:
            dest = filedialog.asksaveasfilename(parent=root, title='Зберегти PureRef',
                       initialfile=path.parent.name + '-PureRef.pur', defaultextension='.pur',
                       filetypes=[('PureRef','*.pur')])
            if not dest:
                return
            output = Path(dest)
        if output.exists():
            messagebox.showerror('Файл уже існує', 'Виберіть нове ім’я або іншу папку.', parent=root)
            return
        start_button.configure(state='disabled')
        browse_button.configure(state='disabled')
        open_button.configure(state='disabled')
        status.set('Конвертація…')
        show_log('')
        def work():
            try:
                events.put(('done', convert(path, output), output))
            except Exception as exc:
                events.put(('error', str(exc), None))
        threading.Thread(target=work, daemon=True).start()

    def poll():
        try:
            kind, result, output = events.get_nowait()
        except queue.Empty:
            root.after(100,poll)
            return
        start_button.configure(state='normal')
        browse_button.configure(state='normal')
        if kind == 'error':
            status.set('Конвертація не виконана.')
            show_log(result)
        else:
            last_output[0] = output if output.is_dir() else output.parent
            open_button.configure(state='normal')
            changes = result['changes']
            status.set(f'Готово: {result["items"]} елементів. Відмінностей: {len(changes)}.')
            show_log(str(output) + '\n\n' + '\n'.join(c['message'] for c in changes)
                     if changes else str(output) + '\n\nЗображення перенесено без виявлених спрощень.')
        root.after(100,poll)

    def close():
        if str(start_button['state']) == 'disabled':
            messagebox.showinfo('Конвертація триває', 'Дочекайтеся завершення.', parent=root)
            return
        root.destroy()
    controls = ttk.Frame(frame)
    controls.pack(fill='x')
    start_button = ttk.Button(controls, text='Конвертувати', command=start)
    start_button.pack(side='left')
    open_button = ttk.Button(controls, text='Відкрити папку результату', state='disabled',
                             command=lambda: os.startfile(last_output[0]))
    open_button.pack(side='right')
    ttk.Label(frame, text='Based on PureRef Interop by Andrii Orlov (DeadSue)',
              foreground='#666666').pack(anchor='w',pady=(12,0))
    root.protocol('WM_DELETE_WINDOW',close)
    root.after(100,poll)
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

