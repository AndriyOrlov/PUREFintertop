# Observed .pur layouts

The following notes describe the tested files, not a promise about future versions.
Source evidence: native fixture files, SQLite integrity checks, published legacy
codec, targeted decompilation of official Linux builds and native load/render tests.

## Modern format 2.1

PureRef 2.0.3 and 2.1.3 both wrote a format identifier of `2.1`.
The application release number is a separate string.

The header is a big-endian QDataStream sequence:

| Field | Encoding |
|---|---|
| Format identifier | QString: byte length + UTF-16BE, observed `2.1` |
| Reserved flags | quint32, observed 0 |
| SQLite database byte length | quint64 |
| Application version | QString, e.g. `2.1.3` |
| MD5 checksum | QString containing 32 hexadecimal characters |
| Thumbnail | QByteArray: byte length + JPEG bytes |

Let `H` be the complete header length and `S` the SQLite database length.
The header replaces the first `H` bytes of the SQLite file. Those original bytes
are relocated to the tail at offset `S`. Therefore total size is `S + H`.

Recovery:

```python
database = pur_bytes[S:] + pur_bytes[H:S]
```

Packing is the inverse. The checksum covers everything after the checksum
QString, including the thumbnail length/data, SQLite body and relocated prefix.
For the tested release strings that coverage started at byte 104, but the adapter
parses the position dynamically rather than hard-coding 104.

The recovered database is checked with SQLite `PRAGMA quick_check`.
Native fixture reconstruction also passed `PRAGMA integrity_check`.

Tables: `images`, `items`, `items_images`, `items_notes`, `items_groups`,
`items_drawings`, `metadata`. Schema details are in `schema.sql`.

`items.transform` is a QVariant with type ID 80, a null flag, and nine big-endian
doubles. In the tested SQLite files it is stored as a TEXT value containing a
Latin-1 interpretation of the serialized bytes, despite the declared BLOB type.
The adapter preserves that convention.

The row-vector matrix is:

```text
[m11, m12, m13, m21, m22, m23, m31, m32, m33]
```

Local transforms compose before their parent transforms. For affine transforms,
this maps to CSS `matrix(m11, m12, m21, m22, m31, m32)`.
An item's image is normally centered at its local origin. `items_images` holds
separate image bounds and the image's pixel-to-local transform.

## Native pen strokes

`items_drawings.strokes` is a serialized QVariant of
`QList<GraphicsDrawItem::Stroke>`. The measured serialized fields per stroke are:

1. Stroke serialization marker 100 (a version marker, not opacity).
2. QColor: specification byte and channel words.
3. Pen width as a double.
4. QPainterPath: element count, typed points, current-subpath index and fill rule.
5. Extra QPointF.
6. Style integer.

The reader also recognizes the older unmarked stroke branch found in the native
reader, but the raster test fixture exercises marker 100, style 0 (freehand).
CurveTo elements use two following CurveToData elements. Shapes are not silently
interpreted as freehand: unsupported styles stop conversion.

Raster conversion adds transparent local padding around the stroke. The new
image transform compensates for that padding and pixel density. Existing item
IDs, parents and z values survive. Child transforms are adjusted with the inverse
of the changed parent transform so their world positions survive too.

## Legacy format 1.10

The tested 1.10.4 and 1.11.1 files use a 224-byte header, embedded PNG data or
resource references, GraphicsImageItem / GraphicsTextItem records, an origin
folder string and an image reference table. The checksum is a UTF-16BE hexadecimal
MD5 over bytes from offset 108 onwards.

The MIT legacy codec is attributed in `third_party/NOTICE.md`. It is patched
locally for empty/note-only scenes and safe file-handle closure.

Compatibility export intentionally emits flat image/note content. Groups and
supported pen drawings are converted first. The conversion report records these
changes. Crops, unknown flags, shape styles, perspective and linked resources
require a deliberate host mapping or pre-rendered preview.

## Reference sources

- https://www.pureref.com/handbook/automation/
- https://www.pureref.com/handbook/2.0/saveload/
- https://github.com/FyorDev/PureRef-format
- https://doc.qt.io/qt-6/qtransform.html
- https://doc.qt.io/qt-6/qpainterpath.html
- https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html
