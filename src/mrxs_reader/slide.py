"""
Main MRXS Slide API.

This module provides the high-level :class:`MrxsSlide` class — the primary
interface for reading MRXS multi-channel fluorescence slides produced by
3DHISTECH scanners.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from .data_reader import DataReader
from .index_parser import IndexParser
from .ini_parser import FilterChannel, MrxsMetadata, parse_slidedat_ini


class MrxsSlide:
    """
    High-level interface for reading MRXS multi-channel fluorescence slides.

    Use as a context manager::

        with MrxsSlide("filename") as slide:
            print(f"Channels: {slide.channel_names}")
            dapi = slide.read_channel("DAPI", zoom_level=5)
            thumb = slide.get_thumbnail("DAPI")

    Parameters
    ----------
    slide_path : str or Path
        Path to the ``.mrxs`` file **or** to the companion directory that
        contains ``Slidedat.ini`` and the data files.
    """

    def __init__(self, slide_path):
        slide_path = Path(slide_path)

        # Accept both .mrxs files and directories
        if slide_path.suffix == ".mrxs":
            self.slide_dir = slide_path.with_suffix("")
        else:
            self.slide_dir = slide_path

        if not self.slide_dir.exists():
            raise FileNotFoundError(f"Slide directory not found: {self.slide_dir}")

        ini_path = self.slide_dir / "Slidedat.ini"
        if not ini_path.exists():
            raise FileNotFoundError(f"Slidedat.ini not found in {self.slide_dir}")

        self.metadata: MrxsMetadata = parse_slidedat_ini(ini_path)

        # Initialised in __enter__
        self._index_parser: Optional[IndexParser] = None
        self._data_reader: Optional[DataReader] = None

    # -- context manager -----------------------------------------------------

    def __enter__(self):
        self._index_parser = IndexParser(
            self.slide_dir / "Index.dat",
            self.metadata.slide_id,
            self.metadata.zoom_levels,
        )
        self._index_parser.__enter__()

        self._data_reader = DataReader(
            self.slide_dir, self.metadata, self._index_parser
        )
        self._data_reader.__enter__()

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._data_reader:
            self._data_reader.__exit__(exc_type, exc_val, exc_tb)
        if self._index_parser:
            self._index_parser.__exit__(exc_type, exc_val, exc_tb)

    # -- properties ----------------------------------------------------------

    @property
    def slide_id(self) -> str:
        """Unique slide identifier (UUID from ``Slidedat.ini``)."""
        return self.metadata.slide_id

    @property
    def channels(self) -> List[FilterChannel]:
        """List of all fluorescence channels."""
        return self.metadata.filters

    @property
    def channel_names(self) -> List[str]:
        """List of channel names."""
        return [c.name for c in self.metadata.filters]

    @property
    def level_count(self) -> int:
        """Number of pyramid zoom levels."""
        return self.metadata.zoom_levels

    @property
    def tile_size(self) -> int:
        """Standard tile size in pixels."""
        if self.metadata.zoom_pyramid:
            return self.metadata.zoom_pyramid[0].digitizer_width
        return 256

    @property
    def dimensions(self) -> Tuple[int, int]:
        """Full-resolution slide dimensions ``(width, height)`` in pixels."""
        ts = self.tile_size
        return (self.metadata.tile_count_x * ts, self.metadata.tile_count_y * ts)

    @property
    def level_dimensions(self) -> List[Tuple[int, int]]:
        """Dimensions at each pyramid level ``(width, height)``."""
        base_w, base_h = self.dimensions
        return [
            (base_w // (2**i), base_h // (2**i))
            for i in range(self.level_count)
        ]

    # -- public API ----------------------------------------------------------

    def get_level_pixel_size(self, level: int) -> float:
        """Get pixel size in µm for a specific pyramid *level*."""
        if level < len(self.metadata.zoom_pyramid):
            return self.metadata.zoom_pyramid[level].pixel_size_um
        return self.metadata.zoom_pyramid[0].pixel_size_um * (2**level)

    def get_channel(self, name: str) -> Optional[FilterChannel]:
        """Return :class:`FilterChannel` for *name*, or ``None``."""
        for ch in self.metadata.filters:
            if ch.name == name:
                return ch
        return None

    def read_channel(
        self, channel_name: str, zoom_level: int = 0
    ) -> Optional[np.ndarray]:
        """
        Read a complete channel image at *zoom_level*.

        Parameters
        ----------
        channel_name : str
            Fluorescence channel name (e.g. ``"DAPI"``).
        zoom_level : int
            0 = full resolution, higher = smaller.

        Returns
        -------
        numpy.ndarray or None
            2-D ``uint8`` array.
        """
        return self._data_reader.assemble_channel(channel_name, zoom_level)

    def get_thumbnail(
        self, channel_name: str = "DAPI", max_size: int = 512
    ) -> Optional[np.ndarray]:
        """
        Get a thumbnail using the highest (coarsest) zoom level.

        Parameters
        ----------
        channel_name : str
            Channel to thumbnail.
        max_size : int
            Advisory (actual size depends on available zoom levels).

        Returns
        -------
        numpy.ndarray or None
            2-D ``uint8`` thumbnail array.
        """
        return self._data_reader.assemble_channel(
            channel_name, self.level_count - 1
        )

    def create_composite(
        self,
        channels: List[str],
        zoom_level: int = 5,
        normalize: bool = True,
    ) -> Optional[np.ndarray]:
        """
        Create a multi-channel false-colour composite.

        Parameters
        ----------
        channels : list of str
            Channel names to include.
        zoom_level : int
            Pyramid level.
        normalize : bool
            Apply percentile-based intensity normalisation.

        Returns
        -------
        numpy.ndarray or None
            3-D RGB ``uint8`` array.
        """
        channel_images: Dict[str, np.ndarray] = {}
        target_shape = None

        for ch_name in channels:
            ch = self.get_channel(ch_name)
            if ch is None:
                print(f"Warning: Unknown channel '{ch_name}', skipping")
                continue

            img = self.read_channel(ch_name, zoom_level)
            if img is not None:
                channel_images[ch_name] = img
                if target_shape is None:
                    target_shape = img.shape

        if not channel_images:
            return None

        composite = np.zeros((*target_shape, 3), dtype=np.float32)

        for ch_name, img in channel_images.items():
            ch = self.get_channel(ch_name)

            # Resize if shapes don't match
            if img.shape != target_shape:
                pil_img = Image.fromarray(img)
                pil_img = pil_img.resize(
                    (target_shape[1], target_shape[0]), Image.LANCZOS
                )
                img = np.array(pil_img)

            # Normalise
            if normalize and img.max() > 0:
                nonzero = img[img > 0]
                if len(nonzero) > 100:
                    p1, p99 = np.percentile(nonzero, [1, 99])
                    if p99 > p1:
                        img_norm = np.clip(
                            (img.astype(np.float32) - p1) / (p99 - p1), 0, 1
                        )
                    else:
                        img_norm = img.astype(np.float32) / 255.0
                else:
                    img_norm = img.astype(np.float32) / 255.0
            else:
                img_norm = img.astype(np.float32) / 255.0

            # Apply channel colour
            r, g, b = ch.color_rgb
            composite[:, :, 0] += img_norm * (r / 255.0)
            composite[:, :, 1] += img_norm * (g / 255.0)
            composite[:, :, 2] += img_norm * (b / 255.0)

        return np.clip(composite * 255, 0, 255).astype(np.uint8)

    def get_slide_info(self) -> Dict:
        """
        Return a comprehensive dictionary of slide metadata.

        Keys include ``slide_id``, ``dimensions``, ``level_count``,
        ``pixel_size_um``, ``channels`` (list of dicts), etc.
        """
        return {
            "slide_id": self.slide_id,
            "slide_dir": str(self.slide_dir),
            "dimensions": self.dimensions,
            "tile_size": self.tile_size,
            "level_count": self.level_count,
            "level_dimensions": self.level_dimensions,
            "pixel_size_um": self.get_level_pixel_size(0),
            "objective_magnification": self.metadata.objective_magnification,
            "slide_type": self.metadata.slide_type,
            "channels": [
                {
                    "name": ch.name,
                    "filter_level": ch.data_filter_level,
                    "storing_channel": ch.storing_channel,
                    "color_rgb": ch.color_rgb,
                    "excitation_nm": ch.excitation_wavelength,
                    "emission_nm": ch.emission_wavelength,
                    "exposure_us": ch.exposure_time_us,
                }
                for ch in self.channels
            ],
        }
