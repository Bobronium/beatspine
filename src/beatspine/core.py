from __future__ import annotations

import re
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont
from tqdm import tqdm

from beatlapse.console import echo

from beatlapse.console import error

from beatlapse.console import warning
from beatlapse.constants import DEFAULT_END_OFFSET_BEATS
from beatlapse.constants import DEFAULT_FPS
from beatlapse.constants import DEFAULT_GAP_SEC
from beatlapse.constants import DEFAULT_START_OFFSET_BEATS
from beatlapse.constants import SUPPORTED_IMAGE_EXTENSIONS
from beatlapse.definitions import BeatInfo
from beatlapse.definitions import DateRange
from beatlapse.definitions import Dimensions
from beatlapse.definitions import MediaAsset
from beatlapse.definitions import MediaType
from beatlapse.definitions import PhotoCluster
from beatlapse.definitions import PhotoPlacement
from beatlapse.definitions import PlaceholderMode
from beatlapse.definitions import TimeGap
from beatlapse.definitions import TimeRange
from beatlapse.definitions import TimeUnit
from beatlapse.definitions import TimelineElement
from beatlapse.definitions import TimelineMarker
from beatlapse.definitions import TimelineProject
from beatlapse.filesystem import generate_deterministic_uid
from beatlapse.filesystem import get_audio_duration
from beatlapse.filesystem import get_finder_comment
from beatlapse.filesystem import get_photo_date
from beatlapse.typehints import PhotoMetadata
from beatlapse.typehints import Seconds


def parse_beat_anchor(comment: str) -> int | None:
    """Parse beat number from Finder comment. Expected format: 'beat:N' or just 'N'."""
    if not comment:
        return None

    # Try "beat:N" format first
    beat_match = re.search(r"beat:\s*(\d+)", comment.lower())
    if beat_match:
        return int(beat_match.group(1))

    # Try standalone number
    number_match = re.search(r"^\s*(\d+)\s*$", comment)
    if number_match:
        return int(number_match.group(1))

    return None


def load_photos(
    dir_path: Path, supported_extensions: frozenset[str] = SUPPORTED_IMAGE_EXTENSIONS
) -> list[PhotoMetadata]:
    """Load all supported image files and extract dates and beat anchors."""
    photos: list[PhotoMetadata] = []

    for file_path in dir_path.iterdir():
        if file_path.suffix.lower() in supported_extensions:
            try:
                date = get_photo_date(file_path)
                comment = get_finder_comment(file_path)
                anchored_beat = parse_beat_anchor(comment) if comment else None
                photos.append((file_path, date, anchored_beat))
            except (OSError, ValueError) as e:
                warning(f"Skipping {file_path}: {e}")

    if not photos:
        error("No photos found in the specified directory.")

    return sorted(photos, key=lambda x: x[1])


def calculate_time_delta(date1: datetime, date2: datetime, unit: TimeUnit) -> int:
    """Calculate time difference in specified units."""
    delta = abs((date2 - date1).total_seconds())

    match unit:
        case TimeUnit.SECOND:
            return int(delta)
        case TimeUnit.MINUTE:
            return int(delta / 60)
        case TimeUnit.HOUR:
            return int(delta / 3600)
        case TimeUnit.DAY:
            return int(delta / 86400)
        case TimeUnit.WEEK:
            return int(delta / (86400 * 7))
        case TimeUnit.MONTH:
            # Approximate: 30.44 days per month
            return int(delta / (86400 * 30.44))
        case TimeUnit.YEAR:
            # Approximate: 365.25 days per year
            return int(delta / (86400 * 365.25))


def normalize_to_period_start(date: datetime, unit: TimeUnit) -> datetime:
    """Normalize datetime to the start of its period for grouping."""
    match unit:
        case TimeUnit.SECOND:
            return date.replace(microsecond=0)
        case TimeUnit.MINUTE:
            return date.replace(second=0, microsecond=0)
        case TimeUnit.HOUR:
            return date.replace(minute=0, second=0, microsecond=0)
        case TimeUnit.DAY:
            return date.replace(hour=0, minute=0, second=0, microsecond=0)
        case TimeUnit.WEEK:
            # Start of week (Monday)
            days_since_monday = date.weekday()
            start_of_week = date - timedelta(days=days_since_monday)
            return start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
        case TimeUnit.MONTH:
            return date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        case TimeUnit.YEAR:
            return date.replace(
                month=1, day=1, hour=0, minute=0, second=0, microsecond=0
            )


def cluster_photos_by_time_gap(
    photos: list[PhotoMetadata], time_gap: TimeGap
) -> list[PhotoCluster]:
    """Cluster photos based on time gap configuration."""
    if time_gap.amount == 0:  # No clustering
        return [PhotoCluster([photo], photo[1], photo[1]) for photo in photos]

    if time_gap.same_period_mode:
        return cluster_photos_by_same_period(photos, time_gap)
    else:
        return cluster_photos_by_minimum_gap(photos, time_gap)


def cluster_photos_by_same_period(
    photos: list[PhotoMetadata], time_gap: TimeGap
) -> list[PhotoCluster]:
    """Group photos that belong to the same time period."""
    period_groups: dict[datetime, list[PhotoMetadata]] = {}

    for photo in photos:
        period_start = normalize_to_period_start(photo[1], time_gap.unit)
        if period_start not in period_groups:
            period_groups[period_start] = []
        period_groups[period_start].append(photo)

    clusters = []
    for period_start, group_photos in period_groups.items():
        if not group_photos:
            continue

        dates = [photo[1] for photo in group_photos]
        cluster = PhotoCluster(
            photos=group_photos, start_date=min(dates), end_date=max(dates)
        )
        clusters.append(cluster)

    # Sort clusters by their start date
    return sorted(clusters, key=lambda c: c.start_date)


def cluster_photos_by_minimum_gap(
    photos: list[PhotoMetadata], time_gap: TimeGap
) -> list[PhotoCluster]:
    """Group photos with minimum time gap between clusters."""
    if not photos:
        return []

    clusters: list[PhotoCluster] = []
    current_cluster_photos: list[PhotoMetadata] = [photos[0]]

    for i in range(1, len(photos)):
        prev_photo = photos[i - 1]
        current_photo = photos[i]

        gap = calculate_time_delta(prev_photo[1], current_photo[1], time_gap.unit)

        if gap >= time_gap.amount:
            # Create cluster from accumulated photos
            dates = [photo[1] for photo in current_cluster_photos]
            cluster = PhotoCluster(
                photos=current_cluster_photos,
                start_date=min(dates),
                end_date=max(dates),
            )
            clusters.append(cluster)

            # Start new cluster
            current_cluster_photos = [current_photo]
        else:
            # Add to current cluster
            current_cluster_photos.append(current_photo)

    # Add final cluster
    if current_cluster_photos:
        dates = [photo[1] for photo in current_cluster_photos]
        cluster = PhotoCluster(
            photos=current_cluster_photos, start_date=min(dates), end_date=max(dates)
        )
        clusters.append(cluster)

    return clusters


def calculate_element_durations(
    beats: list[Seconds], total_duration: Seconds
) -> list[Seconds]:
    """Calculate duration for each beat position using exact offset differences."""
    durations: list[Seconds] = []

    for i in range(len(beats)):
        if i < len(beats) - 1:
            # Exact duration until next beat offset
            duration = beats[i + 1] - beats[i]
        else:
            # Last beat - exact remaining time
            duration = total_duration - beats[i]

        durations.append(duration)

    return durations


def map_photos_to_beats_original(
    photos: list[PhotoMetadata],
    beats: list[BeatInfo],
    start_date: datetime,
    end_date: datetime,
    start_offset: int,
    end_offset: int,
    id_method: str = "inode",
) -> list[PhotoPlacement]:
    """Original photo to beat mapping logic for when clustering is disabled."""
    timespan = (end_date - start_date).total_seconds()
    if timespan <= 0:
        error("Invalid time span: end date must be after start date")

    # Calculate effective beat range
    effective_beats = beats[start_offset : len(beats) - end_offset]
    num_effective_beats = len(effective_beats)

    beat_occupied = [False] * len(beats)
    placements: list[PhotoPlacement] = []

    # Filter photos within date range
    filtered_photos = [
        (path, date, anchor)
        for path, date, anchor in photos
        if start_date <= date <= end_date
    ]

    if len(filtered_photos) > num_effective_beats:
        error(
            f"Too many photos ({len(filtered_photos)}) for available beats ({num_effective_beats}). "
            f"Reduce photos, increase soundtrack duration, or adjust beat offsets."
        )

    echo(
        f"Available beats: {num_effective_beats} (total: {len(beats)}, offset: {start_offset}-{end_offset})"
    )
    echo(f"Found photos: {len(filtered_photos)}")

    # First pass: place anchored photos
    anchored_count = 0
    for path, date, anchor in filtered_photos:
        if anchor is not None:
            # Convert to 0-based index and check bounds
            beat_index = anchor - 1
            if start_offset <= beat_index < len(beats) - end_offset:
                if beat_occupied[beat_index]:
                    warning(
                        f"Beat {anchor} already occupied, photo {path.name} will be redistributed"
                    )
                else:
                    beat_occupied[beat_index] = True
                    anchored_count += 1
                    asset = MediaAsset.from_photo(
                        path, generate_deterministic_uid(path, id_method)
                    )
                    placements.append(
                        PhotoPlacement(
                            asset=asset,
                            beat_index=beat_index,
                            date=date,
                            is_anchored=True,
                        )
                    )
            else:
                warning(f"Beat {anchor} outside effective range for photo {path.name}")

    # Second pass: distribute non-anchored photos
    non_anchored = [
        (path, date)
        for path, date, anchor in filtered_photos
        if anchor is None or not any(p.asset.path == path for p in placements)
    ]

    for path, date in tqdm(non_anchored, desc="Processing non-anchored photos"):
        # Calculate preferred beat based on chronological position
        position = (date - start_date).total_seconds() / timespan

        # Map to effective beat range
        preferred_beat = start_offset + min(
            int(position * (num_effective_beats - 1)), num_effective_beats - 1
        )

        # Find nearest available beat within effective range
        assigned_beat = preferred_beat
        search_radius = 0

        while beat_occupied[assigned_beat] and search_radius < num_effective_beats:
            search_radius += 1
            if search_radius % 2 == 1:
                candidate = preferred_beat - (search_radius // 2 + 1)
            else:
                candidate = preferred_beat + (search_radius // 2)

            if (
                start_offset <= candidate < len(beats) - end_offset
                and not beat_occupied[candidate]
            ):
                assigned_beat = candidate
                break

        if not beat_occupied[assigned_beat]:
            beat_occupied[assigned_beat] = True
            asset = MediaAsset.from_photo(
                path, generate_deterministic_uid(path, id_method)
            )
            placements.append(
                PhotoPlacement(
                    asset=asset, beat_index=assigned_beat, date=date, is_anchored=False
                )
            )

    echo(
        f"Placed {len(placements)} photos ({anchored_count} anchored, {len(placements) - anchored_count} distributed)"
    )
    echo(
        f"Empty beats: {num_effective_beats - len(placements)} (will be filled with placeholders)"
    )

    return sorted(placements, key=lambda x: x.beat_index)


def map_photo_clusters_to_beats(
    clusters: list[PhotoCluster],
    beats: list[BeatInfo],
    start_date: datetime,
    end_date: datetime,
    start_offset: int,
    end_offset: int,
    id_method: str = "inode",
) -> list[PhotoPlacement]:
    """Map photo clusters to beats with chronological distribution."""
    timespan = (end_date - start_date).total_seconds()
    if timespan <= 0:
        error("Invalid time span: end date must be after start date")

    # Calculate effective beat range
    effective_beats = beats[start_offset : len(beats) - end_offset]
    num_effective_beats = len(effective_beats)

    if len(clusters) > num_effective_beats:
        error(
            f"Too many photo clusters ({len(clusters)}) for available beats ({num_effective_beats}). "
            f"Reduce photos, increase soundtrack duration, adjust time gap, or adjust beat offsets."
        )

    echo(
        f"Available beats: {num_effective_beats} (total: {len(beats)}, offset: {start_offset}-{end_offset})"
    )
    echo(f"Photo clusters: {len(clusters)}")

    beat_occupied = [False] * len(beats)
    placements: list[PhotoPlacement] = []

    # First pass: handle anchored photos within clusters
    anchored_count = 0
    for cluster_idx, cluster in enumerate(clusters):
        for path, date, anchor in cluster.photos:
            if anchor is not None:
                beat_index = anchor - 1
                if start_offset <= beat_index < len(beats) - end_offset:
                    if not beat_occupied[beat_index]:
                        beat_occupied[beat_index] = True
                        anchored_count += 1
                        asset = MediaAsset.from_photo(
                            path, generate_deterministic_uid(path, id_method)
                        )
                        placements.append(
                            PhotoPlacement(
                                asset=asset,
                                beat_index=beat_index,
                                date=date,
                                is_anchored=True,
                                cluster_id=cluster_idx,
                            )
                        )
                    else:
                        warning(
                            f"Beat {anchor} already occupied, photo {path.name} will be redistributed"
                        )

    # Second pass: distribute clusters chronologically
    unassigned_clusters = []
    for cluster_idx, cluster in enumerate(clusters):
        # Check if any photos in this cluster are already anchored
        cluster_has_anchored = any(
            placement.cluster_id == cluster_idx and placement.is_anchored
            for placement in placements
        )

        if not cluster_has_anchored:
            unassigned_clusters.append((cluster_idx, cluster))

    for cluster_idx, cluster in unassigned_clusters:
        # Calculate preferred beat based on cluster's chronological position
        position = (cluster.representative_date - start_date).total_seconds() / timespan

        # Map to effective beat range
        preferred_beat = start_offset + min(
            int(position * (num_effective_beats - 1)), num_effective_beats - 1
        )

        # Find nearest available beat
        assigned_beat = preferred_beat
        search_radius = 0

        while beat_occupied[assigned_beat] and search_radius < num_effective_beats:
            search_radius += 1
            if search_radius % 2 == 1:
                candidate = preferred_beat - (search_radius // 2 + 1)
            else:
                candidate = preferred_beat + (search_radius // 2)

            if (
                start_offset <= candidate < len(beats) - end_offset
                and not beat_occupied[candidate]
            ):
                assigned_beat = candidate
                break

        if not beat_occupied[assigned_beat]:
            beat_occupied[assigned_beat] = True

            # Place all photos in cluster at this beat
            for path, date, anchor in cluster.photos:
                # Skip if already anchored
                if any(p.asset.path == path and p.is_anchored for p in placements):
                    continue

                asset = MediaAsset.from_photo(
                    path, generate_deterministic_uid(path, id_method)
                )
                placements.append(
                    PhotoPlacement(
                        asset=asset,
                        beat_index=assigned_beat,
                        date=date,
                        is_anchored=False,
                        cluster_id=cluster_idx,
                    )
                )

    echo(f"Placed {len(placements)} photos in {len(clusters)} clusters")
    echo(f"Anchored photos: {anchored_count}")
    return sorted(placements, key=lambda x: (x.beat_index, x.date))


def map_photos_to_beats(
    photos: list[PhotoMetadata],
    beats: list[BeatInfo],
    start_date: datetime,
    end_date: datetime,
    start_offset: int,
    end_offset: int,
    time_gap: TimeGap,
    id_method: str = "inode",
) -> list[PhotoPlacement]:
    """Map photos to beats with optional time gap clustering."""
    # Filter photos within date range
    filtered_photos = [
        (path, date, anchor)
        for path, date, anchor in photos
        if start_date <= date <= end_date
    ]

    if not filtered_photos:
        warning("No photos found within the specified date range")
        return []

    # Use original logic when clustering is disabled
    if time_gap.amount == 0:
        return map_photos_to_beats_original(
            filtered_photos,
            beats,
            start_date,
            end_date,
            start_offset,
            end_offset,
            id_method,
        )

    # Cluster photos and then map clusters to beats
    clusters = cluster_photos_by_time_gap(filtered_photos, time_gap)

    echo(f"Clustered {len(filtered_photos)} photos into {len(clusters)} groups")
    if time_gap.same_period_mode:
        echo(f"Grouping photos by same {time_gap.unit.value}")
    else:
        echo(f"Minimum gap: {time_gap.amount} {time_gap.unit.value}(s)")

    return map_photo_clusters_to_beats(
        clusters, beats, start_date, end_date, start_offset, end_offset, id_method
    )


def calculate_date_ranges(
    photos: list[PhotoMetadata],
    beats: list[BeatInfo],
    start_date: datetime,
    end_date: datetime,
    placements: list[PhotoPlacement],
) -> list[DateRange]:
    """Calculate approximate date ranges for each beat."""
    timespan = Decimal((end_date - start_date).total_seconds())
    beat_ranges: list[DateRange] = []

    # Create mapping of beat index to photo dates
    beat_to_photos: dict[int, list[datetime]] = {}
    for placement in placements:
        if placement.beat_index not in beat_to_photos:
            beat_to_photos[placement.beat_index] = []
        beat_to_photos[placement.beat_index].append(placement.date)

    for i, beat in enumerate(beats):
        if i in beat_to_photos:
            # Use actual photo dates for this beat
            photo_dates = beat_to_photos[i]
            range_start = min(photo_dates)
            range_end = max(photo_dates)
        else:
            # Approximate based on beat position
            beat_position = (
                Decimal(beat.time) / Decimal(beats[-1].time)
                if beats[-1].time > 0
                else Decimal(0)
            )
            microseconds_factor = 1_000_000
            range_duration = timedelta(
                microseconds=int((timespan * Decimal("0.1")) * microseconds_factor)
            )
            range_center = start_date + timedelta(
                microseconds=int((timespan * beat_position) * microseconds_factor)
            )

            range_start = range_center - range_duration / 2
            range_end = range_center + range_duration / 2

        beat_ranges.append(DateRange(range_start, range_end))

    return beat_ranges


def generate_placeholder_image(
    beat_index: int,
    output_dir: Path,
    dimensions: Dimensions,
    date_range: DateRange | None = None,
) -> Path:
    """Generate placeholder image with beat number and date information."""
    # Use smaller dimensions for placeholder efficiency
    width, height = min(dimensions.width, 640), min(dimensions.height, 360)
    placeholder_path = output_dir / f"placeholder_beat_{beat_index + 1:03d}.png"

    # Create image with dark background
    img = Image.new("RGB", (width, height), color=(32, 32, 32))
    draw = ImageDraw.Draw(img)

    # Try to use Courier New (monospace), fall back to default
    try:
        font_size = min(width, height) // 12
        font = ImageFont.truetype("Courier New", font_size)
    except OSError:
        try:
            font = ImageFont.truetype("courier.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()

    # Prepare text content matching title style
    beat_text = str(beat_index + 1)
    date_text = date_range.format_range() if date_range else "Unknown Date"

    # Draw beat number (primary text)
    bbox = draw.textbbox((0, 0), beat_text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    x = (width - text_width) // 2
    y = (height - text_height) // 2 - font_size // 4

    draw.text((x, y), beat_text, fill=(255, 255, 255), font=font)

    # Draw date information below beat number
    if date_range:
        try:
            date_font_size = font_size // 2
            date_font = ImageFont.truetype("Courier New", date_font_size)
        except OSError:
            date_font = font

        date_bbox = draw.textbbox((0, 0), date_text, font=date_font)
        date_width = date_bbox[2] - date_bbox[0]

        date_x = (width - date_width) // 2
        date_y = y + text_height + 10

        draw.text((date_x, date_y), date_text, fill=(200, 200, 200), font=date_font)

    # Save image
    img.save(placeholder_path)
    return placeholder_path


def create_timeline_project(
    photo_dir: Path,
    soundtrack_path: Path,
    bpm: Decimal,
    project_name: str,
    frame_rate: int = DEFAULT_FPS,
    gap_sec: Seconds = DEFAULT_GAP_SEC,
    start_offset_beats: int = DEFAULT_START_OFFSET_BEATS,
    end_offset_beats: int = DEFAULT_END_OFFSET_BEATS,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    placeholder_mode: PlaceholderMode = PlaceholderMode.NONE,
    time_gap: TimeGap | None = None,
    id_method: str = "inode",
    supported_extensions: frozenset[str] = SUPPORTED_IMAGE_EXTENSIONS,
) -> TimelineProject:
    """Create NLE-agnostic timeline project from photos and soundtrack."""

    if time_gap is None:
        time_gap = TimeGap.none()

    # Extract audio duration
    duration_sec = get_audio_duration(soundtrack_path)
    echo(f"Soundtrack duration: {duration_sec:.2f} seconds")

    # Load photos and their dates
    photo_metadata = load_photos(photo_dir, supported_extensions)

    # Override date range if specified
    if start_date is None:
        start_date = photo_metadata[0][1]
    if end_date is None:
        end_date = photo_metadata[-1][1]

    # Generate beats based on BPM and duration
    beat_duration = 60 / bpm
    num_beats = int(duration_sec / beat_duration) + 1
    beat_times = [Decimal(i) * beat_duration for i in range(num_beats)]

    # Create beat info objects (without date ranges yet)
    beats = [BeatInfo(index=i, time=time) for i, time in enumerate(beat_times)]

    echo(f"Generated {num_beats} beats at {bpm} BPM")

    # Map photos to beats with optional time gap clustering
    placements = map_photos_to_beats(
        photo_metadata,
        beats,
        start_date,
        end_date,
        start_offset_beats,
        end_offset_beats,
        time_gap,
        id_method,
    )

    # Calculate date ranges and update beats
    date_ranges = calculate_date_ranges(
        photo_metadata, beats, start_date, end_date, placements
    )
    beats = [
        BeatInfo(index=beat.index, time=beat.time, date_range=date_ranges[i])
        for i, beat in enumerate(beats)
    ]

    # Detect dimensions from first photo or use defaults
    dimensions = None
    for placement in placements:
        if placement.asset.dimensions:
            dimensions = placement.asset.dimensions
            break

    if not dimensions:
        warning("Could not detect dimensions from photos. Using default 1920x1080.")
        dimensions = Dimensions(1920, 1080)

    # Create timeline elements
    elements: list[TimelineElement] = []
    markers: list[TimelineMarker] = []

    # Calculate element durations
    element_durations = calculate_element_durations(beat_times, duration_sec)

    # Create audio track element
    audio_asset = MediaAsset.from_audio(
        soundtrack_path,
        generate_deterministic_uid(soundtrack_path, id_method),
        duration_sec * 1000,
    )

    elements.append(
        TimelineElement(
            asset=audio_asset,
            time_range=TimeRange(gap_sec * 1000, duration_sec * 1000),
            track=-1,  # Convention: negative tracks for audio
            media_type=MediaType.AUDIO,
        )
    )

    # Create photo elements
    for placement in placements:
        beat_start_sec = beats[placement.beat_index].time
        duration_sec = element_durations[placement.beat_index]

        elements.append(
            TimelineElement(
                asset=placement.asset,
                time_range=TimeRange(
                    start=beat_start_sec * 1000 + gap_sec * 1000,
                    duration=duration_sec * 1000,
                ),
                track=1,
                media_type=MediaType.VIDEO,
                metadata={
                    "beat_index": placement.beat_index,
                    "is_anchored": placement.is_anchored,
                    "photo_date": placement.date.isoformat(),
                    "cluster_id": placement.cluster_id,
                },
            )
        )

    # Create beat markers
    effective_beats = beats[start_offset_beats : len(beats) - end_offset_beats]
    for i, beat in enumerate(effective_beats):
        absolute_beat_index = i + start_offset_beats
        markers.append(
            TimelineMarker(
                position=beat.time * 1000 + gap_sec * 1000,
                name=f"Beat {absolute_beat_index + 1}",
            )
        )

    # Create timeline project
    return TimelineProject(
        name=project_name,
        duration=(duration_sec + gap_sec) * 1000,
        frame_rate=frame_rate,
        dimensions=dimensions,
        elements=elements,
        markers=markers,
        beats=beats,
        photo_placements=placements,
        gap_duration=gap_sec * 1000,
        start_offset_beats=start_offset_beats,
        end_offset_beats=end_offset_beats,
        placeholder_mode=placeholder_mode,
        time_gap=time_gap,
    )
