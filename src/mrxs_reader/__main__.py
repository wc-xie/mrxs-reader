"""
MRXS Reader Command Line Interface.

This script provides command-line access to MRXS slide reading functionality,
including channel extraction, composite image generation, and slide information.
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional
import numpy as np
from PIL import Image
import tifffile

from . import MrxsSlide


def extract_channels(slide_path: Path, 
                    output_dir: Path, 
                    channels: Optional[List[str]] = None,
                    level: int = 0,
                    region: Optional[tuple] = None,
                    format: str = 'tiff') -> None:
    """
    Extract individual channels from an MRXS slide.
    
    Args:
        slide_path: Path to slide directory or .mrxs file
        output_dir: Directory to save channel images
        channels: List of channel names (None = all)
        level: Pyramid level to extract
        region: Optional (x, y, width, height) region
        format: Output format ('tiff', 'png')
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    with MrxsSlide(slide_path) as slide:
        print(f"Processing slide: {slide.slide_id}")
        print(f"Available channels: {slide.channel_names}")
        
        # Use all channels if none specified
        if channels is None:
            channels = slide.channel_names
        
        # Validate channels
        invalid_channels = [ch for ch in channels if ch not in slide.channel_names]
        if invalid_channels:
            print(f"Warning: Unknown channels ignored: {invalid_channels}")
            channels = [ch for ch in channels if ch in slide.channel_names]
        
        if not channels:
            print("No valid channels to extract")
            return
        
        # Determine region
        if region is None:
            full_width, full_height = slide.dimensions
            region = (0, 0, full_width, full_height)
        
        x, y, width, height = region
        
        # Extract each channel
        for channel_name in channels:
            print(f"Extracting {channel_name}...")
            
            try:
                # Read channel data
                channel_data = slide.read_region(x, y, width, height, level=level, channel=channel_name)
                
                # Generate output filename
                slide_name = slide_path.stem
                if slide_name.endswith('.mrxs'):
                    slide_name = slide_name[:-5]  # Remove .mrxs
                
                region_suffix = f"_L{level}"
                if region != (0, 0, full_width, full_height):
                    region_suffix += f"_x{x}_y{y}_w{width}_h{height}"
                
                output_filename = f"{slide_name}_{channel_name}{region_suffix}.{format}"
                output_path = output_dir / output_filename
                
                # Save image
                if format.lower() == 'tiff':
                    # Use tifffile for better metadata support
                    tifffile.imwrite(output_path, channel_data, 
                                   metadata={'channel': channel_name,
                                           'level': level,
                                           'slide_id': slide.slide_id})
                else:
                    # Use PIL for other formats
                    image = Image.fromarray(channel_data)
                    image.save(output_path)
                
                print(f"  Saved: {output_path} ({channel_data.shape})")
                
            except Exception as e:
                print(f"  Error extracting {channel_name}: {e}")
        
        print(f"Channel extraction complete. Files saved to: {output_dir}")


def create_composite(slide_path: Path,
                    output_path: Path,
                    channels: Optional[List[str]] = None,
                    level: int = 0,
                    region: Optional[tuple] = None,
                    normalize: bool = True) -> None:
    """
    Create a multi-channel composite image.
    
    Args:
        slide_path: Path to slide directory or .mrxs file
        output_path: Output image path
        channels: List of channel names (None = all)
        level: Pyramid level
        region: Optional (x, y, width, height) region
        normalize: Whether to normalize intensities
    """
    with MrxsSlide(slide_path) as slide:
        print(f"Creating composite for slide: {slide.slide_id}")
        
        # Use all channels if none specified
        if channels is None:
            channels = slide.channel_names
        
        print(f"Compositing channels: {channels}")
        
        # Create composite
        composite_data = slide.create_composite_image(
            channels=channels,
            level=level, 
            region=region,
            normalize=normalize
        )
        
        # Save composite
        composite_image = Image.fromarray(composite_data)
        composite_image.save(output_path)
        
        print(f"Composite saved: {output_path} ({composite_data.shape})")


def show_info(slide_path: Path) -> None:
    """Show detailed information about an MRXS slide."""
    with MrxsSlide(slide_path) as slide:
        info = slide.get_slide_info()
        
        print("=== MRXS Slide Information ===")
        print(f"Slide ID: {info['slide_id']}")
        print(f"Slide Type: {info['slide_type']}")
        print(f"Dimensions: {info['dimensions'][0]} × {info['dimensions'][1]} pixels")
        print(f"Pyramid Levels: {info['level_count']}")
        print(f"Pixel Size: {info['pixel_size_um']:.4f} µm/pixel")
        print(f"Objective: {info['objective_magnification']}×")
        
        print(f"\n=== Pyramid Levels ===")
        for i, (w, h) in enumerate(info['level_dimensions']):
            downsample = info['level_downsamples'][i]
            pixel_size = slide.get_level_pixel_size(i)
            print(f"Level {i}: {w} × {h} (downsample {downsample:.1f}×, {pixel_size:.4f} µm/px)")
        
        print(f"\n=== Fluorescence Channels ({len(info['channels'])}) ===")
        for ch in info['channels']:
            print(f"{ch['name']:15} | Ex: {ch['excitation']:12} | Em: {ch['emission']:12} | "
                  f"RGB: {ch['color_rgb']} | Exp: {ch['exposure_time_us']:6}µs")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="MRXS Reader - Extract channels and create composites from MRXS slides",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Show slide information
  python -m mrxs_reader info slide.mrxs
  
  # Extract all channels at full resolution
  python -m mrxs_reader extract slide.mrxs output/
  
  # Extract specific channels at level 7
  python -m mrxs_reader extract slide.mrxs output/ --channels DAPI SpGreen --level 7
  
  # Create a composite image
  python -m mrxs_reader composite slide.mrxs composite.png --channels DAPI SpGreen CY5
  
  # Extract a specific region
  python -m mrxs_reader extract slide.mrxs output/ --region 1000 1000 2000 2000
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # GUI command
    subparsers.add_parser('gui', help='Launch the graphical slide viewer')

    # Info command
    info_parser = subparsers.add_parser('info', help='Show slide information')
    info_parser.add_argument('slide', type=Path, help='Path to slide directory or .mrxs file')
    
    # Extract command
    extract_parser = subparsers.add_parser('extract', help='Extract individual channels')
    extract_parser.add_argument('slide', type=Path, help='Path to slide directory or .mrxs file')
    extract_parser.add_argument('output_dir', type=Path, help='Output directory for channel images')
    extract_parser.add_argument('--channels', nargs='+', help='Channel names to extract (default: all)')
    extract_parser.add_argument('--level', type=int, default=0, help='Pyramid level (default: 0 = full resolution)')
    extract_parser.add_argument('--region', nargs=4, type=int, metavar=('X', 'Y', 'W', 'H'),
                               help='Extract specific region (x y width height)')
    extract_parser.add_argument('--format', choices=['tiff', 'png'], default='tiff',
                               help='Output image format (default: tiff)')
    
    # Composite command  
    composite_parser = subparsers.add_parser('composite', help='Create multi-channel composite')
    composite_parser.add_argument('slide', type=Path, help='Path to slide directory or .mrxs file')
    composite_parser.add_argument('output', type=Path, help='Output composite image path')
    composite_parser.add_argument('--channels', nargs='+', help='Channel names to include (default: all)')
    composite_parser.add_argument('--level', type=int, default=7, help='Pyramid level (default: 7 for thumbnails)')
    composite_parser.add_argument('--region', nargs=4, type=int, metavar=('X', 'Y', 'W', 'H'),
                                 help='Composite specific region (x y width height)')
    composite_parser.add_argument('--no-normalize', action='store_true',
                                 help='Disable intensity normalization')
    
    args = parser.parse_args()
    
    if args.command is None:
        parser.print_help()
        return

    if args.command == 'gui':
        from .gui import main as _gui_main
        _gui_main()
        return

    try:
        if args.command == 'info':
            show_info(args.slide)
            
        elif args.command == 'extract':
            region = tuple(args.region) if args.region else None
            extract_channels(
                slide_path=args.slide,
                output_dir=args.output_dir,
                channels=args.channels,
                level=args.level,
                region=region,
                format=args.format
            )
            
        elif args.command == 'composite':
            region = tuple(args.region) if args.region else None
            create_composite(
                slide_path=args.slide,
                output_path=args.output,
                channels=args.channels,
                level=args.level,
                region=region,
                normalize=not args.no_normalize
            )
    
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())