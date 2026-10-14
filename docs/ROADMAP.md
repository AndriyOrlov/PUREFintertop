# Roadmap status

Status of the work proposed after the 2026-10-14 review, as of version 0.3.0.

## Review findings

| Finding | Status |
|---|---|
| Dashed pen rendering could hang | Fixed; regression test with the exact polyline |
| New legacy image inherited a removed image's crop | Fixed; duplicates keep their legacy record |
| `save(format="legacy")` dropped perspective | Fixed: rejected with UnsupportedFormat |
| Group flattening changed stacking | Fixed: z renumbered in native drawing order, confirmed in PureRef |
| Manifest import dropped comments | Fixed; schema 2 keeps every readable property |
| CI always red | Fixed in 0.2 (owner) |
| Plain notes containing `<p…` parsed as HTML | Fixed |
| `NOT IN` with NULL blocked image clean-up | Fixed |
| Phone photos: `MPO` format name, EXIF size | Fixed to match native PureRef imports |
| Crop and flip depended on call order | Fixed; both orders render the same in PureRef |
| Readability | Restructured `board`, `qt`, `legacy`, `drawings`; named constants |

## Proposed features

| # | Feature | Status |
|---|---|---|
| 1 | Lossless read API (crop, flags, note/group style, drawing order, world transforms) | Done: `image_settings`, `note_style`, `group_style`, `transform_matrix`, `scene.drawing_order`, `scene.world_properties` |
| 2 | Lossless JSON exchange with schema and unresolved list | Done: manifest schema 2, `docs/manifest.schema.json`, `strict=True` |
| 3 | FrameRef media mapping (video, PDF, arrows, links, audio) | Done in the manifest importer, with a report |
| 4 | Keep FrameRef-only data | Done: sidecar file. Native PureRef deletes unknown tables, so the data cannot live inside the `.pur` |
| 5 | Thumbnail and view state | Thumbnail and `scene_rect` done. Scroll/zoom (`view_transform`) are left as stored; PureRef sets them per window |
| 6 | Large boards | Done: temporary-file database above 128 MiB, streamed checksum and writing |
| 7 | Language-neutral conformance suite | Done: `conformance/` with native resaves |
| 8 | Standalone executable | Done: PyInstaller build in the release workflow |
| 9 | Repair damaged boards | Done: checksum repair, row salvage, picture carving |
| 10 | Linked images | Done: list, relink (checksum-verified when possible), embed |
| 11 | Batch operations | Done: merge, split by group, de-duplicate, arrange, search |
| 12 | Lossless PNG shrinking | Done |
| 13 | Shapes from PureRef 2.1 | Covered: rectangles and ellipses are stored as pen paths in the measured styles |

## Still open

- Tests on Andrii's real boards (`docs/REAL_BOARD_TEST_PLAN.md`).
- macOS native check.
- The meaning of PureRef's rewritten sort keys.
- A Qt-accurate renderer, if a host needs pixel-exact previews; FrameRef can
  use its own renderer with the exported data instead.
- Agreement with Polygonflow on the host API.
