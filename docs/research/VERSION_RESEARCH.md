# File evolution and compatibility forecast

Scope: native files and targeted binary analysis for 1.10.4, 1.11.1, 2.0.3 and
2.1.3. This is a measured subset, not a complete reconstruction of every
application subsystem. See `binary-analysis.json` for executable hashes and
targeted decompilation addresses. Proprietary executables and decompiled source
are not redistributed.

## Two container families

| Release | File identifier | Container | Release field |
|---|---|---|---|
| 1.10.4 | 1.10 | Record stream, 224-byte measured header | QString at offset 24 |
| 1.11.1 | 1.10 | Same measured header and reference table | QString at offset 24 |
| 2.0.3 | 2.1 | Relocated SQLite | Dynamic QString in prefix |
| 2.1.3 | 2.1 | Relocated SQLite | Dynamic QString in prefix |

The identifier is a file format version, not the application release.

### Legacy header

All offsets below apply to the observed six-character release names. A different
release-field length must be handled by a new codec rather than fixed offsets.

| Offset | Bytes | Meaning |
|---|---:|---|
| 0 | 12 | QString: 1.10 |
| 12 | 2 | Total item count, big endian |
| 14 | 2 | Image count, big endian |
| 16 | 8 | Reference-table offset |
| 24 | 16 | QString release: length 12 plus UTF-16 BE text |
| 40 | 68 | QString MD5 of bytes from offset 108 onward |
| 108 | 4 | Next item ID |
| 112 | 32 | Canvas rectangle: four doubles |
| 144 | 72 | View transform: nine doubles |
| 216 | 8 | Scroll: two integers |
| 224 | Variable | Resources and scene records |

Each measured reference-table entry is 20 bytes: ID u32, start u64, end u64.
The vendored writer leaves the release text empty; this does not identify a
native application release. Legacy decoding still uses heuristic PNG record
discovery and does not cover every possible native record tail.

### Modern container

Read in order: QString format, flags u32, SQLite size S u64, QString application
release, QString MD5, QByteArray JPEG thumbnail. Let H be the resulting header
length. The file length is S + H. Recover SQLite as `data[S:] + data[H:S]`.
MD5 covers all bytes after its QString, including the thumbnail length and data.
The thumbnail length is at offset 104 only for the observed five-character
release names; the decoder uses dynamic offsets.

Both measured 2.x releases use seven tables: `images`, `items`, `items_images`,
`items_notes`, `items_groups`, `items_drawings`, `metadata`. Qt variants appear
as Latin-1 TEXT even in columns with BLOB affinity. Item comments contain TEXT
despite their INTEGER column affinity; they are not IDs of note records.

QTransform: type 80, null byte, nine big-endian doubles. Compose row vectors as
local @ parent. QSizeF is type 22; fixed-size notes are positioned by their
center. QRectF is type 20. User types use marker 1024 plus their type name.

BigRational ordering cannot be reduced to arbitrary byte sorting. Generated
positive integer ranks use the measured 53-byte form; fractional or unknown
encodings must remain opaque unless a compatible comparator is implemented.

Drawing marker 100 is a version marker. Each measured stroke includes QColor,
width, painter-path elements, current-start/fill-rule metadata, an extra point
and style. Styles measured in 2.1.3: 0 solid, 1 dashed, 2 arrow. Rectangles and
ellipses are path geometry. Pillow rasterization approximates Qt antialiasing;
it is not a promise of identical native pixels.

## Forecast, not a guarantee

| Component | Expected persistence | Reason and failure signal |
|---|---|---|
| Image/note semantics and affine placement | High | Shared across both families; new geometry could need extensions |
| Resource identity and hierarchy | High | Necessary scene concepts; native representation may change |
| 2.x SQLite container and table model | Medium | Shared by two measured releases; format ID, flags or schema can change |
| Qt metatype serialization | Medium | Qt-dependent; stream version or custom payload may change |
| Drawing styles, image flags, playback | Low | New features can add values and different rendering rules |
| Thumbnail, UI/view state, metadata | Low | Not required for scene interchange; avoid treating as core |

For a new release: collect native fixtures; map headers and integrity ranges;
compare exact SQL and QVariant payloads; verify byte-identical no-op saving;
verify edits in the native app; then run the 1000-image shuffle/restore and
compare native renders. Only after passing may a release enter the tested list.
Recognized but untested releases are reported as `unverified_release`.

Remaining work includes real user boards, broader legacy record coverage,
linked-resource policy, unknown painter-path styles and a native FrameRef
integration. A portable manifest bridge is not a completed FrameRef plugin.
