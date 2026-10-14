# Common base across measured releases

The common base is semantic, not a shared byte layout. Native files from PureRef
1.10.4, 1.11.1, 2.0.3 and 2.1.3 retain images, notes, names, local affine
geometry, hierarchy and resource references. The 1.x and 2.x containers differ.

`src/pur_adapter/core.py` owns the immutable item model and row-vector geometry.
It depends only on the Python standard library. It does not import SQLite, Qt,
Pillow, a release profile or a storage codec. Existing `board.Item` and
`qt.affine`, `qt.multiply`, `qt.inverse` imports remain available.

| Concern | Common meaning | Storage-specific behavior |
|---|---|---|
| Images | Dimensions, decoded pixels, shared resources | Legacy PNG records vs SQLite resources |
| Geometry | Local transform followed by parent transform | Legacy doubles vs Qt QVariant QTransform |
| Notes | Text, placement, parent | Legacy records vs SQLite rich text and style |
| Hierarchy | Parent and child relation | Legacy text children; modern groups and other parents |
| Ordering | Relative scene order | Legacy z; modern z and BigRational sort_order |
| Integrity | Detect damaged input | MD5 ranges and header relocation differ |

Identical decoded pixels need not have identical encoded PNG bytes. Native
default z values also differ. These are not byte invariants. Groups, drawing
styles, animation options and modern metadata are version-dependent features.

Keep codecs separate. Preserve unknown native values on no-op and ordinary edits;
reject conversions that would discard an unrecognized feature. A future version
can resemble a tested layout without being a tested release.
