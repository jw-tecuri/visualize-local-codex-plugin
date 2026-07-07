"""Shared privacy checks for visualize-local temp artifacts."""

from __future__ import annotations

import os
from pathlib import Path


PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def ensure_private_directory(path: Path, *, create: bool) -> None:
    if path.is_symlink():
        raise ValueError(f"{path} must not be a symlink")

    if create:
        try:
            path.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)
        except OSError as error:
            raise ValueError(f"{path} cannot be created as a private directory: {error}") from error

    if not path.exists():
        raise ValueError(f"{path} does not exist")

    try:
        stat_result = path.stat()
    except OSError as error:
        raise ValueError(f"{path} cannot be inspected: {error}") from error

    if stat_result.st_uid != os.getuid():
        raise ValueError(f"{path} is not owned by the current user")
    if not path.is_dir():
        raise ValueError(f"{path} is not a directory")

    current_mode = stat_result.st_mode & 0o777
    if current_mode != PRIVATE_DIR_MODE:
        try:
            path.chmod(PRIVATE_DIR_MODE)
        except OSError as error:
            raise ValueError(f"{path} permissions cannot be repaired: {error}") from error
