"""PureRef 2.1 container, measured in native 2.0.3 and 2.1.3 files."""

import hashlib
import sqlite3
import struct


class InvalidPurFile(ValueError):
    pass


class UnsupportedFormat(InvalidPurFile):
    pass


def qstring(value):
    data = value.encode("utf-16-be")
    return struct.pack(">I", len(data)) + data


def unpack_container(data):
    pos = 0

    def take(n):
        nonlocal pos
        if n < 0 or pos + n > len(data):
            raise InvalidPurFile("Truncated PureRef header")
        result = data[pos : pos + n]
        pos += n
        return result

    def string():
        n = struct.unpack(">I", take(4))[0]
        if n > 4096 or n % 2:
            raise InvalidPurFile("Invalid header string length")
        try:
            return take(n).decode("utf-16-be")
        except UnicodeDecodeError as exc:
            raise InvalidPurFile("Invalid header text") from exc

    version = string()
    if version != "2.1":
        raise UnsupportedFormat(f"Unsupported modern format: {version!r}")
    flags = take(4)
    if flags != bytes(4):
        raise UnsupportedFormat("Unknown modern header flags")
    size = struct.unpack(">Q", take(8))[0]
    application_version = string()
    expected = string()
    checksum_end = pos
    thumb_size = struct.unpack(">I", take(4))[0]
    thumbnail = take(thumb_size)
    header_size = pos
    if size < header_size or len(data) != size + header_size:
        raise InvalidPurFile("Invalid SQLite size or relocated header length")
    actual = hashlib.md5(data[checksum_end:]).hexdigest()
    if expected != actual:
        raise InvalidPurFile("PureRef checksum mismatch")
    database = data[size:] + data[header_size:size]
    if not database.startswith(b"SQLite format 3\0"):
        raise InvalidPurFile("Missing relocated SQLite header")
    connection = sqlite3.connect(":memory:")
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 256 * 1024 * 1024)
    try:
        connection.deserialize(database)
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise InvalidPurFile("SQLite integrity check failed")
    except sqlite3.Error as exc:
        connection.close()
        raise InvalidPurFile(f"Invalid SQLite payload: {exc}") from exc
    return connection, application_version, thumbnail


def pack_container(connection, application_version, thumbnail):
    connection.commit()
    database = connection.serialize()
    prefix = qstring("2.1") + bytes(4) + struct.pack(">Q", len(database))
    prefix += qstring(application_version)
    checksum_start = len(prefix) + 4
    prefix += qstring("0" * 32)
    checksum_end = len(prefix)
    prefix += struct.pack(">I", len(thumbnail)) + thumbnail
    if len(prefix) > len(database):
        raise InvalidPurFile("Thumbnail exceeds database size")
    output = bytearray(prefix + database[len(prefix) :] + database[: len(prefix)])
    output[checksum_start:checksum_end] = (
        hashlib.md5(output[checksum_end:]).hexdigest().encode("utf-16-be")
    )
    return bytes(output)
