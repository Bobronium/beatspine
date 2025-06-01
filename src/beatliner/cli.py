from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from beatliner.core import create_timeline_project
from beatliner.console import echo

from beatliner.console import error
from beatliner.constants import DEFAULT_END_OFFSET_BEATS
from beatliner.constants import DEFAULT_FPS
from beatliner.constants import DEFAULT_GAP_SEC
from beatliner.constants import DEFAULT_OUTPUT
from beatliner.constants import DEFAULT_PLACEHOLDERS
from beatliner.constants import DEFAULT_START_OFFSET_BEATS
from beatliner.constants import DEFAULT_TIME_GAP
from beatliner.constants import SUPPORTED_AUDIO_EXTENSIONS
from beatliner.constants import SUPPORTED_IMAGE_EXTENSIONS
from beatliner.definitions import PlaceholderMode
from beatliner.definitions import TimeGap
from beatliner.exporters.fcpx import FCPXMLExporter
from beatliner.exporters.resolve import ResolveExporter


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate timeline projects for multiple NLEs with beat-synchronized photos"
    )

    parser.add_argument(
        "--dir", required=True, type=Path, help="Directory containing photos"
    )
    parser.add_argument(
        "--soundtrack",
        required=True,
        type=Path,
        help="Audio file (.m4a, .mp3, .wav, .aac) for timeline duration and sync",
    )
    parser.add_argument(
        "--bpm",
        type=Decimal,
        required=True,
        help="Beats per minute for timeline synchronization",
    )
    parser.add_argument(
        "--nle",
        type=str,
        choices=["fcpx", "resolve", "both"],
        default="fcpx",
        help="Target NLE: fcpx (Final Cut Pro), resolve (DaVinci Resolve), or both",
    )

    parser.add_argument(
        "--start-offset-beats",
        type=int,
        default=DEFAULT_START_OFFSET_BEATS,
        help=f"Number of beats to skip at start (default: {DEFAULT_START_OFFSET_BEATS})",
    )
    parser.add_argument(
        "--end-offset-beats",
        type=int,
        default=DEFAULT_END_OFFSET_BEATS,
        help=f"Number of beats to skip at end (default: {DEFAULT_END_OFFSET_BEATS})",
    )

    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Start date in ISO format (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end-date", type=str, default=None, help="End date in ISO format (YYYY-MM-DD)"
    )

    parser.add_argument(
        "--time-gap",
        type=str,
        default=DEFAULT_TIME_GAP,
        help=(
            "Time gap for photo clustering. Format: 'amount-unit' or 'amount-unit-same'. "
            "Units: second, minute, hour, day, week, month, year. "
            "Examples: '1-day' (minimum 1 day gap), '1-year-same' (group by same year). "
            f"Default: {DEFAULT_TIME_GAP}"
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path(DEFAULT_OUTPUT),
        help=f"Output file path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--extensions",
        type=str,
        default=None,
        help=f"Comma-separated image extensions (default: {','.join(SUPPORTED_IMAGE_EXTENSIONS)})",
    )

    parser.add_argument(
        "--gap-sec",
        type=Decimal,
        default=DEFAULT_GAP_SEC,
        help=f"Gap duration in seconds (default: {DEFAULT_GAP_SEC})",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=DEFAULT_FPS,
        help=f"Frames per second (default: {DEFAULT_FPS})",
    )

    parser.add_argument(
        "--name", type=str, default="PhotoTimeline", help="Project name"
    )

    parser.add_argument(
        "--placeholders",
        type=str,
        choices=["image", "title", "captions", "missing", "none"],
        default=DEFAULT_PLACEHOLDERS,
        help=(
            "Placeholder mode for empty beats: "
            "'image' generates PIL placeholder images, "
            "'title' uses text overlays, "
            "'captions' creates subtitle-style captions, "
            "'missing' creates missing media indicators (fastest), "
            f"'none' skips placeholders entirely (default: {DEFAULT_PLACEHOLDERS})"
        ),
    )

    parser.add_argument(
        "--id-method",
        type=str,
        choices=["inode", "content", "path"],
        default="inode",
        help="Method for generating deterministic UIDs",
    )

    return parser.parse_args()


def parse_date_arg(date_str: str | None) -> datetime | None:
    """Parse a date string or return None."""
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str)
    except ValueError:
        error(f"Invalid date format: {date_str}. Use ISO format (YYYY-MM-DD).")


def main() -> None:
    """Main entry point."""
    args = parse_args()

    if not args.soundtrack.exists():
        error(f"Soundtrack file not found: {args.soundtrack}")

    if args.soundtrack.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
        error(f"Unsupported audio format: {args.soundtrack.suffix}")

    if not args.dir.is_dir():
        error(f"Photo directory not found: {args.dir}")

    start_date = parse_date_arg(args.start_date)
    end_date = parse_date_arg(args.end_date)

    supported_extensions = SUPPORTED_IMAGE_EXTENSIONS
    if args.extensions:
        extensions = [ext.strip().lower() for ext in args.extensions.split(",")]
        supported_extensions = frozenset(
            ext if ext.startswith(".") else f".{ext}" for ext in extensions
        )

    # Convert placeholder mode
    placeholder_mode = PlaceholderMode(args.placeholders)

    # Parse time gap
    try:
        time_gap = TimeGap.parse(args.time_gap)
    except ValueError as e:
        error(f"Invalid time gap: {e}")

    # Create timeline project
    project = create_timeline_project(
        photo_dir=args.dir,
        soundtrack_path=args.soundtrack,
        bpm=args.bpm,
        project_name=args.name,
        frame_rate=args.frames,
        gap_sec=args.gap_sec,
        start_offset_beats=args.start_offset_beats,
        end_offset_beats=args.end_offset_beats,
        start_date=start_date,
        end_date=end_date,
        placeholder_mode=placeholder_mode,
        time_gap=time_gap,
        id_method=args.id_method,
        supported_extensions=supported_extensions,
    )

    # Export to selected NLE(s)
    output_dir = args.output.parent if args.output.parent != Path() else Path.cwd()

    if args.nle in {"fcpx", "both"}:
        fcpx_output = (
            args.output.with_suffix(".fcpxml")
            if args.nle == "fcpx"
            else output_dir / f"{args.name}.fcpxml"
        )
        exporter = FCPXMLExporter(output_dir)
        exporter.export(project, fcpx_output)

    if args.nle in {"resolve", "both"}:
        resolve_output = (
            args.output.with_suffix(".xml")
            if args.nle == "resolve"
            else output_dir / f"{args.name}_resolve.xml"
        )
        exporter = ResolveExporter(output_dir)
        exporter.export(project, resolve_output)

    # Performance optimization messaging
    performance_msg = {
        PlaceholderMode.NONE: "No placeholders - optimal performance",
        PlaceholderMode.MISSING: "Missing media placeholders - slightly faster than image",
        PlaceholderMode.CAPTIONS: "Caption placeholders - good performance with subtitle track",
        PlaceholderMode.IMAGE: "Generated image placeholders - moderate performance impact",
        PlaceholderMode.TITLE: "Text title placeholders - highest performance impact on timeline navigation",
    }

    echo(
        f"Created timeline with {len(project.photo_placements)} photos and audio track"
    )
    echo(
        f"Placeholder mode: {placeholder_mode.value} ({performance_msg[placeholder_mode]})"
    )

    if time_gap.amount > 0:
        gap_description = f"{time_gap.amount} {time_gap.unit.value}(s)"
        mode_description = (
            "same period grouping"
            if time_gap.same_period_mode
            else "minimum gap clustering"
        )
        echo(f"Time gap clustering: {gap_description} ({mode_description})")

    if placeholder_mode == PlaceholderMode.IMAGE:
        echo(f"Generated placeholder images in: {output_dir / 'placeholders'}")
    elif placeholder_mode == PlaceholderMode.MISSING:
        echo("Missing media indicators will appear in the NLE for empty beats")
    elif placeholder_mode == PlaceholderMode.CAPTIONS:
        echo("Caption track will display beat numbers and date ranges for empty beats")
