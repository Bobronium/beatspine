from __future__ import annotations

import uuid
from decimal import Decimal

from typing import Final


DEFAULT_FPS: Final[int] = 60
DEFAULT_GAP_SEC: Final[Decimal] = Decimal(0)
DEFAULT_START_OFFSET_BEATS: Final[int] = 0
DEFAULT_END_OFFSET_BEATS: Final[int] = 0
DEFAULT_OUTPUT: Final[str] = "timeline_project"
DEFAULT_PLACEHOLDERS: Final[str] = "none"
DEFAULT_TIME_GAP: Final[str] = "none"
UUID_NAMESPACE: Final[uuid.UUID] = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
FINDER_COMMENT_ATTR: Final[str] = "com.apple.metadata:kMDItemFinderComment"
SUPPORTED_IMAGE_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {".jpg", ".jpeg", ".png", ".heic", ".tiff", ".tif", ".gif"}
)
SUPPORTED_AUDIO_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {".m4a", ".mp3", ".wav", ".aac"}
)
