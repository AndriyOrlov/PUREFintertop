# Binary and native-file research

Official Linux builds were downloaded and extracted into separate version
folders. The application executables were inspected with readelf, nm, strings
and objdump, then targeted functions were decompiled with Ghidra 11.0.3.

| Application release | Targeted functions decompiled | File layout observed |
|---|---:|---|
| 1.10.4 | 15 | Legacy 1.10 |
| 1.11.1 | 12 | Legacy 1.10 |
| 2.0.3 | 11 | Modern 2.1 + SQLite |
| 2.1.3 | 11 | Modern 2.1 + SQLite |

The 1.x binaries expose little application naming; targets were selected through
file-format string references. The 2.x dynamic symbols identify SaveFileLoader,
SaveFile, SceneSerializerSqlite and GraphicsDrawItem functions. These are focused
interoperability analyses, not full reconstructions of the applications.

`binary-analysis.json` records executable SHA-256, byte size, function addresses,
selection markers and completion status. `tools/research/InspectPurRef.py` is the
research script. Raw application binaries and recovered proprietary C listings
are not included in the project.

Confirmed mechanisms are described in `docs/FORMAT.md` and exercised by native
fixtures. The stroke writer/reader analysis confirmed the marker/color/width/path/
extra-point/style order used by `drawings.py`.

The Linux virtual display used Xvfb over loopback TCP because the environment did
not permit its usual Unix sockets. AppImage contents were extracted to avoid a
FUSE mount dependency. Old first-run dialogs were completed in the test display.

Native CLI caveat: the tested modern builds could export loaded boards, but saving
a loaded board to a brand-new destination returned an error even with exit code 0.
Pre-copying the board to the destination and re-saving that same path succeeded.
A normal GUI Save As also succeeded. Verify output and log errors, not just exit
status. The delivered direct-file API does not depend on this CLI behavior.
