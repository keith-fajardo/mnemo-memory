"""Descriptor-verified reads for bounded deployment files."""

from __future__ import annotations

import os
import stat
from pathlib import Path


class OwnedFileReadError(RuntimeError):
    pass


def read_bounded_owned_file(path: Path, *, maximum_bytes: int, owner_only: bool) -> str:
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        mode = stat.S_IMODE(metadata.st_mode)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise OSError
        if (owner_only and mode & 0o077) or (not owner_only and mode & 0o022):
            raise OSError
        content = os.read(descriptor, maximum_bytes + 1)
        if len(content) > maximum_bytes:
            raise OSError
        return content.decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise OwnedFileReadError from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
