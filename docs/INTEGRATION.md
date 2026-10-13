# FrameRef integration boundary and reverse export

## Import into a host

`export_manifest(board, folder)` produces `board.json` plus `assets/`.
The host reads one scene description and creates its own native board objects.

The contract uses item-local coordinates, parent IDs and nine row-major Qt-style
matrix coefficients. Images have their item origin at the image center. The
coordinate system has positive y downward. Asset paths are relative to the
package folder and do not require network access.

Common fields: `id`, `kind`, `name`, `parent_id`, `matrix`, `opacity`, `locked`, `z`.
Images add `path`, `pixel_size` and `native_image_properties`. The latter retains
Qt's native bounds/image transform/flags. A production host mapper should decode
those into its crop representation or explicitly bake a preview. Python manifest
round trips restore these fields; this is not itself a FrameRef crop implementation.
Notes currently expose native HTML when present. Treat it as document data and
map it to the host's rich-text model rather than executing it.

Groups preserve hierarchy. Group visual styles and comment relationships need an
explicit host mapping before a production release. Unsupported drawing shapes
must use a host/native raster renderer or remain flagged as unresolved.

## Export from a host

The host supplies the same JSON contract to `import_manifest()`. IDs may be strings
or integers; the importer returns an `id_map` for the generated PureRef IDs.
Supported kinds are `image`, `note`, `group`. Other media use `preview_path`:
`video`, `pdf`, `link`, `svg`, `drawing`.

Example host-side data:

```json
{
  "schema_version": 1,
  "items": [
    {"id":"clip","kind":"video","preview_path":"assets/selected-frame.png","x":100,"y":150},
    {"id":"plan","kind":"pdf","preview_path":"assets/page-2.png","x":500,"y":150}
  ]
}
```

Video timestamps and PDF page selection belong to the host. The adapter consumes
those chosen raster images; it does not independently select frames/pages.
A multi-page PDF becomes multiple image items if the host exports multiple pages.
Use original source resolution or a documented export density.

Then call `export_legacy()` to emit the simplified `1.10` `.pur` layout, compatible
with the four tested versions. The host receives a report for conversion changes.

| Host feature | Simplified PureRef representation |
|---|---|
| Image | Embedded PNG; affine placement retained |
| GIF / video | Selected frame as PNG |
| PDF | Selected page(s) as image items |
| Freehand / shape | Host-rendered PNG, or supported native freehand renderer |
| Group | Parent transforms flattened; group frame/style omitted |
| Rich text | Plain note, or a host-rendered image for visual fidelity |
| Link card / AI result | Host-provided preview image; label as a note if desired |
| Tags / app-specific settings | Conversion report / external metadata |

This path preserves a useful static board. It cannot retain video playback, PDF
page navigation, AI operations or host-specific editing behavior in legacy `.pur`.

## Suggested Polygonflow implementation

1. Wrap this adapter or port the documented codecs into the host's language.
2. Map JSON items to FrameRef image, note, group and media objects.
3. Let the host render unsupported crops, styles and shapes using its own renderer.
4. Expose `Import PureRef` and `Export PureRef compatibility board` commands.
5. Show a short conversion summary and keep the full report available.

There is no assumption about FrameRef's implementation language or private save
format. The native plugin/SDK wiring is pending an agreement with its author.
