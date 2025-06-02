#!/usr/bin/env python3
"""
Beatliner CLI - Beat-synchronized photo timeline generator for DaVinci Resolve.

This tool creates timelines where photos are synchronized to musical beats,
with two operational modes:
- export: Generate XML files for manual import
- sync: Direct integration with running DaVinci Resolve instance
"""

from __future__ import annotations

import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Final

import click
from rich.console import Console
from rich.progress import Progress

from beatspine.constants import (
    DEFAULT_END_OFFSET_BEATS,
    DEFAULT_FPS,
    DEFAULT_GAP_SEC,
    DEFAULT_OUTPUT,
    DEFAULT_PLACEHOLDERS,
    DEFAULT_START_OFFSET_BEATS,
    DEFAULT_TIME_GAP,
    SUPPORTED_AUDIO_EXTENSIONS,
    SUPPORTED_IMAGE_EXTENSIONS,
)
from beatspine.core import create_timeline_project
from beatspine.definitions import PlaceholderMode, TimeGap
from beatspine.exporters.fcpx import FCPXMLExporter
from beatspine.exporters.resolve import ResolveExporter
from beatspine.resolve_sync import ResolveSync


console = Console()

# CLI Constants
SUPPORTED_NLES: Final[tuple[str, ...]] = ("fcpx", "resolve", "both")
ID_METHODS: Final[tuple[str, ...]] = ("inode", "content", "path")


def echo(message: str, **kwargs) -> None:
    """Output to console with rich formatting."""
    console.print(message, **kwargs)


def error(message: str, code: int = 1) -> None:
    """Output error and exit."""
    console.print(f"Error: {message}", style="bold red")
    sys.exit(code)


def handle_exception(verbose: bool, exc: Exception) -> None:
    """Handle exceptions with optional verbose traceback."""
    if verbose:
        console.print_exception(show_locals=True, width=300, max_frames=3)
        sys.exit(1)
    else:
        error(str(exc))


def validate_audio_file(
    ctx: click.Context, param: click.Parameter, value: Path
) -> Path:
    """Validate audio file exists and has supported extension."""
    if not value.exists():
        raise click.BadParameter(f"Audio file not found: {value}")

    if value.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
        raise click.BadParameter(f"Unsupported audio format: {value.suffix}")

    return value


def validate_photo_dir(ctx: click.Context, param: click.Parameter, value: Path) -> Path:
    """Validate photo directory exists."""
    if not value.is_dir():
        raise click.BadParameter(f"Photo directory not found: {value}")

    return value


def parse_date(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> datetime | None:
    """Parse ISO date string."""
    if not value:
        return None

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise click.BadParameter(
            f"Invalid date format: {value}. Use ISO format (YYYY-MM-DD)"
        )


def parse_extensions(extensions_str: str | None) -> frozenset[str]:
    """Parse comma-separated extensions."""
    if not extensions_str:
        return SUPPORTED_IMAGE_EXTENSIONS

    extensions = [ext.strip().lower() for ext in extensions_str.split(",")]
    return frozenset(ext if ext.startswith(".") else f".{ext}" for ext in extensions)


# Common options for both commands
photo_dir_option = click.option(
    "--dir",
    "photo_dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    callback=validate_photo_dir,
    help="Directory containing photos",
)

soundtrack_option = click.option(
    "--soundtrack",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    callback=validate_audio_file,
    help="Audio file (.m4a, .mp3, .wav, .aac) for timeline duration and sync",
)

bpm_option = click.option(
    "--bpm",
    required=True,
    type=Decimal,
    help="Beats per minute for timeline synchronization",
)

verbose_option = click.option(
    "--verbose", is_flag=True, help="Show full tracebacks on errors"
)

timing_options = [
    click.option(
        "--start-offset-beats",
        default=DEFAULT_START_OFFSET_BEATS,
        help=f"Number of beats to skip at start (default: {DEFAULT_START_OFFSET_BEATS})",
    ),
    click.option(
        "--end-offset-beats",
        default=DEFAULT_END_OFFSET_BEATS,
        help=f"Number of beats to skip at end (default: {DEFAULT_END_OFFSET_BEATS})",
    ),
    click.option(
        "--start-date",
        callback=parse_date,
        help="Start date in ISO format (YYYY-MM-DD)",
    ),
    click.option(
        "--end-date", callback=parse_date, help="End date in ISO format (YYYY-MM-DD)"
    ),
    click.option(
        "--time-gap",
        default=DEFAULT_TIME_GAP,
        help=(
            "Time gap for photo clustering. Format: 'amount-unit' or 'amount-unit-same'. "
            "Units: second, minute, hour, day, week, month, year. "
            "Examples: '1-day' (minimum 1 day gap), '1-year-same' (group by same year). "
            f"Default: {DEFAULT_TIME_GAP}"
        ),
    ),
]

media_options = [
    click.option(
        "--extensions",
        help=f"Comma-separated image extensions (default: {','.join(SUPPORTED_IMAGE_EXTENSIONS)})",
    ),
    click.option(
        "--gap-sec",
        default=DEFAULT_GAP_SEC,
        type=Decimal,
        help=f"Gap duration in seconds (default: {DEFAULT_GAP_SEC})",
    ),
    click.option(
        "--frames",
        "frame_rate",
        default=DEFAULT_FPS,
        help=f"Frames per second (default: {DEFAULT_FPS})",
    ),
    click.option(
        "--name", "project_name", default="PhotoTimeline", help="Project name"
    ),
    click.option(
        "--placeholders",
        type=click.Choice(["image", "title", "captions", "missing", "none"]),
        default=DEFAULT_PLACEHOLDERS,
        help=(
            "Placeholder mode for empty beats: "
            "'image' generates PIL placeholder images, "
            "'title' uses text overlays, "
            "'captions' creates subtitle-style captions, "
            "'missing' creates missing media indicators (fastest), "
            f"'none' skips placeholders entirely (default: {DEFAULT_PLACEHOLDERS})"
        ),
    ),
    click.option(
        "--id-method",
        type=click.Choice(ID_METHODS),
        default="inode",
        help="Method for generating deterministic UIDs",
    ),
]


def add_options(options):
    """Decorator to add multiple options to a command."""

    def decorator(func):
        for option in reversed(options):
            func = option(func)
        return func

    return decorator


@click.group()
@click.version_option()
def main():
    """Beatliner - Beat-synchronized photo timeline generator for DaVinci Resolve."""
    pass


@main.command()
@photo_dir_option
@soundtrack_option
@bpm_option
@add_options(timing_options)
@add_options(media_options)
@verbose_option
@click.option(
    "--nle",
    type=click.Choice(SUPPORTED_NLES),
    default="fcpx",
    help="Target NLE: fcpx (Final Cut Pro), resolve (DaVinci Resolve), or both",
)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default=Path(DEFAULT_OUTPUT),
    help=f"Output file path (default: {DEFAULT_OUTPUT})",
)
def export(
    photo_dir: Path,
    soundtrack: Path,
    bpm: Decimal,
    start_offset_beats: int,
    end_offset_beats: int,
    start_date: datetime | None,
    end_date: datetime | None,
    time_gap: str,
    extensions: str | None,
    gap_sec: Decimal,
    frame_rate: int,
    project_name: str,
    placeholders: str,
    id_method: str,
    verbose: bool,
    nle: str,
    output: Path,
) -> None:
    """Export timeline to XML files for manual import into NLE."""

    try:
        # Parse and validate inputs
        supported_extensions = parse_extensions(extensions)
        placeholder_mode = PlaceholderMode(placeholders)

        try:
            parsed_time_gap = TimeGap.parse(time_gap)
        except ValueError as e:
            error(f"Invalid time gap: {e}")

        # Create timeline project
        with Progress() as progress:
            task = progress.add_task("Creating timeline project...", total=None)

            project = create_timeline_project(
                photo_dir=photo_dir,
                soundtrack_path=soundtrack,
                bpm=bpm,
                project_name=project_name,
                frame_rate=frame_rate,
                gap_sec=gap_sec,
                start_offset_beats=start_offset_beats,
                end_offset_beats=end_offset_beats,
                start_date=start_date,
                end_date=end_date,
                placeholder_mode=placeholder_mode,
                time_gap=parsed_time_gap,
                id_method=id_method,
                supported_extensions=supported_extensions,
            )
            progress.update(task, completed=True)

        # Export to selected NLE(s)
        output_dir = output.parent if output.parent != Path() else Path.cwd()

        if nle in {"fcpx", "both"}:
            fcpx_output = (
                output.with_suffix(".fcpxml")
                if nle == "fcpx"
                else output_dir / f"{project_name}.fcpxml"
            )
            exporter = FCPXMLExporter(output_dir)
            exporter.export(project, fcpx_output)

        if nle in {"resolve", "both"}:
            resolve_output = (
                output.with_suffix(".xml")
                if nle == "resolve"
                else output_dir / f"{project_name}_resolve.xml"
            )
            exporter = ResolveExporter(output_dir)
            exporter.export(project, resolve_output)

        # Success messaging
        echo(
            f"✅ Created timeline with {len(project.photo_placements)} photos and audio track",
            style="green",
        )

        performance_messages = {
            PlaceholderMode.NONE: "No placeholders - optimal performance",
            PlaceholderMode.MISSING: "Missing media placeholders - slightly faster than image",
            PlaceholderMode.CAPTIONS: "Caption placeholders - good performance with subtitle track",
            PlaceholderMode.IMAGE: "Generated image placeholders - moderate performance impact",
            PlaceholderMode.TITLE: "Text title placeholders - highest performance impact on timeline navigation",
        }

        echo(
            f"📋 Placeholder mode: {placeholder_mode.value} ({performance_messages[placeholder_mode]})"
        )

        if parsed_time_gap.amount > 0:
            gap_description = (
                f"{parsed_time_gap.amount} {parsed_time_gap.unit.value}(s)"
            )
            mode_description = (
                "same period grouping"
                if parsed_time_gap.same_period_mode
                else "minimum gap clustering"
            )
            echo(f"⏱️  Time gap clustering: {gap_description} ({mode_description})")

    except Exception as exc:
        handle_exception(verbose, exc)


@main.command()
@photo_dir_option
@soundtrack_option
@bpm_option
@add_options(timing_options)
@add_options(media_options)
@verbose_option
@click.option("--force", is_flag=True, help="Force sync even if conflicts detected")
@click.option("--recreate", is_flag=True, help="Force sync even if conflicts detected")
@click.option(
    "--dry-run", is_flag=True, help="Show what would be done without making changes"
)
def sync(
    photo_dir: Path,
    soundtrack: Path,
    bpm: Decimal,
    start_offset_beats: int,
    end_offset_beats: int,
    start_date: datetime | None,
    end_date: datetime | None,
    time_gap: str,
    extensions: str | None,
    gap_sec: Decimal,
    frame_rate: int,
    project_name: str,
    placeholders: str,
    id_method: str,
    verbose: bool,
    force: bool,
    dry_run: bool,
    recreate: bool,
) -> None:
    """Sync timeline directly with running DaVinci Resolve instance."""

    try:
        # Parse and validate inputs
        supported_extensions = parse_extensions(extensions)
        placeholder_mode = PlaceholderMode(placeholders)

        try:
            parsed_time_gap = TimeGap.parse(time_gap)
        except ValueError as e:
            error(f"Invalid time gap: {e}")

        # Create timeline project
        with Progress() as progress:
            task = progress.add_task("Creating timeline project...", total=None)

            project = create_timeline_project(
                photo_dir=photo_dir,
                soundtrack_path=soundtrack,
                bpm=bpm,
                project_name=project_name,
                frame_rate=frame_rate,
                gap_sec=gap_sec,
                start_offset_beats=start_offset_beats,
                end_offset_beats=end_offset_beats,
                start_date=start_date,
                end_date=end_date,
                placeholder_mode=placeholder_mode,
                time_gap=parsed_time_gap,
                id_method=id_method,
                supported_extensions=supported_extensions,
            )
            progress.update(task, completed=True)

        # Sync with DaVinci Resolve
        sync_engine = ResolveSync()
        sync_engine.sync_project(
            project, force=force, dry_run=dry_run, recreate=recreate
        )

        if dry_run:
            echo("🔍 Dry run completed - no changes made", style="blue")
        else:
            echo("✅ Timeline synced successfully", style="green")

    except Exception as exc:
        handle_exception(verbose, exc)


if __name__ == "__main__":
    main()
