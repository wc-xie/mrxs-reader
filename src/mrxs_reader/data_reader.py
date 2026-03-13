"""
Data reader module for MRXS slides.

This module reads JPEG tiles from Data*.dat files using exact byte offsets
and sizes from the index parser. It decodes JPEG tiles and extracts individual
fluorescence channels from the RGB-packed JPEG format.

Channel storage model:
  Each JPEG tile stores multiple fluorescence channels in its RGB channels.
  The storing_channel_number from Slidedat.ini maps to:
    0 -> R channel
    1 -> G channel  
    2 -> B channel
    
  FilterLevel_0 (3 channels): R=DAPI, G=SpGreen, B=SpOrange
  FilterLevel_1 (2 channels): R=CY5,  G=SpAqua
"""

import io
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import numpy as np
from PIL import Image

from .ini_parser import FilterChannel, MrxsMetadata
from .index_parser import IndexParser, TileEntry, HierRecord


class DataReader:
    """
    Reader for MRXS Data*.dat files.
    
    Uses index entries to read JPEG tiles at exact byte offsets from the correct
    data files. Extracts individual fluorescence channels from RGB-packed JPEGs.
    """
    
    def __init__(self, slide_path: Path, metadata: MrxsMetadata, index_parser: IndexParser):
        """
        Initialize data reader.
        
        Args:
            slide_path: Path to the slide directory (containing Data*.dat files)
            metadata: Parsed metadata from Slidedat.ini
            index_parser: Initialized index parser
        """
        self.slide_path = slide_path
        self.metadata = metadata
        self.index_parser = index_parser
        
        # Build channel lookup
        self._channel_by_name: Dict[str, FilterChannel] = {
            ch.name: ch for ch in metadata.filters
        }
        
        # Group channels by filter level
        self._channels_by_filter_level: Dict[str, List[FilterChannel]] = {}
        for ch in metadata.filters:
            fl = ch.data_filter_level
            if fl not in self._channels_by_filter_level:
                self._channels_by_filter_level[fl] = []
            self._channels_by_filter_level[fl].append(ch)
        
        # Sort channels within each group by storing_channel number
        for fl in self._channels_by_filter_level:
            self._channels_by_filter_level[fl].sort(key=lambda c: c.storing_channel)
        
        # Cache for open file handles
        self._file_handles: Dict[int, any] = {}

        # Cache for auto-detected channel remapping.
        # Key: (filter_level, storing_channel) -> actual JPEG component index.
        # Populated on first tile decode when the expected component has no data.
        self._channel_remap: Dict[Tuple[str, int], int] = {}
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        for handle in self._file_handles.values():
            handle.close()
        self._file_handles.clear()
    
    def _get_file_handle(self, fileno: int):
        """Get file handle for a data file by its number, opening if necessary."""
        if fileno not in self._file_handles:
            if fileno >= len(self.metadata.data_files):
                raise ValueError(f"Data file index {fileno} out of range (max {len(self.metadata.data_files)-1})")
            filename = self.metadata.data_files[fileno]
            file_path = self.slide_path / filename
            if not file_path.exists():
                raise FileNotFoundError(f"Data file not found: {file_path}")
            self._file_handles[fileno] = open(file_path, 'rb')
        return self._file_handles[fileno]
    
    def read_tile_jpeg(self, entry: TileEntry) -> bytes:
        """
        Read raw JPEG data for a tile entry.
        
        Args:
            entry: TileEntry with fileno, offset, and size
            
        Returns:
            Raw JPEG bytes
        """
        handle = self._get_file_handle(entry.fileno)
        handle.seek(entry.offset)
        return handle.read(entry.size)
    
    def decode_tile(self, entry: TileEntry) -> np.ndarray:
        """
        Read and decode a JPEG tile to an RGB numpy array.
        
        Args:
            entry: TileEntry specifying the tile location
            
        Returns:
            3D numpy array (height, width, 3) in RGB format
        """
        jpeg_data = self.read_tile_jpeg(entry)
        img = Image.open(io.BytesIO(jpeg_data))
        if img.mode != 'RGB':
            img = img.convert('RGB')
        return np.array(img)
    
    def extract_channel_from_tile(self, entry: TileEntry, channel: FilterChannel) -> np.ndarray:
        """
        Extract a specific fluorescence channel from a tile.

        Uses storing_channel_number to select the JPEG component, with
        auto-detection fallback for scanners that store data in a different
        component than declared (e.g. Pannoramic MIDI FilterLevel_1 tiles).

        Args:
            entry: TileEntry specifying the tile location
            channel: FilterChannel describing which RGB channel to extract

        Returns:
            2D numpy array (height, width) with channel intensity (uint8)
        """
        rgb = self.decode_tile(entry)
        remap_key = (channel.data_filter_level, channel.storing_channel)
        actual_ch = self._channel_remap.get(remap_key, channel.storing_channel)
        return rgb[:, :, actual_ch]
    
    def get_channel_by_name(self, name: str) -> Optional[FilterChannel]:
        """Get channel info by name."""
        return self._channel_by_name.get(name)

    def _detect_channel_remap(self, channel: FilterChannel) -> int:
        """
        Find the actual JPEG component carrying a channel's signal.

        The INI's STORING_CHANNEL_NUMBER is sometimes wrong (e.g. CY5 declares
        component 0/R but data is in component 2/B on Pannoramic MIDI scanners).

        Strategy: probe zoom levels from 0 (8192 tiles, entire slide covered)
        downward, sampling up to 50 tiles per level.  Stop as soon as any tile
        shows signal in the declared component (no remap needed) or in exactly
        one non-claimed alternative component (remap found).
        """
        # Components locked by sibling channels sharing the same filter level.
        siblings = self._channels_by_filter_level.get(channel.data_filter_level, [])
        claimed: set = set()
        for sib in siblings:
            if sib.name == channel.name:
                continue
            sib_key = (sib.data_filter_level, sib.storing_channel)
            claimed.add(self._channel_remap.get(sib_key, sib.storing_channel))

        for probe_zoom in range(self.metadata.zoom_levels):
            try:
                probe_rec = self.index_parser.get_record_for_filter_level(
                    channel.data_filter_level, probe_zoom
                )
            except Exception:
                continue
            if not probe_rec.entries:
                continue

            n = len(probe_rec.entries)
            step = max(1, n // 50)
            best_alt_ch = channel.storing_channel
            best_alt_max = 0

            for si in range(0, n, step):
                try:
                    tile = self.decode_tile(probe_rec.entries[si])
                except Exception:
                    continue

                declared_max = int(tile[:, :, channel.storing_channel].max())
                if declared_max > 0:
                    # Declared component has signal — no remap needed.
                    return channel.storing_channel

                # Declared component is empty for this tile; check alternatives.
                for alt_ch in range(tile.shape[2]):
                    if alt_ch == channel.storing_channel or alt_ch in claimed:
                        continue
                    alt_max = int(tile[:, :, alt_ch].max())
                    if alt_max > best_alt_max:
                        best_alt_max = alt_max
                        best_alt_ch = alt_ch

            if best_alt_max > 0 and best_alt_ch != channel.storing_channel:
                print(
                    f"  Channel remap: {channel.name} "
                    f"storing_channel={channel.storing_channel} "
                    f"-> actual JPEG component {best_alt_ch} "
                    f"(detected at zoom {probe_zoom}, {n} tiles)"
                )
                return best_alt_ch

        # No signal found anywhere; return the declared channel (may give blank output).
        print(f"  Warning: no signal found for {channel.name} in any zoom level")
        return channel.storing_channel

    def assemble_channel(self, channel_name: str, zoom_level: int = 0) -> Optional[np.ndarray]:
        """
        Assemble a complete slide image for one channel at a given zoom level.
        
        This reads all tile entries for the channel's filter level at the specified
        zoom, decodes each JPEG tile, extracts the correct RGB channel, and places
        tiles at their correct (x, y) positions to form the full image.
        
        Args:
            channel_name: Name of the fluorescence channel (e.g., "DAPI", "SpGreen")
            zoom_level: Zoom level (0 = full resolution, higher = smaller image)
            
        Returns:
            2D numpy array with the assembled channel image, or None on failure
        """
        channel = self.get_channel_by_name(channel_name)
        if channel is None:
            print(f"Error: Unknown channel '{channel_name}'")
            return None
        
        # Get the correct hier record based on filter level
        record = self.index_parser.get_record_for_filter_level(
            channel.data_filter_level, zoom_level
        )
        
        if not record.entries:
            print(f"No tiles found for {channel_name} at zoom {zoom_level}")
            return None
        
        images_x = self.metadata.tile_count_x
        
        # The image_index always uses full-resolution grid coordinates,
        # even at higher zoom levels. At zoom level N, each tile covers
        # 2^N original tiles, so we must divide coordinates by 2^N.
        zoom_factor = 2 ** zoom_level
        
        # Compute tile positions from image_index
        # image_index = y * images_x + x  (in full-res grid)
        # Actual tile position at this zoom = (x // zoom_factor, y // zoom_factor)
        tile_positions = []
        for entry in record.entries:
            orig_x = entry.image_index % images_x
            orig_y = entry.image_index // images_x
            tx = orig_x // zoom_factor
            ty = orig_y // zoom_factor
            tile_positions.append((tx, ty, entry))
        
        # Find the bounding box of actual tiles
        min_x = min(tx for tx, ty, _ in tile_positions)
        max_x = max(tx for tx, ty, _ in tile_positions)
        min_y = min(ty for tx, ty, _ in tile_positions)
        max_y = max(ty for tx, ty, _ in tile_positions)
        
        tiles_wide = max_x - min_x + 1
        tiles_tall = max_y - min_y + 1
        
        # Get tile dimensions from the first tile
        first_entry = record.entries[0]
        first_tile = self.decode_tile(first_entry)
        tile_h, tile_w = first_tile.shape[:2]

        # Ensure remap is detected before we start placing tiles.
        remap_key = (channel.data_filter_level, channel.storing_channel)
        if remap_key not in self._channel_remap:
            self._channel_remap[remap_key] = self._detect_channel_remap(channel)
        
        # Create output image
        img_width = tiles_wide * tile_w
        img_height = tiles_tall * tile_h
        
        print(f"Assembling {channel_name} at zoom {zoom_level}: "
              f"{len(record.entries)} tiles, grid {tiles_wide}×{tiles_tall}, "
              f"image {img_width}×{img_height}px")
        
        full_image = np.zeros((img_height, img_width), dtype=np.uint8)
        
        # Place each tile
        placed = 0
        for tx, ty, entry in tile_positions:
            try:
                tile_data = self.extract_channel_from_tile(entry, channel)
                
                # Calculate pixel position relative to bounding box
                px = (tx - min_x) * tile_w
                py = (ty - min_y) * tile_h
                
                # Handle tiles that might be slightly different sizes at edges
                th, tw = tile_data.shape
                if py + th > img_height:
                    th = img_height - py
                if px + tw > img_width:
                    tw = img_width - px
                
                full_image[py:py+th, px:px+tw] = tile_data[:th, :tw]
                placed += 1
                
                if placed % 2000 == 0:
                    print(f"  Placed {placed}/{len(record.entries)} tiles...")
                    
            except Exception as e:
                if placed < 5:
                    print(f"  Warning: Failed tile at ({tx},{ty}): {e}")
        
        print(f"  Placed {placed}/{len(record.entries)} tiles successfully")
        
        # Crop to remove empty borders
        row_sums = np.sum(full_image, axis=1)
        col_sums = np.sum(full_image, axis=0)
        non_zero_rows = np.where(row_sums > 0)[0]
        non_zero_cols = np.where(col_sums > 0)[0]
        
        if len(non_zero_rows) > 0 and len(non_zero_cols) > 0:
            y0, y1 = non_zero_rows[0], non_zero_rows[-1] + 1
            x0, x1 = non_zero_cols[0], non_zero_cols[-1] + 1
            full_image = full_image[y0:y1, x0:x1]
            print(f"  Cropped to {full_image.shape[1]}×{full_image.shape[0]}px")
        
        return full_image
