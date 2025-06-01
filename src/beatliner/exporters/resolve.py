from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from beatliner.definitions import MediaType
from beatliner.definitions import TimelineElement
from beatliner.definitions import TimelineProject
from beatliner.console import echo


class ResolveExporter:
    """DaVinci Resolve timeline exporter (XML format)."""

    def __init__(self, placeholder_output_dir: Path | None = None) -> None:
        self.placeholder_output_dir = placeholder_output_dir or Path.cwd()

    def export(self, project: TimelineProject, output_path: Path) -> None:
        """Export timeline to DaVinci Resolve XML format."""
        # Ensure .xml extension
        if output_path.suffix != ".xml":
            output_path = output_path.with_suffix(".xml")

        # Create XML structure
        xmeml = self._create_xmeml(project)
        self._write_xml(xmeml, output_path)

    def _create_xmeml(self, project: TimelineProject) -> ET.Element:
        """Create XMEML structure for Resolve."""
        xmeml = ET.Element("xmeml", attrib={"version": "5"})

        sequence = ET.SubElement(xmeml, "sequence")
        ET.SubElement(sequence, "name").text = project.name
        ET.SubElement(sequence, "duration").text = str(
            int(project.duration / 1000 * project.frame_rate)
        )

        rate = ET.SubElement(sequence, "rate")
        ET.SubElement(rate, "timebase").text = str(project.frame_rate)
        ET.SubElement(rate, "ntsc").text = "FALSE"

        # Create media section
        media_elem = ET.SubElement(sequence, "media")

        # Video tracks
        video = ET.SubElement(media_elem, "video")
        self._add_video_format(video, project)

        # Add video tracks
        video_tracks = self._get_video_tracks(project)
        for elements in video_tracks.values():
            track = ET.SubElement(video, "track")
            for element in elements:
                self._add_clip_to_track(track, element, project)

        # Audio tracks
        audio = ET.SubElement(media_elem, "audio")
        audio_tracks = self._get_audio_tracks(project)
        for elements in audio_tracks.values():
            track = ET.SubElement(audio, "track")
            for element in elements:
                self._add_audio_clip_to_track(track, element, project)

        # Add markers
        self._add_markers(sequence, project)

        return xmeml

    def _add_video_format(self, video: ET.Element, project: TimelineProject) -> None:
        """Add video format information."""
        format_elem = ET.SubElement(video, "format")
        samplecharacteristics = ET.SubElement(format_elem, "samplecharacteristics")
        ET.SubElement(samplecharacteristics, "width").text = str(
            project.dimensions.width
        )
        ET.SubElement(samplecharacteristics, "height").text = str(
            project.dimensions.height
        )
        ET.SubElement(samplecharacteristics, "pixelaspectratio").text = "square"
        ET.SubElement(samplecharacteristics, "fielddominance").text = "none"

        rate = ET.SubElement(samplecharacteristics, "rate")
        ET.SubElement(rate, "timebase").text = str(project.frame_rate)
        ET.SubElement(rate, "ntsc").text = "FALSE"

    def _get_video_tracks(
        self, project: TimelineProject
    ) -> dict[int, list[TimelineElement]]:
        """Organize video elements by track."""
        tracks: dict[int, list[TimelineElement]] = {}
        for element in project.elements:
            if element.media_type == MediaType.VIDEO and element.track > 0:
                if element.track not in tracks:
                    tracks[element.track] = []
                tracks[element.track].append(element)

        # Sort elements by start time within each track
        for track_elements in tracks.values():
            track_elements.sort(key=lambda e: e.time_range.start)

        return tracks

    def _get_audio_tracks(
        self, project: TimelineProject
    ) -> dict[int, list[TimelineElement]]:
        """Organize audio elements by track."""
        tracks: dict[int, list[TimelineElement]] = {}
        for element in project.elements:
            if element.media_type == MediaType.AUDIO:
                # Convert negative track numbers to positive for Resolve
                track_num = abs(element.track)
                if track_num not in tracks:
                    tracks[track_num] = []
                tracks[track_num].append(element)

        return tracks

    def _add_clip_to_track(
        self, track: ET.Element, element: TimelineElement, project: TimelineProject
    ) -> None:
        """Add video clip to track."""
        clipitem = ET.SubElement(track, "clipitem")
        ET.SubElement(clipitem, "name").text = element.name
        ET.SubElement(clipitem, "duration").text = str(
            int(element.time_range.duration / 1000 * project.frame_rate)
        )

        rate = ET.SubElement(clipitem, "rate")
        ET.SubElement(rate, "timebase").text = str(project.frame_rate)
        ET.SubElement(rate, "ntsc").text = "FALSE"

        ET.SubElement(clipitem, "in").text = "0"
        ET.SubElement(clipitem, "out").text = str(
            int(element.time_range.duration / 1000 * project.frame_rate)
        )
        ET.SubElement(clipitem, "start").text = str(
            int(element.time_range.start / 1000 * project.frame_rate)
        )
        ET.SubElement(clipitem, "end").text = str(
            int(element.time_range.end / 1000 * project.frame_rate)
        )

        if element.asset:
            file_elem = ET.SubElement(clipitem, "file")
            ET.SubElement(file_elem, "name").text = element.asset.name
            ET.SubElement(
                file_elem, "pathurl"
            ).text = f"file://localhost{element.asset.path.resolve()}"

    def _add_audio_clip_to_track(
        self, track: ET.Element, element: TimelineElement, project: TimelineProject
    ) -> None:
        """Add audio clip to track."""
        clipitem = ET.SubElement(track, "clipitem")
        ET.SubElement(clipitem, "name").text = element.name
        ET.SubElement(clipitem, "duration").text = str(
            int(element.time_range.duration / 1000 * project.frame_rate)
        )

        rate = ET.SubElement(clipitem, "rate")
        ET.SubElement(rate, "timebase").text = str(project.frame_rate)
        ET.SubElement(rate, "ntsc").text = "FALSE"

        ET.SubElement(clipitem, "in").text = "0"
        ET.SubElement(clipitem, "out").text = str(
            int(element.time_range.duration / 1000 * project.frame_rate)
        )
        ET.SubElement(clipitem, "start").text = str(
            int(element.time_range.start / 1000 * project.frame_rate)
        )
        ET.SubElement(clipitem, "end").text = str(
            int(element.time_range.end / 1000 * project.frame_rate)
        )

        if element.asset:
            file_elem = ET.SubElement(clipitem, "file")
            ET.SubElement(file_elem, "name").text = element.asset.name
            ET.SubElement(
                file_elem, "pathurl"
            ).text = f"file://localhost{element.asset.path.resolve()}"

    def _add_markers(self, sequence: ET.Element, project: TimelineProject) -> None:
        """Add markers to sequence."""
        for marker in project.markers:
            marker_elem = ET.SubElement(sequence, "marker")
            ET.SubElement(marker_elem, "name").text = marker.name
            ET.SubElement(marker_elem, "comment").text = marker.name
            ET.SubElement(marker_elem, "in").text = str(
                int(marker.position / 1000 * project.frame_rate)
            )
            ET.SubElement(marker_elem, "out").text = str(
                int((marker.position + marker.duration) / 1000 * project.frame_rate)
            )

    def _write_xml(self, root: ET.Element, output_path: Path) -> None:
        """Write XML tree to file."""
        tree = ET.ElementTree(root)
        tree.write(output_path, encoding="utf-8", xml_declaration=True)
        echo(f"DaVinci Resolve project saved to: {output_path}")
