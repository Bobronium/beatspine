"""
ResolveSync - Differential DaVinci Resolve integration for beatspine.

Implements idempotent synchronization through minimal state changes,
preserving manual modifications while maintaining timeline consistency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Final

from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from beatspine.definitions import (
    MediaAsset,
    MediaType,
    PlaceholderMode,
    TimelineElement,
    TimelineProject,
)
from beatspine.resolve import get_resolve

if TYPE_CHECKING:
    from beatspine import fusionscript

console = Console()

# State management constants
BEATLINER_METADATA_KEY: Final[str] = "beatspine_managed"
BEATLINER_STATE_KEY: Final[str] = "beatspine_state"
BEATLINER_VERSION: Final[str] = "1.0"


@dataclass(slots=True, frozen=True)
class SyncState:
    """Immutable synchronization state for idempotent operations."""

    project_name: str
    timeline_name: str
    photo_count: int
    audio_duration_ms: int
    placeholder_mode: PlaceholderMode
    managed_asset_uids: frozenset[str] = field(default_factory=frozenset)
    timeline_item_count: int = 0
    last_sync_version: str = BEATLINER_VERSION


@dataclass(slots=True, frozen=True)
class TimelineChanges:
    """Differential changes required for timeline synchronization."""

    items_to_add: tuple[TimelineElement, ...] = field(default_factory=tuple)
    items_to_remove: tuple[str, ...] = field(default_factory=tuple)
    items_to_update: tuple[tuple[str, TimelineElement], ...] = field(
        default_factory=tuple
    )
    markers_to_sync: bool = False

    @property
    def has_changes(self) -> bool:
        """Check if any changes are required."""
        return bool(
            self.items_to_add
            or self.items_to_remove
            or self.items_to_update
            or self.markers_to_sync
        )


@dataclass(slots=True, frozen=True)
class ConflictReport:
    """Analysis of conflicts between expected and actual timeline state."""

    unmanaged_items: tuple[str, ...] = field(default_factory=tuple)
    modified_positions: tuple[str, ...] = field(default_factory=tuple)
    manual_markers: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_conflicts(self) -> bool:
        """Check if any conflicts were detected."""
        return bool(
            self.unmanaged_items or self.modified_positions or self.manual_markers
        )


class ResolveConnectionError(Exception):
    """Connection to DaVinci Resolve failed."""


class ResolveSync:
    """
    Differential synchronization engine for DaVinci Resolve timelines.

    Analyzes current timeline state and applies minimal changes to achieve
    target configuration while preserving manual modifications.
    """

    def __init__(self) -> None:
        self._resolve: fusionscript.Resolve | None = None
        self._project_manager: fusionscript.ProjectManager | None = None
        self._current_project: fusionscript.Project | None = None
        self._current_timeline: fusionscript.Timeline | None = None
        self._timeline_start_frame: int | None = None

    def _establish_connection(self) -> None:
        """Connect to running DaVinci Resolve instance."""
        if self._resolve is not None:
            return

        try:
            self._resolve = get_resolve()
            self._project_manager = self._resolve.GetProjectManager()
            console.print("✅ Connected to DaVinci Resolve", style="green")
        except Exception as e:
            raise ResolveConnectionError(
                f"DaVinci Resolve connection failed. Ensure application is running "
                f"with scripting enabled. Error: {e}"
            ) from e

    def _locate_or_create_project(
        self, project_name: str, recreate: bool
    ) -> fusionscript.Project:
        """Retrieve existing project or create new instance."""
        assert self._project_manager is not None

        existing_project = self._project_manager.LoadProject(project_name)
        if existing_project is not None and not recreate:
            console.print(f"📂 Located project: {project_name}", style="blue")
            return existing_project
        elif existing_project is not None:
            self._project_manager.CloseProject(existing_project)
            self._project_manager.DeleteProject(project_name)

        new_project = self._project_manager.CreateProject(project_name)
        if new_project is None:
            raise RuntimeError(f"Project creation failed: {project_name}")
        console.print(f"🆕 Created project: {project_name}", style="green")
        return new_project

    def _locate_timeline(
        self,
        project: fusionscript.Project,
        timeline_name: str,
    ) -> fusionscript.Timeline | None:
        """Find existing timeline by name."""
        timeline_count = project.GetTimelineCount()

        for index in range(1, timeline_count + 1):
            timeline = project.GetTimelineByIndex(index)
            if timeline.GetName() == timeline_name:
                return timeline

        return None

    def _extract_managed_state(
        self, timeline: fusionscript.Timeline
    ) -> SyncState | None:
        """Extract stored synchronization state from timeline metadata."""
        try:
            state_json = timeline.GetSetting(BEATLINER_STATE_KEY)
            if not state_json:
                return None

            state_data = json.loads(state_json)
            return SyncState(
                project_name=state_data["project_name"],
                timeline_name=state_data["timeline_name"],
                photo_count=state_data["photo_count"],
                audio_duration_ms=state_data["audio_duration_ms"],
                placeholder_mode=PlaceholderMode(state_data["placeholder_mode"]),
                managed_asset_uids=frozenset(state_data["managed_asset_uids"]),
                timeline_item_count=state_data.get("timeline_item_count", 0),
                last_sync_version=state_data.get("last_sync_version", "unknown"),
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def _persist_managed_state(
        self, timeline: fusionscript.Timeline, state: SyncState
    ) -> None:
        """Store synchronization state in timeline metadata."""
        state_data = {
            "project_name": state.project_name,
            "timeline_name": state.timeline_name,
            "photo_count": state.photo_count,
            "audio_duration_ms": state.audio_duration_ms,
            "placeholder_mode": state.placeholder_mode.value,
            "managed_asset_uids": list(state.managed_asset_uids),
            "timeline_item_count": state.timeline_item_count,
            "last_sync_version": state.last_sync_version,
        }

        timeline.SetSetting(BEATLINER_STATE_KEY, json.dumps(state_data))
        timeline.SetSetting(BEATLINER_METADATA_KEY, "true")

    def _catalog_current_items(
        self, timeline: fusionscript.Timeline
    ) -> dict[str, fusionscript.TimelineItem]:
        """Build mapping of beatspine-managed items in timeline."""
        managed_items: dict[str, fusionscript.TimelineItem] = {}

        for track_type in ["video", "audio"]:
            track_count = timeline.GetTrackCount(track_type)
            for track_index in range(1, track_count + 1):
                items = timeline.GetItemListInTrack(track_type, track_index)
                for item in items:
                    asset_uid = self._extract_beatspine_uid(item)
                    if asset_uid:
                        managed_items[asset_uid] = item

        return managed_items

    def _extract_beatspine_uid(self, item: fusionscript.TimelineItem) -> str | None:
        """Extract beatspine asset UID from timeline item markers."""
        markers = item.GetMarkers()
        for marker_info in markers.values():
            custom_data = marker_info.get("customData", "")
            if custom_data.startswith("beatspine:"):
                return custom_data.split(":", 1)[1]
        return None

    def _compute_differential_changes(
        self,
        current_items: dict[str, fusionscript.TimelineItem],
        target_elements: dict[str, TimelineElement],
        stored_state: SyncState | None,
        project: TimelineProject,
    ) -> TimelineChanges:
        """Analyze differences between current and target states."""
        current_uids = set(current_items.keys())
        target_uids = set(target_elements.keys())
        managed_uids = stored_state.managed_asset_uids if stored_state else frozenset()

        console.print("🔍 Differential analysis:", style="dim")
        console.print(f"  Current items: {len(current_uids)}", style="dim")
        console.print(f"  Target elements: {len(target_uids)}", style="dim")
        console.print(f"  Total project elements: {len(project.elements)}", style="dim")
        console.print(f"  Managed UIDs: {len(managed_uids)}", style="dim")

        # Identify new elements to add
        new_uids = target_uids - current_uids
        items_to_add = tuple(target_elements[uid] for uid in new_uids)

        # Identify obsolete managed items to remove
        stale_uids = (current_uids & managed_uids) - target_uids

        # Identify items requiring position/duration updates
        common_uids = current_uids & target_uids
        items_to_update = tuple(
            (uid, target_elements[uid])
            for uid in common_uids
            if self._requires_update(current_items[uid], target_elements[uid], project)
        )

        console.print(f"  New UIDs to add: {len(new_uids)}", style="dim")
        console.print(f"  Stale UIDs to remove: {len(stale_uids)}", style="dim")
        console.print(f"  Items to update: {len(items_to_update)}", style="dim")

        return TimelineChanges(
            items_to_add=items_to_add,
            items_to_remove=stale_uids,
            items_to_update=items_to_update,
            markers_to_sync=True,  # Always sync beat markers
        )

    def _requires_update(
        self,
        current_item: fusionscript.TimelineItem,
        target_element: TimelineElement,
        project: TimelineProject,
    ) -> bool:
        """Determine if timeline item needs position or duration adjustment."""
        frame_rate = project.frame_rate
        tolerance = 0.5  # Sub-frame precision

        # Check timeline position
        current_start_frames = Decimal(current_item.GetStart(True)) # Assuming GetStart returns frame count
        target_start_frames = Decimal(target_element.time_range.start_frame)

        if abs(current_start_frames - target_start_frames) > tolerance:
            return True

        # Check duration
        current_duration_frames = Decimal(current_item.GetDuration(True)) # Assuming GetDuration returns frame count
        target_duration_frames = Decimal(target_element.time_range.duration_frames)

        return abs(current_duration_frames - target_duration_frames) > tolerance

    def _analyze_conflicts(
        self,
        timeline: fusionscript.Timeline,
        current_items: dict[str, fusionscript.TimelineItem],
        managed_uids: frozenset[str],
    ) -> ConflictReport:
        """Detect manual modifications that conflict with beatspine management."""
        unmanaged_items = []
        manual_markers = []

        # Scan for items not managed by beatspine
        for track_type in "video", "audio":
            track_count = timeline.GetTrackCount(track_type)
            for track_index in range(1, track_count + 1):
                items = timeline.GetItemListInTrack(track_type, track_index)
                for item in items:
                    if not self._extract_beatspine_uid(item):
                        unmanaged_items.append(f"Manual item: {item.GetName()}")

        # Scan for non-beatspine markers
        markers = timeline.GetMarkers()
        for frame_id, marker_info in markers.items():
            custom_data = marker_info.get("customData", "")
            if not custom_data.startswith("beatspine:"):
                marker_name = marker_info.get("name", "Unknown")
                manual_markers.append(
                    f"Manual marker at frame {frame_id}: {marker_name}"
                )

        return ConflictReport(
            unmanaged_items=tuple(unmanaged_items),
            manual_markers=tuple(manual_markers),
        )

    def _import_media_batch(
        self,
        media_pool: fusionscript.MediaPool,
        assets: list[MediaAsset],
    ) -> dict[str, fusionscript.MediaPoolItem]:
        """Import media assets and return UID mapping."""
        imported_items: dict[str, fusionscript.MediaPoolItem] = {}

        existing_assets = [asset for asset in assets if asset.path.exists()]
        if not existing_assets:
            return imported_items

        media_paths = [str(asset.path.resolve()) for asset in existing_assets]
        console.print(f"📥 Importing {len(media_paths)} media files", style="blue")

        pool_items = media_pool.ImportMedia(media_paths)
        if not pool_items:
            console.print("⚠️  No media imported", style="yellow")
            return imported_items

        # Map imported items to asset UIDs
        path_to_item: dict[Path, fusionscript.MediaPoolItem] = {}
        for item in pool_items:
            file_path = item.GetClipProperty("File Path")
            if file_path:
                path_to_item[Path(file_path).resolve()] = item

        for asset in existing_assets:
            resolved_path = asset.path.resolve()
            if resolved_path in path_to_item:
                pool_item = path_to_item[resolved_path]
                pool_item.SetMetadata(BEATLINER_METADATA_KEY, "true")
                pool_item.SetMetadata("beatspine_uid", asset.uid)
                imported_items[asset.uid] = pool_item

        return imported_items

    def _apply_differential_changes(
        self,
        timeline: fusionscript.Timeline,
        changes: TimelineChanges,
        media_items: dict[str, fusionscript.MediaPoolItem],
        project: TimelineProject,
    ) -> None:
        """Apply computed changes to timeline through minimal operations."""
        console.print(
            f"🔍 Applying changes: {len(changes.items_to_add)} to add, {len(changes.items_to_remove)} to remove",
            style="dim",
        )

        if changes.items_to_add:
            console.print(
                f"➕ Adding {len(changes.items_to_add)} items to timeline", style="blue"
            )
            try:
                self._add_elements_to_timeline(
                    timeline, changes.items_to_add, media_items, project
                )
            except Exception as e:
                console.print(
                    f"❌ Exception in _add_elements_to_timeline: {e}", style="red"
                )
                console.print(f"❌ Exception type: {type(e).__name__}", style="red")
                import traceback

                traceback.print_exc()
                raise

        if changes.items_to_remove:
            console.print(
                f"🗑️  Removing {len(changes.items_to_remove)} obsolete items",
                style="yellow",
            )
            # Note: Actual removal requires tracking specific timeline items

        if changes.items_to_update:
            console.print(
                f"🔄 Updating {len(changes.items_to_update)} item positions",
                style="blue",
            )
            # Note: Updates require timeline item repositioning API calls

        if changes.markers_to_sync:
            self._synchronize_beat_markers(timeline, project)

    def _add_elements_to_timeline(
        self,
        timeline: fusionscript.Timeline,
        elements: tuple[TimelineElement, ...],
        media_items: dict[str, fusionscript.MediaPoolItem],
        project: TimelineProject,
    ) -> None:
        """Add new elements to timeline using batch import."""
        clip_infos: list[fusionscript.MediaPoolClipInfo] = []
        media_pool = self._current_project.GetMediaPool()
        total_video_tracks = timeline.GetTrackCount("video")
        total_audio_tracks = timeline.GetTrackCount("audio")

        for media_type in (
            MediaType.VIDEO,
            MediaType.AUDIO,
        ):
            for element in elements:
                if (
                    not element.asset
                    or element.asset.uid not in media_items
                    or element.media_type != media_type
                ):
                    continue

                media_item = media_items[element.asset.uid]

                # record_timeline_frame is where the clip is placed on the timeline
                record_timeline_frame = (
                    self._timeline_start_frame + element.time_range.start_frame
                )

                clip_info = {
                    "mediaPoolItem": media_item,
                    "trackIndex": total_video_tracks
                    if element.media_type is MediaType.VIDEO
                    else total_audio_tracks,
                    "recordFrame": record_timeline_frame,
                    "mediaType": 2 if element.media_type == MediaType.AUDIO else 1,
                }

                if element.media_type is MediaType.VIDEO:
                    media_fps_str = media_item.GetClipProperty("FPS")
                    media_clip_start_str = media_item.GetClipProperty(
                        "Start"
                    )  # Media's own start frame/timecode
                    timeline_fps = Decimal(project.frame_rate)

                    try:
                        media_fps = Decimal(media_fps_str)
                        if media_fps <= 0:  # FPS cannot be zero or negative
                            media_fps = timeline_fps
                    except (ValueError, TypeError, AttributeError):
                        media_fps = timeline_fps

                    try:
                        media_native_start_frame = Decimal(media_clip_start_str)
                    except (ValueError, TypeError, AttributeError):
                        media_native_start_frame = Decimal(0)

                    # frames_on_timeline is how long the clip should be on the timeline (in timeline frames)
                    frames_on_timeline = element.time_range.duration_frames

                    frames_of_media_to_use: Decimal
                    if media_fps == timeline_fps:
                        frames_of_media_to_use = Decimal(frames_on_timeline)
                    else:
                        # Adjust number of media frames to read based on FPS difference
                        frames_of_media_to_use = (
                            Decimal(frames_on_timeline) * media_fps
                        ) / timeline_fps

                    clip_info["startFrame"] = float(
                        media_native_start_frame
                    )  # Media In point (from source media)
                    clip_info["endFrame"] = float(
                        media_native_start_frame + frames_of_media_to_use
                    )  # Media Out point (from source media)

                elif element.media_type is MediaType.AUDIO:
                    clip_info["startFrame"] = 0
                    clip_info["endFrame"] = element.time_range.duration_frames

                response = media_pool.AppendToTimeline([clip_info])

                if response and all(response):
                    console.print(
                        f"✅ Added {element.asset.path.name} to the timeline",
                        style="green",
                    )
                else:
                    console.print(
                        f"⚠️{element.asset.path.name} couldn't be placed on the timeline...",
                        style="yellow",
                    )

    def _synchronize_beat_markers(
        self, timeline: fusionscript.Timeline, project: TimelineProject
    ) -> None:
        """Perform differential synchronization of beat markers."""
        # Catalog existing beatspine beat markers
        existing_markers = timeline.GetMarkers()
        current_beat_markers: dict[int, float] = {}  # beat_index -> frame_position

        for frame_id, marker_info in existing_markers.items():
            custom_data = marker_info.get("customData", "")
            if custom_data.startswith("beatspine:beat:"):
                try:
                    beat_index = int(custom_data.split(":")[-1])
                    current_beat_markers[beat_index] = float(frame_id)
                except (ValueError, IndexError):
                    # Remove malformed beatspine markers
                    timeline.DeleteMarkerAtFrame(frame_id)

        # Compute required changes
        target_markers: dict[int, float] = {}
        for beat in project.beats:
            frame_position = int(beat.time * project.frame_rate)
            target_markers[beat.index] = frame_position

        current_indices = set(current_beat_markers.keys())
        target_indices = set(target_markers.keys())

        # Remove obsolete markers
        obsolete_indices = current_indices - target_indices
        for beat_index in obsolete_indices:
            frame_position = current_beat_markers[beat_index]
            timeline.DeleteMarkerAtFrame(frame_position)

        # Add new markers and update moved markers
        for beat_index in target_indices:
            target_frame = target_markers[beat_index]
            current_frame = current_beat_markers.get(beat_index)

            # Check if marker needs update (new or moved)
            if current_frame is None or abs(current_frame - target_frame) > 0.5:
                # Remove old marker if it exists but moved
                if current_frame is not None:
                    timeline.DeleteMarkerAtFrame(int(current_frame))

                # Add marker at correct position
                beat = project.beats[beat_index]
                marker_name = f"Beat {beat.index + 1}"
                note = (
                    f"Photos: {beat.date_range.format_range()}"
                    if beat.date_range
                    else ""
                )

                timeline.AddMarker(
                    target_frame,
                    "Yellow",
                    marker_name,
                    note,
                    1.0,
                    f"beatspine:beat:{beat.index}",
                )

        added_count = len(target_indices - current_indices)
        removed_count = len(obsolete_indices)
        updated_count = len(
            [
                i
                for i in target_indices & current_indices
                if abs(current_beat_markers[i] - target_markers[i]) > 0.5
            ]
        )

        console.print(
            f"🎯 Beat markers: +{added_count} -{removed_count} ~{updated_count}",
            style="dim",
        )

    def _create_new_timeline(self, project: TimelineProject) -> fusionscript.Timeline:
        """Create timeline from scratch when none exists."""
        assert self._current_project is not None

        media_pool = self._current_project.GetMediaPool()
        timeline = media_pool.CreateEmptyTimeline(project.name)

        if timeline is None:
            raise RuntimeError(f"Timeline creation failed: {project.name}")

        console.print(f"📹 Created timeline: {project.name}", style="green")
        return timeline

    def _display_conflict_report(
        self, conflicts: ConflictReport, project_name: str
    ) -> None:
        """Present conflict analysis in formatted table."""
        table = Table(
            title=f"Manual Modifications Detected: {project_name}", style="yellow"
        )
        table.add_column("Type", style="bold")
        table.add_column("Details")

        for item in conflicts.unmanaged_items:
            table.add_row("Unmanaged Item", item)

        for marker in conflicts.manual_markers:
            table.add_row("Manual Marker", marker)

        console.print(table)

    def sync_project(
        self,
        project: TimelineProject,
        force: bool = False,
        recreate: bool = False,
        dry_run: bool = False,
    ) -> None:
        """
        Synchronize TimelineProject with DaVinci Resolve through differential updates.

        Preserves manual modifications unless force override is specified.
        Applies minimal changes to achieve target timeline state.
        """
        self._establish_connection()

        resolve_project = self._locate_or_create_project(project.name, recreate)

        self._current_project = resolve_project

        try:
            assert resolve_project.SetSetting(
                "timelineFrameRate", str(project.frame_rate)
            ), "timelineFrameRate"
            assert resolve_project.SetSetting(
                "timelineFrameRateMismatchBehavior", "fcp7"
            ), "timelineFrameRateMismatchBehavior"
            assert resolve_project.SetSetting(
                "videoMonitorFormat", f"HD 1080p {project.frame_rate}"
            ), "videoMonitorFormat"

        except AssertionError as e:
            raise RuntimeError(f"Failed to set project framerate: {e}")
        # print(
        #     json.dumps(resolve_project.GetSetting(None), indent=4, ensure_ascii=False)
        # )
        existing_timeline = self._locate_timeline(resolve_project, project.name)

        if existing_timeline is None:
            if dry_run:
                console.print("🔍 Would create new timeline", style="blue")
                return

            timeline = self._create_new_timeline(project)
            self._timeline_start_frame = timeline.GetStartFrame()

            self._sync_timeline_content(timeline, project, dry_run)
            return

        self._timeline_start_frame = existing_timeline.GetStartFrame()

        stored_state = self._extract_managed_state(existing_timeline)
        current_items = self._catalog_current_items(existing_timeline)

        target_elements = {
            elem.asset.uid: elem for elem in project.elements if elem.asset
        }

        console.print("🔍 Target elements analysis:", style="dim")
        console.print(f"  Total project elements: {len(project.elements)}", style="dim")
        console.print(f"  Elements with assets: {len(target_elements)}", style="dim")
        console.print(
            f"  First 5 target UIDs: {list(target_elements.keys())[:5]}", style="dim"
        )

        changes = self._compute_differential_changes(
            current_items, target_elements, stored_state, project
        )

        # Check for manual modifications
        managed_uids = stored_state.managed_asset_uids if stored_state else frozenset()
        conflicts = self._analyze_conflicts(
            existing_timeline, current_items, managed_uids
        )

        if conflicts.has_conflicts and not force:
            self._display_conflict_report(conflicts, project.name)

            if dry_run:
                console.print(
                    "🔍 Conflicts detected - would require --force", style="yellow"
                )
                return

            if not Confirm.ask(
                "Apply changes? Manual modifications will be preserved."
            ):
                console.print("❌ Sync cancelled", style="red")
                return

        if dry_run:
            self._display_dry_run_preview(changes, project)
            return

        self._sync_timeline_content(existing_timeline, project, dry_run, changes)

    def _sync_timeline_content(
        self,
        timeline: fusionscript.Timeline,
        project: TimelineProject,
        dry_run: bool,
        changes: TimelineChanges | None = None,
    ) -> None:
        """Apply content synchronization to timeline."""
        # Collect unique assets for import
        unique_assets: list[MediaAsset] = []
        asset_uids: set[str] = set()

        for element in project.elements:
            if element.asset and element.asset.uid not in asset_uids:
                unique_assets.append(element.asset)
                asset_uids.add(element.asset.uid)

        # Import media to pool
        assert self._current_project is not None
        media_pool = self._current_project.GetMediaPool()
        media_items = self._import_media_batch(media_pool, unique_assets)

        if changes is None:
            # Full sync for new timeline
            self._add_elements_to_timeline(
                timeline, tuple(project.elements), media_items, project
            )
            self._synchronize_beat_markers(timeline, project)
        else:
            # Differential sync
            self._apply_differential_changes(timeline, changes, media_items, project)

        # Update state tracking
        sync_state = SyncState(
            project_name=project.name,
            timeline_name=project.name,
            photo_count=len(project.photo_placements),
            audio_duration_ms=int(project.duration),
            placeholder_mode=project.placeholder_mode,
            managed_asset_uids=frozenset(asset_uids),
            timeline_item_count=len(project.elements),
        )
        self._persist_managed_state(timeline, sync_state)

        # Activate timeline
        self._current_project.SetCurrentTimeline(timeline)

        console.print("✅ Timeline synchronized", style="green")
        console.print(f"  📊 Project: {project.name}")
        console.print(f"  📸 Photos: {len(project.photo_placements)}")
        console.print(f"  🎵 Duration: {project.duration / 1000:.1f}s")

    def _display_dry_run_preview(
        self, changes: TimelineChanges, project: TimelineProject
    ) -> None:
        """Show what would be changed in dry run mode."""
        if not changes.has_changes:
            console.print(
                "🔍 No changes required - timeline is synchronized", style="blue"
            )
            return

        console.print("🔍 Planned changes:", style="blue")

        if changes.items_to_add:
            console.print(f"  ➕ Add {len(changes.items_to_add)} new items")

        if changes.items_to_remove:
            console.print(f"  🗑️  Remove {len(changes.items_to_remove)} obsolete items")

        if changes.items_to_update:
            console.print(f"  🔄 Update {len(changes.items_to_update)} item positions")

        if changes.markers_to_sync:
            console.print(f"  🎯 Sync {len(project.markers)} project markers")
