"""
MRXS Reader — Command Line Interface.

Provides ``info``, ``extract``, and ``composite`` sub-commands for working
with MRXS multi-channel fluorescence slides from the terminal.

Usage::

    python -m mrxs_reader info   MB-21.mrxs
    python -m mrxs_reader extract MB-21.mrxs output/ --level 5
    python -m mrxs_reader composite MB-21.mrxs composite.png --level 7
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

import numpy as np
from PIL import Image

from . import MrxsSlide


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------


def show_info(slide_path: Path) -> None:
    """Print detailed information about an MRXS slide."""
    with MrxsSlide(slide_path) as slide:
        info = slide.get_slide_info()

        print("=== MRXS Slide Information ===")
        print(f"Slide ID     : {info['slide_id']}")
        print(f"Slide Type   : {info['slide_type']}")
        print(f"Dimensions   : {info['dimensions'][0]} × {info['dimensions'][1]} px")
        print(f"Tile Size    : {info['tile_size']} px")
        print(f"Pyramid Lvls : {info['level_count']}")
        print(f"Pixel Size   : {info['pixel_size_um']:.4f} µm/px")
        print(f"Objective    : {info['objective_magnification']}×")

        print(f"\n=== Pyramid Levels ===")
        for i, (w, h) in enumerate(info["level_dimensions"]):
            downsample = 2**i
            pixel_size = slide.get_level_pixel_size(i)
            print(
                f"  Level {i}: {w:>7} × {h:<7}  "
                f"(↓{downsample}×, {pixel_size:.4f} µm/px)"
            )

        print(f"\n=== Fluorescence Channels ({len(info['channels'])}) ===")
        for ch in info["channels"]:
            print(
                f"  {ch['name']:12}  "
                f"Ex {str(ch['excitation_nm']):>12}  "
                f"Em {str(ch['emission_nm']):>12}  "
                f"RGB {ch['color_rgb']}  "
                f"Exp {ch['exposure_us']:>6} µs  "
                f"[{ch['filter_level']}  ch{ch['storing_channel']}]"
            )


def extract_channels(
    slide_path: Path,
    output_dir: Path,
    channels: Optional[List[str]] = None,
    level: int = 0,
    fmt: str = "tiff",
) -> None:
    """Extract individual channels from an MRXS slide."""
    output_dir.mkdir(parents=True, exist_ok=True)

    with MrxsSlide(slide_path) as slide:
        print(f"Slide  : {slide.slide_id}")
        print(f"Channels available: {slide.channel_names}")

        # Default to all channels
        if channels is None:
            channels = slide.channel_names

        # Validate
        invalid = [ch for ch in channels if ch not in slide.channel_names]
        if invalid:
            print(f"Warning: unknown channels ignored: {invalid}")
            channels = [ch for ch in channels if ch in slide.channel_names]

        if not channels:
            print("No valid channels to extract")
            return

        slide_name = slide_path.stem
        if slide_name.endswith(".mrxs"):
            slide_name = slide_name[:-5]

        for channel_name in channels:
            print(f"\nExtracting {channel_name} at level {level} ...")

            try:
                channel_data = slide.read_channel(channel_name, zoom_level=level)

                if channel_data is None:
                    print(f"  Skipped (no data)")
                    continue

                out_name = f"{slide_name}_{channel_name}_L{level}.{fmt}"
                out_path = output_dir / out_name

                if fmt.lower() == "tiff":
                    try:
                        import tifffile

                        tifffile.imwrite(
                            out_path,
                            channel_data,
                            metadata={
                                "channel": channel_name,
                                "level": level,
                                "slide_id": slide.slide_id,
                            },
                        )
                    except ImportError:
                        Image.fromarray(channel_data).save(out_path)
                else:
                    Image.fromarray(channel_data).save(out_path)

                print(f"  Saved: {out_path} ({channel_data.shape})")

            except Exception as e:
                print(f"  Error extracting {channel_name}: {e}")

        print(f"\nDone. Files in: {output_dir}")


def create_composite_cmd(
    slide_path: Path,
    output_path: Path,
    channels: Optional[List[str]] = None,
    level: int = 7,
    normalize: bool = True,
) -> None:
    """Create a multi-channel false-colour composite image."""
    with MrxsSlide(slide_path) as slide:
        print(f"Slide: {slide.slide_id}")

        if channels is None:
            channels = slide.channel_names

        print(f"Compositing channels: {channels} at level {level}")

        composite = slide.create_composite(
            channels=channels,
            zoom_level=level,
            normalize=normalize,
        )

        if composite is None:
            print("Failed to create composite (no data)")
            return

        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(composite).save(output_path)
        print(f"Saved: {output_path} ({composite.shape})")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="mrxs-reader",
        description=(
            "MRXS Reader — extract channels and create composites "
            "from 3DHISTECH MRXS fluorescence slides"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples
--------
  # Show slide information
  mrxs-reader info MB-21.mrxs

  # Extract all channels at level 5
  mrxs-reader extract MB-21.mrxs output/ --level 5

  # Extract specific channels
  mrxs-reader extract MB-21.mrxs output/ --channels DAPI SpGreen --level 7

  # Create composite image
  mrxs-reader composite MB-21.mrxs composite.png --channels DAPI SpGreen CY5

  # Same via python -m
  python -m mrxs_reader info MB-21.mrxs
""",
    )

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # -- info ----------------------------------------------------------------
    p_info = sub.add_parser("info", help="Show slide information")
    p_info.add_argument("slide", type=Path, help="Path to .mrxs file or slide dir")

    # -- extract -------------------------------------------------------------
    p_ext = sub.add_parser("extract", help="Extract individual channels")
    p_ext.add_argument("slide", type=Path, help="Path to .mrxs file or slide dir")
    p_ext.add_argument("output_dir", type=Path, help="Output directory")
    p_ext.add_argument(
        "--channels", nargs="+", help="Channel names (default: all)"
    )
    p_ext.add_argument(
        "--level",
        type=int,
        default=0,
        help="Pyramid level (0 = full res, default: 0)",
    )
    p_ext.add_argument(
        "--format",
        choices=["tiff", "png"],
        default="tiff",
        help="Output format (default: tiff)",
    )

    # -- composite -----------------------------------------------------------
    p_comp = sub.add_parser("composite", help="Create false-colour composite")
    p_comp.add_argument("slide", type=Path, help="Path to .mrxs file or slide dir")
    p_comp.add_argument("output", type=Path, help="Output image path")
    p_comp.add_argument(
        "--channels", nargs="+", help="Channel names (default: all)"
    )
    p_comp.add_argument(
        "--level",
        type=int,
        default=7,
        help="Pyramid level (default: 7)",
    )
    p_comp.add_argument(
        "--no-normalize",
        action="store_true",
        help="Disable intensity normalisation",
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    try:
        if args.command == "info":
            show_info(args.slide)

        elif args.command == "extract":
            extract_channels(
                slide_path=args.slide,
                output_dir=args.output_dir,
                channels=args.channels,
                level=args.level,
                fmt=args.format,
            )

        elif args.command == "composite":
            create_composite_cmd(
                slide_path=args.slide,
                output_path=args.output,
                channels=args.channels,
                level=args.level,
                normalize=not args.no_normalize,
            )

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
