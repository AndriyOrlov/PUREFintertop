"""Observed release coverage, separate from recognizing a file layout."""

TESTED_LEGACY_RELEASES = ("1.10.4", "1.11.1")
TESTED_MODERN_RELEASES = ("2.0.3", "2.1.3")
WRITER_APPLICATION_VERSION = "2.1.3"


def compatibility_status(board):
    legacy = board.format == "legacy"
    tested = TESTED_LEGACY_RELEASES if legacy else TESTED_MODERN_RELEASES
    release = board.application_version
    status = "tested_release" if release in tested else "unverified_release"
    if legacy and release is None:
        status = "legacy_release_not_recorded"
    return {
        "format_identifier": "1.10" if legacy else "2.1",
        "status": status,
        "tested_releases": list(tested),
        "reason": "Layout recognition does not certify an untested release.",
    }
