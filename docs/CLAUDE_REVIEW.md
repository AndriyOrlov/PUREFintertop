# Review brief for Claude

Review this prototype before it is proposed for Polygonflow integration.
Do not assume that passing synthetic round trips establish full format support.

Run `python -m unittest discover -s tests -v`. Read the native evidence and the
real-board results supplied by Andrii. Review these areas first:

- Modern container relocation, dynamic checksum position, schema preservation.
- Qt matrix composition, image-center coordinates, parent transforms.
- Drawing-to-image alignment, pixel density and child-transform compensation.
- Compatibility export: no silent loss, GIF frame selection, opacity baking,
  group flattening, unsupported crop/shape/flags behavior.
- Legacy codec assumptions, pathological files, linked images, nested notes.
- Atomic output, exception contracts, manifest path validation and round-trip fields.
- API readability and whether a host developer can integrate it without a rewrite.
- Memory use and default file/pixel budgets on real, large boards.

Classify findings as Critical, Required, Optional or Nit. Give a concrete failing
case for correctness findings. Keep refactors separate from behavior changes.

FrameRef-native integration is not implemented. This review concerns the `.pur`
API, codecs, neutral contract and simplification path. Shape support, exact
antialiasing, modern thumbnail regeneration and host comment/style mapping are
known pending work, not capabilities to infer from the project name.

Do not send any proposal email or publish the repository publicly.
