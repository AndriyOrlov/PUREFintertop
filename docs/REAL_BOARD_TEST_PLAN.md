# Testing with real boards

Always test **copies**. Native tests have passed on Linux (1.10.4–2.1.3) and on
Windows with PureRef 2.1.3 (see `NATIVE_WINDOWS.md`). Real boards come next.

1. Pick 3–5 boards: a simple one; a large one; one with groups; one with notes
   and pen drawings; and separately boards with GIFs, cropped, mirrored or
   linked (not embedded) images, and phone photos.
2. Keep the original copies and a screenshot of each board in PureRef.
3. Install the package as described in the README (or use `pur-adapter.exe`).
   The commands below use Python from `.venv`; with the `.exe`, replace the
   start of each line with `pur-adapter.exe`.

```powershell
& .\.venv\Scripts\python.exe -X utf8 -m pur_adapter inspect "C:\Refs\copy.pur"
& .\.venv\Scripts\python.exe -X utf8 -m pur_adapter render "C:\Refs\copy.pur" "C:\Refs\preview.png"
& .\.venv\Scripts\python.exe -X utf8 -m pur_adapter export-package "C:\Refs\copy.pur" "C:\Refs\package-test"
& .\.venv\Scripts\python.exe -X utf8 -m pur_adapter create "C:\Refs\package-test\board.json" "C:\Refs\roundtrip.pur"
& .\.venv\Scripts\python.exe -X utf8 -m pur_adapter export-legacy "C:\Refs\copy.pur" "C:\Refs\simplified.pur"
& .\.venv\Scripts\python.exe -X utf8 -m pur_adapter linked "C:\Refs\copy.pur"
```

4. Open `roundtrip.pur` in PureRef and compare it with the original: position,
   rotation, scale, crop, mirroring, layer order, notes (text and colour),
   groups (colour), comments, opacity. There should be no differences at all.
5. Open `simplified.pur` (the 1.x simplification): groups are flattened, pen
   drawings became images, the layer order is unchanged. Check the changes
   against the `.report.json` report.
6. Look at `preview.png`. It is an approximate render (fonts differ), but every
   item should be in its place.
7. Save the board again in PureRef itself and read it with the adapter
   (`inspect`). Boards carrying FrameRef data have a `*.pur.host.json` next to them.
8. For a damaged file (if you have one): `repair broken.pur recovered-folder`.
9. Record the PureRef version, file size, item count, time, memory and result
   in `examples/real-board-results.csv`. Add screenshots of any differences.

Criteria: the source is unchanged; the output opens; every item is accounted
for; positions and transforms match; nothing is lost silently; the report
matches the changes. Pen antialiasing differences are acceptable only if they
are visible and agreed.

After your check: fix what was found and repeat the tests.
