"""
Data reader module for MRXS slides.

This module reads JPEG tiles from ``Data*.dat`` files using exact byte offsets
and sizes obtained from the :class:`IndexParser`.  It decodes JPEG tiles and
extracts individual fluorescence channels from the RGB-packed JPEG format.

Channel storage model
---------------------

Each JPEG tile stores multiple fluorescence channels in its RGB planes.
The ``storing_channel_number`` from ``Slidedat.ini`` maps to::

    0 → Red channel
    1 → Green channel
    2 → Blue channel

For a typical 5-channel fluorescence slide the packing is::

    FilterLevel_0 JPEG: R = DAPI, G = SpGreen, B = SpOrange
    FilterLevel_1 JPEG: R = CY5,  G = SpAqua
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image

from .index_parser import HierRecord, IndexParser, TileEntry
from .ini_parser import FilterChannel, MrxsMetadata


class DataReader:
    """
    Reader for MRXS ``Data*.dat`` files.

    Uses :class:`TileEntry` objects to seek and read JPEG tiles at exact byte
    offsets.  Extracts individual fluorescence channels from RGB-packed JPEGs.

    Parameters
    ----------
    slide_path : Path
        Path to the slide directory (containing ``Data*.dat`` files).
    metadata : MrxsMetadata
        Parsed metadata from ``Slidedat.ini``.
    index_parser : IndexParser
        An initialised (entered) index parser instance.
    """

    def __init__(
        self,
        slide_path: Path,
        metadata: MrxsMetadata,
        index_parser: IndexParser,
    ):
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
        self._file_handles: Dict[int, Any] = {}

    # -- context manager -----------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        for handle in self._file_handles.values():
            handle.close()
        self._file_handles.clear()

    # -- internal ------------------------------------------------------------

    def _get_file_handle(self, fileno: int):
        """Open (or return cached) handle for ``Data<fileno>.dat``."""
        if fileno not in self._file_handles:
            if fileno >= len(self.metadata.data_files):
                raise ValueError(
                    f"Data file index {fileno} out of range "
                    f"(max {len(self.metadata.data_files) - 1})"
                )
            filename = self.metadata.data_files[fileno]
            file_path = self.slide_path / filename
            if not file_path.exists():
                raise FileNotFoundError(f"Data file not found: {file_path}")
            self._file_handles[fileno] = open(file_path, "rb")
        return self._file_handles[fileno]

    # -- public API ----------------------------------------------------------

    def read_tile_jpeg(self, entry: TileEntry) -> bytes:
        """
        Read raw JPEG data for a tile entry.

        Parameters
        ----------
        entry : TileEntry
            Tile entry with ``fileno``, ``offset``, and ``size``.

        Returns
        -------
        bytes
            Raw JPEG bytes.
        """
        handle = self._get_file_handle(entry.fileno)
        handle.seek(entry.offset)
        return handle.read(entry.size)

    def decode_tile(self, entry: TileEntry) -> np.ndarray:
        """
        Read and decode a JPEG tile to an RGB numpy array.

        Returns
        -------
        numpy.ndarray
            Shape ``(height, width, 3)`` in RGB format, dtype ``uint8``.
        """
        jpeg_data = self.read_tile_jpeg(entry)
        img = Image.open(io.BytesIO(jpeg_data))
        if img.mode != "RGB":
            img = img.convert("RGB")
        return np.array(img)

    def extract_channel_from_tile(
        self, entry: TileEntry, channel: FilterChannel
    ) -> np.ndarray:
        """
        Extract a specific fluorescence channel from a tile.

        The ``storing_channel`` number maps to JPEG RGB channels:
        ``0 → R``, ``1 → G``, ``2 → B``.

        Returns
        -------
        numpy.ndarray
            Shape ``(height, width)``, dtype ``uint8``.
        """
        rgb = self.decode_tile(entry)
        return rgb[:, :, channel.storing_channel]

    def get_channel_by_name(self, name: str) -> Optional[FilterChannel]:
        """Get channel info by name."""
        return self._channel_by_name.get(name)

    def assemble_channel(
        self, channel_name: str, zoom_level: int = 0
    ) -> Optional[np.ndarray]:
        """
        Assemble a complete slide image for one channel at a given zoom level.

        Reads all tile entries for the channel's filter level at *zoom_level*,
        decodes each JPEG tile, extracts the correct RGB plane, and places
        tiles at their correct ``(x, y)`` positions.

        Parameters
        ----------
        channel_name : str
            Fluorescence channel name (e.g. ``"DAPI"``, ``"SpGreen"``).
        zoom_level : int
            0 = full resolution, higher = smaller image.

        Returns
        -------
        numpy.ndarray or None
            2-D ``uint8`` array with the assembled channel image.
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
        # even at higher zoom levels.  At zoom level N each tile covers
        # 2^N original tiles, so we must divide coordinates by 2^N.
        zoom_factor = 2**zoom_level

        tile_positions = []
        for entry in record.entries:
            orig_x = entry.image_index % images_x
            orig_y = entry.image_index // images_x
            tx = orig_x // zoom_factor
            ty = orig_y // zoom_factor
            tile_positions.append((tx, ty, entry))

        # Bounding box of actual tiles
        min_x = min(tx for tx, ty, _ in tile_positions)
        max_x = max(tx for tx, ty, _ in tile_positions)
        min_y = min(ty for tx, ty, _ in tile_positions)
        max_y = max(ty for tx, ty, _ in tile_positions)

        tiles_wide = max_x - min_x + 1
        tiles_tall = max_y - min_y + 1

        # Get tile dimensions from the first tile
        first_tile = self.decode_tile(record.entries[0])
        tile_h, tile_w = first_tile.shape[:2]

        img_width = tiles_wide * tile_w
        img_height = tiles_tall * tile_h

        print(
            f"Assembling {channel_name} at zoom {zoom_level}: "
            f"{len(record.entries)} tiles, grid {tiles_wide}×{tiles_tall}, "
            f"image {img_width}×{img_height}px"
        )

        full_image = np.zeros((img_height, img_width), dtype=np.uint8)

        placed = 0
        for tx, ty, entry in tile_positions:
            try:
                tile_data = self.extract_channel_from_tile(entry, channel)
                px = (tx - min_x) * tile_w
                py = (ty - min_y) * tile_h

                th, tw = tile_data.shape
                if py + th > img_height:
                    th = img_height - py
                if px + tw > img_width:
                    tw = img_width - px

                full_image[py : py + th, px : px + tw] = tile_data[:th, :tw]
                placed += 1

                if placed % 2000 == 0:
                    print(f"  Placed {placed}/{len(record.entries)} tiles...")

            except Exception as e:
                if placed < 5:
                    print(f"  Warning: Failed tile at ({tx},{ty}): {e}")

        print(f"  Placed {placed}/{len(record.entries)} tiles successfully")

        # Crop empty borders
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
