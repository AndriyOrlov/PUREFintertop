"""The PureRef 2.1 file container, measured in native 2.0.3 and 2.1.3 files.

A modern .pur file is an SQLite database with a header written over its start:

    [header][SQLite bytes after the header length][first SQLite bytes, moved here]

The header (big-endian QDataStream) holds:

    format id "2.1" | 4 reserved zero bytes | SQLite size (uint64)
    | application version | MD5 checksum text | thumbnail (JPEG)

The checksum covers everything after the checksum text. Let H be the header
length and S the SQLite size: the file is S + H bytes long and the database is
`file[S:] + file[H:S]`.

Two ways to open a file are offered:

* `unpack_container(bytes)` - everything in memory, for small boards.
* `open_container_file(path, folder)` - the database is rebuilt in a temporary
  file in chunks, so multi-gigabyte boards never need to fit in memory.
"""

import hashlib
import io
import os
import shutil
import sqlite3
import struct
from dataclasses import dataclass

FORMAT_ID = "2.1"
_RESERVED = bytes(4)
_SQLITE_MAGIC = b"SQLite format 3\0"
_MAX_HEADER_TEXT = 4096  # bytes; real header strings are a few dozen
_MAX_THUMBNAIL = 64 * 1024 * 1024
_CHUNK = 4 * 1024 * 1024


class InvalidPurFile(ValueError):
    pass


class UnsupportedFormat(InvalidPurFile):
    pass


@dataclass(frozen=True)
class Header:
    application_version: str
    checksum: str
    thumbnail: bytes
    database_size: int  # S
    header_size: int  # H
    checksum_end: int  # where the checksummed bytes start


def qstring(value):
    """Encode text as a QString: byte length, then UTF-16BE."""
    data = value.encode("utf-16-be")
    return struct.pack(">I", len(data)) + data


def read_header(stream):
    """Parse the header from a binary stream positioned at the file start."""

    def take(count):
        data = stream.read(count)
        if len(data) != count:
            raise InvalidPurFile("Truncated PureRef header")
        return data

    def text():
        length = struct.unpack(">I", take(4))[0]
        if length > _MAX_HEADER_TEXT or length % 2:
            raise InvalidPurFile("Invalid header string length")
        try:
            return take(length).decode("utf-16-be")
        except UnicodeDecodeError as exc:
            raise InvalidPurFile("Invalid header text") from exc

    version = text()
    if version != FORMAT_ID:
        raise UnsupportedFormat(f"Unsupported modern format: {version!r}")
    if take(4) != _RESERVED:
        raise UnsupportedFormat("Unknown modern header flags")
    database_size = struct.unpack(">Q", take(8))[0]
    application_version = text()
    checksum = text()
    checksum_end = stream.tell()
    thumbnail_size = struct.unpack(">I", take(4))[0]
    if thumbnail_size > _MAX_THUMBNAIL:
        raise InvalidPurFile("Thumbnail is implausibly large")
    thumbnail = take(thumbnail_size)
    return Header(
        application_version=application_version,
        checksum=checksum,
        thumbnail=thumbnail,
        database_size=database_size,
        header_size=stream.tell(),
        checksum_end=checksum_end,
    )


def _check_layout(header, file_size):
    if (
        header.database_size < header.header_size
        or file_size != header.database_size + header.header_size
    ):
        raise InvalidPurFile("Invalid SQLite size or relocated header length")


def _check_database(connection):
    try:
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise InvalidPurFile("SQLite integrity check failed")
    except sqlite3.Error as exc:
        raise InvalidPurFile(f"Invalid SQLite payload: {exc}") from exc


def _harden(connection):
    """Do not let a crafted file run SQL of its own while it is being edited."""
    connection.execute("PRAGMA trusted_schema=OFF")
    hidden_code = connection.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('trigger', 'view')"
    ).fetchall()
    if hidden_code:
        raise UnsupportedFormat("PureRef files never contain SQLite triggers or views")


# --- In memory -----------------------------------------------------------------


def unpack_container(data):
    """Return (connection, application_version, thumbnail) for in-memory bytes."""
    header = read_header(io.BytesIO(data))
    _check_layout(header, len(data))
    actual = hashlib.md5(data[header.checksum_end :]).hexdigest()
    if header.checksum != actual:
        raise InvalidPurFile("PureRef checksum mismatch")
    database = data[header.database_size :] + data[header.header_size : header.database_size]
    if not database.startswith(_SQLITE_MAGIC):
        raise InvalidPurFile("Missing relocated SQLite header")
    connection = sqlite3.connect(":memory:")
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 256 * 1024 * 1024)
    try:
        connection.deserialize(database)
        _check_database(connection)
        _harden(connection)
    except (sqlite3.Error, InvalidPurFile):
        connection.close()
        raise
    return connection, header.application_version, header.thumbnail


def _prefix(database_size, application_version, thumbnail):
    """Build the header with a placeholder checksum; return it and the checksum slot."""
    from .profiles import WRITER_APPLICATION_VERSION

    prefix = qstring(FORMAT_ID) + _RESERVED + struct.pack(">Q", database_size)
    prefix += qstring(application_version or WRITER_APPLICATION_VERSION)
    checksum_start = len(prefix) + 4  # skip the QString length
    prefix += qstring("0" * 32)
    checksum_end = len(prefix)
    prefix += struct.pack(">I", len(thumbnail)) + thumbnail
    if len(prefix) > database_size:
        raise InvalidPurFile("Thumbnail exceeds database size")
    return prefix, checksum_start, checksum_end


def pack_container(connection, application_version, thumbnail):
    """Return complete .pur bytes for a connection (in-memory or file-backed)."""
    connection.commit()
    database = connection.serialize()
    prefix, checksum_start, checksum_end = _prefix(
        len(database), application_version, thumbnail
    )
    output = bytearray(prefix + database[len(prefix) :] + database[: len(prefix)])
    output[checksum_start:checksum_end] = (
        hashlib.md5(output[checksum_end:]).hexdigest().encode("utf-16-be")
    )
    return bytes(output)


# --- File backed, for large boards ------------------------------------------------


def _copy_range(source, target, start, length):
    source.seek(start)
    while length:
        chunk = source.read(min(_CHUNK, length))
        if not chunk:
            raise InvalidPurFile("Truncated PureRef file")
        target.write(chunk)
        length -= len(chunk)


def open_container_file(path, folder):
    """Rebuild the database into `folder/board.sqlite` and open it.

    Returns (connection, application_version, thumbnail, database_path).
    """
    database_path = os.path.join(folder, "board.sqlite")
    file_size = os.path.getsize(path)
    with open(path, "rb") as source:
        header = read_header(source)
        _check_layout(header, file_size)
        digest = hashlib.md5()
        source.seek(header.checksum_end)
        for chunk in iter(lambda: source.read(_CHUNK), b""):
            digest.update(chunk)
        if digest.hexdigest() != header.checksum:
            raise InvalidPurFile("PureRef checksum mismatch")
        with open(database_path, "wb") as database:
            _copy_range(source, database, header.database_size, header.header_size)
            _copy_range(
                source,
                database,
                header.header_size,
                header.database_size - header.header_size,
            )
    with open(database_path, "rb") as database:
        if database.read(len(_SQLITE_MAGIC)) != _SQLITE_MAGIC:
            raise InvalidPurFile("Missing relocated SQLite header")
    connection = sqlite3.connect(database_path)
    try:
        _check_database(connection)
        _harden(connection)
    except (sqlite3.Error, InvalidPurFile):
        connection.close()
        raise
    return connection, header.application_version, header.thumbnail, database_path


def write_container_file(connection, database_path, application_version, thumbnail, target):
    """Stream a file-backed board into the binary stream `target`."""
    connection.commit()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    database_size = os.path.getsize(database_path)
    prefix, checksum_start, checksum_end = _prefix(
        database_size, application_version, thumbnail
    )
    with open(database_path, "rb") as database:
        # Pass 1: the checksum covers the end of the header, the database body
        # and the relocated database start, in that order.
        digest = hashlib.md5(prefix[checksum_end:])
        database.seek(len(prefix))
        for chunk in iter(lambda: database.read(_CHUNK), b""):
            digest.update(chunk)
        database.seek(0)
        relocated = database.read(len(prefix))
        digest.update(relocated)
        # Pass 2: write the file.
        header = bytearray(prefix)
        header[checksum_start:checksum_end] = digest.hexdigest().encode("utf-16-be")
        target.write(header)
        database.seek(len(prefix))
        shutil.copyfileobj(database, target, _CHUNK)
        target.write(relocated)
