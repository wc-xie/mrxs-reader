"""
INI Parser for MRXS Slidedat.ini files.

This module parses the Slidedat.ini metadata file that describes the structure
of MRXS slides, including hierarchy dimensions, filter channels, zoom levels,
and data file organisation.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class FilterChannel:
    """Information about a fluorescence filter channel."""

    index: int
    name: str
    excitation_wavelength: float
    excitation_bandwidth: float
    emission_wavelength: float
    emission_bandwidth: float
    color_rgb: Tuple[int, int, int]
    storing_channel: int  # R=0, G=1, B=2 in the RGB JPEG tile
    data_filter_level: str  # e.g. "FilterLevel_0" or "FilterLevel_1"
    exposure_time_us: int
    digital_gain: int
    is_master: bool = False
    is_stitching: bool = False


@dataclass
class ZoomLevel:
    """Information about a zoom / magnification level in the image pyramid."""

    level: int
    pixel_size_um: float
    digitizer_width: int
    digitizer_height: int
    concat_factor: int


@dataclass
class MrxsMetadata:
    """Complete metadata parsed from Slidedat.ini."""

    # General slide info
    slide_id: str
    slide_version: str
    slide_type: str
    compression: str
    compression_factor: int
    objective_magnification: int
    camera_bitdepth: int

    # Image dimensions (in tiles)
    tile_count_x: int
    tile_count_y: int

    # Hierarchy structure
    zoom_levels: int
    mask_levels: int
    filter_levels: int
    focus_levels: int

    # Parsed data
    filters: List[FilterChannel] = field(default_factory=list)
    zoom_pyramid: List[ZoomLevel] = field(default_factory=list)
    data_files: List[str] = field(default_factory=list)

    # Physical imaging parameters
    camera_tile_width: int = 0
    camera_tile_height: int = 0
    overlap_x: int = 0
    overlap_y: int = 0


def parse_slidedat_ini(ini_path: Path) -> MrxsMetadata:
    """
    Parse a Slidedat.ini file and return structured metadata.

    Parameters
    ----------
    ini_path : Path
        Path to the Slidedat.ini file.

    Returns
    -------
    MrxsMetadata
        Fully populated metadata object.

    Raises
    ------
    FileNotFoundError
        If the ini file does not exist.
    KeyError
        If required sections or keys are missing.
    """
    ini_path = Path(ini_path)
    if not ini_path.exists():
        raise FileNotFoundError(f"Slidedat.ini not found at {ini_path}")

    config = configparser.ConfigParser()
    # UTF-8-sig handles the BOM that 3DHISTECH prepends
    with open(ini_path, "r", encoding="utf-8-sig") as fh:
        config.read_file(fh)

    general = config["GENERAL"]

    hier = config["HIERARCHICAL"]
    zoom_levels = int(hier["HIER_0_COUNT"])
    mask_levels = int(hier["HIER_1_COUNT"])
    filter_levels = int(hier["HIER_2_COUNT"])
    focus_levels = int(hier["HIER_3_COUNT"])

    metadata = MrxsMetadata(
        slide_id=general["SLIDE_ID"],
        slide_version=general["SLIDE_VERSION"],
        slide_type=general["SLIDE_TYPE"],
        compression=general["COMPRESSION"],
        compression_factor=int(general["COMPRESSION_FACTOR"]),
        objective_magnification=int(general["OBJECTIVE_MAGNIFICATION"]),
        camera_bitdepth=int(general["VIMSLIDE_SLIDE_BITDEPTH"]),
        tile_count_x=int(general["IMAGENUMBER_X"]),
        tile_count_y=int(general["IMAGENUMBER_Y"]),
        zoom_levels=zoom_levels,
        mask_levels=mask_levels,
        filter_levels=filter_levels,
        focus_levels=focus_levels,
    )

    # ---- Data files ----
    datafile = config["DATAFILE"]
    file_count = int(datafile["FILE_COUNT"])
    for i in range(file_count):
        metadata.data_files.append(datafile[f"FILE_{i}"])

    # ---- Zoom pyramid ----
    for level in range(metadata.zoom_levels):
        section_name = f"LAYER_0_LEVEL_{level}_SECTION"
        if section_name in config:
            sec = config[section_name]
            metadata.zoom_pyramid.append(
                ZoomLevel(
                    level=level,
                    pixel_size_um=float(sec["MICROMETER_PER_PIXEL_X"]),
                    digitizer_width=int(sec["DIGITIZER_WIDTH"]),
                    digitizer_height=int(sec["DIGITIZER_HEIGHT"]),
                    concat_factor=int(sec["IMAGE_CONCAT_FACTOR"]),
                )
            )

    # ---- Filter channels ----
    for filter_idx in range(metadata.filter_levels):
        section_name = f"LAYER_2_LEVEL_{filter_idx}_SECTION"
        if section_name in config:
            sec = config[section_name]
            metadata.filters.append(
                FilterChannel(
                    index=filter_idx,
                    name=sec["FILTER_NAME"],
                    excitation_wavelength=float(sec["EXCITATION_WAVELENGTH"]),
                    excitation_bandwidth=0.0,
                    emission_wavelength=float(sec["EMISSION_WAVELENGTH"]),
                    emission_bandwidth=0.0,
                    color_rgb=(
                        int(sec["COLOR_R"]),
                        int(sec["COLOR_G"]),
                        int(sec["COLOR_B"]),
                    ),
                    storing_channel=int(sec["STORING_CHANNEL_NUMBER"]),
                    data_filter_level=sec["DATA_IN_THIS_FILTER_LEVEL"],
                    exposure_time_us=int(sec["EXPOSURE_TIME"]),
                    digital_gain=int(sec["DIGITALGAIN"]),
                    is_master=sec.get("IS_MASTER_FILTER", "False") == "True",
                    is_stitching=sec.get("IS_STITCHING_FILTER", "0") == "1",
                )
            )

    # ---- Stitching info ----
    if "NONHIERLAYER_0_LEVEL_0_SECTION" in config:
        stitch = config["NONHIERLAYER_0_LEVEL_0_SECTION"]
        metadata.camera_tile_width = int(
            stitch["COMPRESSSED_STITCHING_ORIG_CAMERA_TILE_WIDTH"]
        )
        metadata.camera_tile_height = int(
            stitch["COMPRESSSED_STITCHING_ORIG_CAMERA_TILE_HEIGHT"]
        )
        metadata.overlap_x = int(
            stitch["COMPRESSED_STITCHING_ORIG_CAMERA_TILE_OVERLAP_X"]
        )
        metadata.overlap_y = int(
            stitch["COMPRESSED_STITCHING_ORIG_CAMERA_TILE_OVERLAP_Y"]
        )

    return metadata
