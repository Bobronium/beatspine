from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from beatspine.definitions import MediaAsset
from beatspine.definitions import MediaType
from beatspine.definitions import TimelineElement
from beatspine.definitions import TimelineProject
from beatspine.console import echo


class FCPXMLExporter:
    """Final Cut Pro X timeline exporter (FCPXML format)."""

    def __init__(self, placeholder_output_dir: Path | None = None) -> None:
        self.placeholder_output_dir = placeholder_output_dir or Path.cwd()

    def export(self, project: TimelineProject, output_path: Path) -> None:
        """Export timeline to Final Cut Pro X format."""
        # Ensure .fcpxml extension
        if output_path.suffix != ".fcpxml":
            output_path = output_path.with_suffix(".fcpxml")

        # Create FCPXML structure
        fcpxml = self._create_fcpxml(project)
        self._write_xml(fcpxml, output_path)

    def _create_fcpxml(self, project: TimelineProject) -> ET.Element:
        """Create FCPXML structure for Final Cut Pro."""
        fcpxml = ET.Element("fcpxml", attrib={"version": "1.10"})

        # Add resources
        resources = ET.SubElement(fcpxml, "resources")

        # Add format resource
        format_elem = ET.SubElement(
            resources, "format",
            attrib={
                "id": "r1",
                "name": "FFVideoFormat1080p60",
                "frameDuration": f"100/{project.frame_rate}00s",
                "width": str(project.dimensions.width),
                "height": str(project.dimensions.height),
                "colorSpace": "1-1-1 (Rec. 709)"
            }
        )

        # Add media assets
        for element in project.elements:
            if element.asset:
                self._add_asset_to_resources(resources, element.asset)

        # Add events and project
        event = ET.SubElement(fcpxml, "event", attrib={"name": "Generated Timeline"})
        project_elem = ET.SubElement(event, "project", attrib={"name": project.name})

        sequence = ET.SubElement(
            project_elem, "sequence",
            attrib={
                "format": "r1",
                "duration": f"{int(project.duration / 1000 * project.frame_rate)}s",
                "tcStart": "0s"
            }
        )

        spine = ET.SubElement(sequence, "spine")

        # Add clips to spine
        for element in project.elements:
            if element.media_type == MediaType.VIDEO:
                self._add_video_clip_to_spine(spine, element, project)
            elif element.media_type == MediaType.AUDIO:
                self._add_audio_clip_to_spine(spine, element, project)

        return fcpxml

    def _add_asset_to_resources(self, resources: ET.Element, asset: MediaAsset) -> None:
        """Add media asset to resources section."""
        if asset.media_type == MediaType.VIDEO:
            asset_elem = ET.SubElement(
                resources, "asset",
                attrib={
                    "id": asset.uid,
                    "name": asset.name,
                    "src": str(asset.path.resolve())
                }
            )
            if asset.dimensions:
                ET.SubElement(
                    asset_elem, "media-rep",
                    attrib={
                        "kind": "original-media",
                        "src": str(asset.path.resolve())
                    }
                )
        elif asset.media_type == MediaType.AUDIO:
            asset_elem = ET.SubElement(
                resources, "asset",
                attrib={
                    "id": asset.uid,
                    "name": asset.name,
                    "src": str(asset.path.resolve()),
                    "duration": f"{int(asset.duration / 1000 * 60)}s"
                }
            )

    def _add_video_clip_to_spine(
        self, spine: ET.Element, element: TimelineElement, project: TimelineProject
    ) -> None:
        """Add video clip to spine."""
        if not element.asset:
            return

        clip = ET.SubElement(
            spine, "clip",
            attrib={
                "name": element.name,
                "ref": element.asset.uid,
                "offset": f"{int(element.time_range.start / 1000 * project.frame_rate)}s",
                "duration": f"{int(element.time_range.duration / 1000 * project.frame_rate)}s"
            }
        )

    def _add_audio_clip_to_spine(
        self, spine: ET.Element, element: TimelineElement, project: TimelineProject
    ) -> None:
        """Add audio clip to spine."""
        if not element.asset:
            return

        clip = ET.SubElement(
            spine, "audio",
            attrib={
                "name": element.name,
                "ref": element.asset.uid,
                "offset": f"{int(element.time_range.start / 1000 * project.frame_rate)}s",
                "duration": f"{int(element.time_range.duration / 1000 * project.frame_rate)}s"
            }
        )

    def _write_xml(self, root: ET.Element, output_path: Path) -> None:
        """Write XML tree to file."""
        tree = ET.ElementTree(root)
        tree.write(output_path, encoding="utf-8", xml_declaration=True)
        echo(f"Final Cut Pro project saved to: {output_path}")
