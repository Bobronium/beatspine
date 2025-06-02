from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from enum import auto
from pathlib import Path
from typing import Any
from typing import Protocol

from beatspine.typehints import Milliseconds

from beatspine.typehints import PhotoMetadata
from beatspine.typehints import Seconds


class MediaType(StrEnum):
    """Media element types in timeline."""

    VIDEO = auto()
    AUDIO = auto()
    TITLE = auto()
    CAPTION = auto()
    PLACEHOLDER = auto()
    MARKER = auto()


class PlaceholderMode(StrEnum):
    """Placeholder generation strategies."""

    NONE = auto()
    TITLE = auto()
    CAPTIONS = auto()
    IMAGE = auto()
    MISSING = auto()


class TimeUnit(StrEnum):
    """Time gap units for photo clustering."""

    SECOND = auto()
    MINUTE = auto()
    HOUR = auto()
    DAY = auto()
    WEEK = auto()
    MONTH = auto()
    YEAR = auto()


@dataclass(slots=True, frozen=True)
class TimeGap:
    """Time gap configuration for photo clustering."""

    amount: int
    unit: TimeUnit
    same_period_mode: bool = False

    @classmethod
    def none(cls) -> TimeGap:
        """Create a no-gap configuration."""
        return cls(amount=0, unit=TimeUnit.SECOND, same_period_mode=False)

    @classmethod
    def parse(cls, gap_str: str) -> TimeGap:
        """Parse time gap from string format like '1-day', '1-year-same'."""
        if gap_str.lower() == "none":
            return cls.none()

        parts = gap_str.lower().split("-")
        if len(parts) < 2:
            raise ValueError(f"Invalid time gap format: {gap_str}")

        try:
            amount = int(parts[0])
        except ValueError:
            raise ValueError(f"Invalid amount in time gap: {parts[0]}")

        unit_str = parts[1]
        try:
            unit = TimeUnit(unit_str)
        except ValueError:
            raise ValueError(f"Invalid time unit: {unit_str}")

        same_period_mode = len(parts) > 2 and parts[2] == "same"
        return cls(amount=amount, unit=unit, same_period_mode=same_period_mode)


@dataclass(slots=True, frozen=True)
class PhotoCluster:
    """Group of photos within a time gap."""

    photos: list[PhotoMetadata]
    start_date: datetime
    end_date: datetime

    @property
    def representative_date(self) -> datetime:
        """Get the representative date for this cluster (median)."""
        dates = [photo[1] for photo in self.photos]
        dates.sort()
        return dates[len(dates) // 2]


@dataclass(slots=True, frozen=True)
class Dimensions:
    """Video dimensions."""

    width: int
    height: int


@dataclass(slots=True, frozen=True)
class TimeRange:
    """Time range with start and duration."""

    start: Milliseconds
    duration: Milliseconds
    start_frame: int = 0
    duration_frames: int = 0

    @property
    def end(self) -> Milliseconds:
        """Calculate end time."""
        return self.start + self.duration


@dataclass(slots=True, frozen=True)
class DateRange:
    """Date range for approximation display."""

    start_date: datetime
    end_date: datetime

    def format_range(self) -> str:
        """Format date range for display."""
        if self.start_date.date() == self.end_date.date():
            return self.start_date.strftime("%Y-%m-%d")
        return f"{self.start_date.strftime('%Y-%m-%d')} → {self.end_date.strftime('%Y-%m-%d')}"


@dataclass(slots=True, frozen=True)
class MediaAsset:
    """Generic media asset representation."""

    path: Path
    media_type: MediaType
    uid: str
    name: str
    duration: Milliseconds = Decimal(0)
    dimensions: Dimensions | None = None

    @classmethod
    def from_photo(cls, path: Path, uid: str) -> MediaAsset:
        """Create photo asset."""
        from beatspine.filesystem import detect_image_dimensions

        return cls(
            path=path,
            media_type=MediaType.VIDEO,
            uid=uid,
            name=path.stem,
            dimensions=detect_image_dimensions(path),
        )

    @classmethod
    def from_audio(cls, path: Path, uid: str, duration: Milliseconds) -> MediaAsset:
        """Create audio asset."""
        return cls(
            path=path,
            media_type=MediaType.AUDIO,
            uid=uid,
            name=path.stem,
            duration=duration,
        )


@dataclass(slots=True, frozen=True)
class TimelineElement:
    """Element placed on timeline."""

    asset: MediaAsset | None
    time_range: TimeRange
    track: int
    media_type: MediaType
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """Get element name."""
        if self.asset:
            return self.asset.name
        return self.metadata.get("name", "Untitled")


@dataclass(slots=True, frozen=True)
class TimelineMarker:
    """Marker on timeline."""

    position: Milliseconds
    name: str
    duration: Milliseconds = Decimal(1000) / 60  # 1 frame default
    position_frame: int = 0
    duration_frames: int = 1


@dataclass(slots=True, frozen=True)
class BeatInfo:
    """Beat timing and metadata."""

    index: int
    time: Seconds
    frame: int = 0
    date_range: DateRange | None = None


@dataclass(slots=True, frozen=True)
class PhotoPlacement:
    """Photo placement on timeline."""

    asset: MediaAsset
    beat_index: int
    date: datetime
    is_anchored: bool = False
    cluster_id: int | None = None


@dataclass(slots=True, frozen=True)
class TimelineProject:
    """NLE-agnostic timeline representation."""

    name: str
    duration: Milliseconds
    duration_frames: int # Moved here, no default
    frame_rate: int
    dimensions: Dimensions
    elements: list[TimelineElement]
    markers: list[TimelineMarker]
    beats: list[BeatInfo]
    photo_placements: list[PhotoPlacement]

    # Remaining fields with default values
    gap_duration: Milliseconds = Decimal(0)
    start_offset_beats: int = 0
    end_offset_beats: int = 0

    # Placeholder configuration
    placeholder_mode: PlaceholderMode = PlaceholderMode.NONE

    # Time gap configuration
    time_gap: TimeGap = field(default_factory=TimeGap.none)

    def get_effective_beats(self) -> list[BeatInfo]:
        """Get beats excluding offset ranges."""
        start_idx = self.start_offset_beats
        end_idx = len(self.beats) - self.end_offset_beats
        return self.beats[start_idx:end_idx]


class TimelineExporter(Protocol):
    """Protocol for NLE-specific exporters."""

    def export(self, project: TimelineProject, output_path: Path) -> None:
        """Export timeline to NLE-specific format."""
        ...
