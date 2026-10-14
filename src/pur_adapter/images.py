"""Image file helpers that copy what native PureRef 2.1.3 does on import.

Measured with the installed PureRef 2.1.3 `load` command:

* The stored format is detected from the file content, but spelled like the
  file extension when the extension agrees: `a.jpg` -> "JPG", `a.jpeg` -> "JPEG",
  `a.tif` -> "TIF", `a.tiff` -> "TIFF". A PNG saved as `a.jpg` is stored as "PNG".
* A JPEG with an EXIF rotation is stored with its *displayed* width and height,
  and the original file bytes are kept. PureRef rotates the pixels when drawing.
"""

import io
from PIL import Image, ImageOps

# Pillow's format name -> the spellings PureRef accepts for that content.
_SPELLINGS_BY_CONTENT = {
    "JPEG": ("JPG", "JPEG"),
    "MPO": ("JPG", "JPEG"),  # phone cameras: a JPEG with extra embedded pictures
    "TIFF": ("TIF", "TIFF"),
    "PNG": ("PNG",),
    "GIF": ("GIF",),
    "BMP": ("BMP",),
    "WEBP": ("WEBP",),
}

# EXIF orientations 5-8 rotate the picture by 90 degrees, swapping width/height.
_SIDEWAYS_ORIENTATIONS = {5, 6, 7, 8}
_EXIF_ORIENTATION_TAG = 0x0112


def native_format_name(pillow_format, file_suffix=""):
    """Return the format string PureRef writes into `images.format`."""
    spellings = _SPELLINGS_BY_CONTENT.get(pillow_format, (pillow_format or "BIN",))
    suffix = file_suffix.lstrip(".").upper()
    return suffix if suffix in spellings else spellings[-1]


def describe_image_file(data, file_suffix=""):
    """Return (displayed_width, displayed_height, native_format_name) for raw bytes."""
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
        orientation = image.getexif().get(_EXIF_ORIENTATION_TAG, 1)
        if orientation in _SIDEWAYS_ORIENTATIONS:
            width, height = height, width
        return width, height, native_format_name(image.format, file_suffix)


def open_as_displayed(data):
    """Open image bytes with the EXIF rotation applied, the way PureRef shows them.

    Images without a rotation are returned exactly as opened, so animation
    frames can still be selected with `seek`. The caller should close the result.
    """
    image = Image.open(io.BytesIO(data))
    if image.getexif().get(_EXIF_ORIENTATION_TAG, 1) == 1:
        return image
    rotated = ImageOps.exif_transpose(image)
    image.close()
    return rotated
