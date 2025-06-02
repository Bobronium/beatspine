from __future__ import annotations

import hashlib
import os
import re
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import xattr
from PIL import Image
from mutagen import File as MutagenFile
import mactime.core

from beatspine.console import error

from beatspine.console import warning
from beatspine.constants import FINDER_COMMENT_ATTR
from beatspine.constants import UUID_NAMESPACE
from beatspine.typehints import Seconds

if TYPE_CHECKING:
    from beatspine.definitions import Dimensions


def generate_deterministic_uid(path: Path, method: str = "inode") -> str:
    """Generate a deterministic UUID based on specified method."""
    if method == "inode":
        try:
            stat_info = os.stat(path)
            unique_id = f"{stat_info.st_dev}-{stat_info.st_ino}"
            return str(uuid.uuid5(UUID_NAMESPACE, unique_id)).upper()
        except (AttributeError, OSError):
            warning(f"Could not get inode for {path}, falling back to content hash")
            method = "content"

    if method == "content":
        hasher = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return str(uuid.uuid5(UUID_NAMESPACE, hasher.hexdigest())).upper()

    if method == "path":
        return str(uuid.uuid5(UUID_NAMESPACE, str(path.resolve()))).upper()

    warning(f"Unknown ID method '{method}', using inode")
    return generate_deterministic_uid(path, "inode")


def detect_image_dimensions(path: Path) -> Dimensions | None:
    """Detect image dimensions using PIL."""
    from beatspine.definitions import Dimensions

    try:
        with Image.open(path) as img:
            return Dimensions(img.size[0], img.size[1])
    except Exception as e:
        warning(f"Failed to get dimensions of {path}: {e}")
        return None


def get_audio_duration(audio_path: Path) -> Seconds:
    """Extract duration from audio file using mutagen."""
    try:
        audio_file = MutagenFile(audio_path)
        if audio_file is None or audio_file.info is None:
            error(f"Cannot read audio file: {audio_path}")
        return Decimal(audio_file.info.length)
    except Exception as e:
        error(f"Failed to extract duration from {audio_path}: {e}")


def get_finder_comment(path: Path) -> str | None:
    """Extract Finder comment from file metadata."""
    try:
        attrs = xattr.xattr(path)
        if FINDER_COMMENT_ATTR in attrs:
            return attrs[FINDER_COMMENT_ATTR].decode("utf-8")
    except (OSError, UnicodeDecodeError):
        pass
    return None


def extract_date_from_filename(path: Path) -> datetime | None:
    """Extract date from screenshot filenames."""
    pattern = r".*Screenshot ([0-9]{4})-([0-9]{2})-([0-9]{2}) at ([0-9]{2})\.([0-9]{2})\.([0-9]{2}).*"
    match = re.match(pattern, path.name)
    if match:
        year, month, day, hour, minute, second = map(int, match.groups())
        return datetime(year, month, day, hour, minute, second)
    return None


def exif_date(path: Path) -> datetime | None:
    try:
        import exifread

        tags = exifread.process_file(open(path, "rb"), stop_tag="EXIF DateTimeOriginal")
        v = tags.get("EXIF DateTimeOriginal")
        if v:
            return datetime.strptime(str(v.values), "%Y:%m:%d %H:%M:%S")
    except:
        return None


def get_photo_date(path: Path) -> datetime:
    """Get creation date of photo."""
    from_exif = exif_date(path)
    from_filename = extract_date_from_filename(path)
    from_finder = mactime.core.get_timespec_attrs(path)["created"]
    if from_finder in {
        datetime.fromisoformat("1984-01-24 09:00:00"),
        datetime.fromtimestamp(0),
    }:
        from_finder = None
    return min(filter(None, (from_exif, from_filename, from_finder)))
