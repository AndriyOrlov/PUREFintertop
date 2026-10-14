# Changelog

## Unreleased — 2026-10-14

- Add a single Windows EXE with a file picker for PureRef ↔ native FrameRef
  file_version 2 conversion, separate outputs and conversion reports.
- Correct top-left/centred origins and preserve affine world placement, image
  payloads, grayscale, opacity and stacking. Document all simplifications.
- Fix conversion of native PureRef notes whose text colour is NULL: use white
  as the default, matching the preview renderer.
- Verify the EXE with a real FrameRef board (30 items) and its PureRef version
  (32 items), and convert all nine repository fixtures.
- 105 automated tests, including six converter tests. Opening the generated
  boards in the native applications remains unverified.
- Build and attach the converter alongside the existing command-line EXE in
  the release workflow.

## 0.3.0 — 2026-10-14

- Fix the review findings: dashed-pen hang, legacy crop inherited through
  reused IDs, perspective dropped by legacy save, stacking changed by group
  flattening, comments lost by manifest import, NULL-blocked image clean-up,
  false rich-text detection, crop/flip order dependence.
- Match native PureRef 2.1.3 (Windows) for image format names, EXIF photo
  sizes, stacking rules, group margins, note sizes and sort keys.
- Readability: named constants, smaller functions, documented encodings.
- Large boards through a temporary SQLite file (1.41 GiB with a 37 MB peak).
- Rendering, thumbnails and scene rectangle on save.
- Manifest schema 2 (lossless) with JSON Schema, FrameRef media mapping and a
  sidecar file for host-only data.
- Repair of damaged boards; linked-image relink/embed; lossless PNG shrinking;
  merge, split, de-duplicate, arrange, search; new CLI commands.
- Language-neutral conformance suite with PureRef resaves.
- Standalone Windows executable built by the release workflow.
- Charts and an HTML board report in the style of HDRDisplayCalibrate; a
  benchmark with time and memory charts. The report's round-trip check found
  that unstyled notes gained default colours on import; fixed.
- Boards above 32 MB (was 128 MB) open through a temporary file: in memory
  costs about five times the file size.
- License changed from MIT to Apache-2.0 with a NOTICE file.
- 99 automated tests.

## 0.2.0 — 2026-10-14

- Separate version-independent scene model and geometry from storage codecs.
- Document header maps, shared semantics, measured release coverage and forecasts.
- Read native legacy application releases instead of inheriting a modern template value.
- Add groups, note styling, text comments, native pen encoding and measured pen styles.
- Add guarded image options, sibling ordering and atomic layout restoration.
- Add the 1000-image acceptance generator, all-image shuffle and exact restore.
- Verify native PureRef 2.0.3 and 2.1.3 renders and canonical resaves.
- Preserve comments as notes and reset playback after baking an animation frame for legacy export.
- Expand automated coverage from 25 to 44 tests; add Claude review instructions.

Real-board validation and Claude account/app authorization remain pending.
