"""Optional raster export.

The analyser itself stays dependency-free; PNGs are produced by whatever
converter the host happens to have (`rsvg-convert` from librsvg, or `resvg`).
When neither exists the SVG remains the deliverable and this returns None.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional


def available_converter() -> Optional[str]:
    for tool in ("rsvg-convert", "resvg"):
        if shutil.which(tool):
            return tool
    return None


def render_png(svg_path: Path, png_path: Optional[Path] = None, width: int = 1600) -> Optional[Path]:
    svg_path = Path(svg_path)
    png_path = Path(png_path or svg_path.with_suffix(".png"))
    tool = available_converter()
    if tool == "rsvg-convert":
        command = [
            "rsvg-convert", "-w", str(width), "-b", "white",
            "-o", str(png_path), str(svg_path),
        ]
    elif tool == "resvg":
        command = [
            "resvg", "--width", str(width), "--background", "white",
            str(svg_path), str(png_path),
        ]
    else:
        return None
    subprocess.run(command, check=True, capture_output=True, timeout=300)
    return png_path
