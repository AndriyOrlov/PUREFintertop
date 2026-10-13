# PUR Adapter experiment

Objective: a local Python API and CLI to create, read and edit PureRef boards,
with empirical compatibility checks in official PureRef versions.

Target versions exposed by official download page: 2.1.3, 2.0.3, 1.11.1, 1.10.4.
Runtime: Python 3.12, Pillow; unittest; official Linux builds in isolated local
version directories and per-run config directories. No PureRef binaries bundled.

Commands: `python -m unittest discover -s tests -v`; `python -m pur_adapter --help`.
Structure: src/pur_adapter for public API and codecs, tests for contract tests,
fixtures for official application output, docs for format and compatibility.

Public contract: Board.load(path), Board(), add_image(path,x,y), add_note(text,x,y),
update(id,**properties), remove(id), save(path), to_dict(), export_assets(directory).
Expose native properties and retain unsupported byte sections when possible.
Reject unsupported edits explicitly; never claim full compatibility from a
successful synthetic round trip alone. Save must be atomic; overwrite opt-in.

Style: snake_case; dataclasses; explicit exceptions; subprocess argument lists.
Example: `board.add_image('photo.png', x=100, y=200)`.

Testing: malformed input, create/load/edit, Unicode, transforms, duplicates,
image-byte preservation, unsupported fields, native PureRef load/export/save.
Native checks must record application version, output, and limitations.

Boundaries: use official downloads and published CLI; inspect generated document
bytes, not application internals. Preserve original inputs. No credentials,
remote deployment, paid purchase, or binary redistribution. Ordinary dependency
installation and local experiments are authorized by the user request.

Success: runnable adapter, tests, native version matrix, sample boards and a
clear account of any unsupported modern-format features.
