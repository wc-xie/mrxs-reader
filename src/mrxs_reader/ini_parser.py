"""
INI Parser for MRXS Slidedat.ini files.

This module parses the Slidedat.ini metadata file that describes the structure
of MRXS slides, including hierarchy dimensions, filter channels, zoom levels,
and data file organization.
"""

import configparser
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from pathlib import Path


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
    data_filter_level: str  # "FilterLevel_0" or "FilterLevel_1"
    exposure_time_us: int
    digital_gain: int
    is_master: bool = False
    is_stitching: bool = False


@dataclass  
class ZoomLevel:
    """Information about a zoom/magnification level."""
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

    # Hierarchy layer indices (which HIER_N holds zoom/mask/filter/focus)
    zoom_hier_index: int = 0
    mask_hier_index: int = 1
    filter_hier_index: int = 2
    focus_hier_index: int = 3
    # Ordered list of (hier_index, count) for computing record offsets
    hier_layout: List[Tuple[int, int]] = field(default_factory=list)

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
    
    Args:
        ini_path: Path to the Slidedat.ini file
        
    Returns:
        MrxsMetadata object with all parsed information
        
    Raises:
        FileNotFoundError: If ini file doesn't exist
        ValueError: If required sections/keys are missing
    """
    if not ini_path.exists():
        raise FileNotFoundError(f"Slidedat.ini not found at {ini_path}")
    
    config = configparser.ConfigParser()
    # Read file with UTF-8 encoding to handle BOM
    with open(ini_path, 'r', encoding='utf-8-sig') as f:
        config.read_file(f)
    
    # Parse general section
    general = config['GENERAL']

    # Parse hierarchical structure — dynamically detect which HIER index
    # corresponds to zoom, mask, filter, and focus based on HIER_*_NAME.
    # Different scanner versions use different orderings.
    hier = config['HIERARCHICAL']
    hier_count = int(hier['HIER_COUNT'])

    zoom_hier_idx, mask_hier_idx, filter_hier_idx, focus_hier_idx = 0, -1, -1, -1
    zoom_levels = mask_levels = filter_levels = focus_levels = 0
    hier_layout = []  # ordered (hier_index, count)

    for i in range(hier_count):
        name = hier[f'HIER_{i}_NAME'].lower()
        count = int(hier[f'HIER_{i}_COUNT'])
        hier_layout.append((i, count))
        if 'zoom' in name and 'mask' not in name:
            zoom_hier_idx = i
            zoom_levels = count
        elif 'mask' in name:
            mask_hier_idx = i
            mask_levels = count
        elif 'filter' in name:
            filter_hier_idx = i
            filter_levels = count
        elif 'focus' in name:
            focus_hier_idx = i
            focus_levels = count

    metadata = MrxsMetadata(
        slide_id=general['SLIDE_ID'],
        slide_version=general['SLIDE_VERSION'],
        slide_type=general['SLIDE_TYPE'],
        compression=general.get('COMPRESSION', 'JPEG'),
        compression_factor=int(general.get('COMPRESSION_FACTOR', '80')),
        objective_magnification=int(general['OBJECTIVE_MAGNIFICATION']),
        camera_bitdepth=int(general['VIMSLIDE_SLIDE_BITDEPTH']),
        tile_count_x=int(general['IMAGENUMBER_X']),
        tile_count_y=int(general['IMAGENUMBER_Y']),
        zoom_levels=zoom_levels,
        mask_levels=mask_levels,
        filter_levels=filter_levels,
        focus_levels=focus_levels,
        zoom_hier_index=zoom_hier_idx,
        mask_hier_index=mask_hier_idx,
        filter_hier_index=filter_hier_idx,
        focus_hier_index=focus_hier_idx,
        hier_layout=hier_layout,
    )
    
    # Parse data files
    datafile = config['DATAFILE']
    file_count = int(datafile['FILE_COUNT'])
    for i in range(file_count):
        filename = datafile[f'FILE_{i}']
        metadata.data_files.append(filename)
    
    # Parse zoom levels
    for level in range(metadata.zoom_levels):
        section_name = f'LAYER_0_LEVEL_{level}_SECTION'
        if section_name in config:
            section = config[section_name]
            zoom_level = ZoomLevel(
                level=level,
                pixel_size_um=float(section['MICROMETER_PER_PIXEL_X']),
                digitizer_width=int(section['DIGITIZER_WIDTH']),
                digitizer_height=int(section['DIGITIZER_HEIGHT']),
                concat_factor=int(section['IMAGE_CONCAT_FACTOR'])
            )
            metadata.zoom_pyramid.append(zoom_level)
    
    # Parse filter channels from the dynamically detected filter layer
    filter_layer = metadata.filter_hier_index
    for filter_idx in range(metadata.filter_levels):
        section_name = f'LAYER_{filter_layer}_LEVEL_{filter_idx}_SECTION'
        if section_name in config:
            section = config[section_name]
            
            ex_center = float(section['EXCITATION_WAVELENGTH'])
            ex_bw = float(section.get('EXCITATION_BANDWIDTH', '0'))
            em_center = float(section['EMISSION_WAVELENGTH'])
            em_bw = float(section.get('EMISSION_BANDWIDTH', '0'))
            
            # Parse color from individual R,G,B values
            color_r = int(section['COLOR_R'])
            color_g = int(section['COLOR_G']) 
            color_b = int(section['COLOR_B'])
            
            filter_channel = FilterChannel(
                index=filter_idx,
                name=section['FILTER_NAME'],
                excitation_wavelength=ex_center,
                excitation_bandwidth=ex_bw,
                emission_wavelength=em_center,
                emission_bandwidth=em_bw,
                color_rgb=(color_r, color_g, color_b),
                storing_channel=int(section['STORING_CHANNEL_NUMBER']),
                data_filter_level=section['DATA_IN_THIS_FILTER_LEVEL'],
                exposure_time_us=int(section['EXPOSURE_TIME']),
                digital_gain=int(section['DIGITALGAIN']),
                is_master=section.get('IS_MASTER_FILTER', 'False') == 'True',
                is_stitching=section.get('IS_STITCHING_FILTER', '0') == '1'
            )
            metadata.filters.append(filter_channel)
    
    # Parse stitching info — try multiple sections since location varies by scanner
    stitch = None
    for section_name in ['NONHIERLAYER_0_LEVEL_0_SECTION', 'NONHIERLAYER_2_LEVEL_0_SECTION']:
        if section_name in config:
            candidate = config[section_name]
            if 'COMPRESSSED_STITCHING_ORIG_CAMERA_TILE_WIDTH' in candidate:
                stitch = candidate
                break
    if stitch is not None:
        metadata.camera_tile_width = int(stitch['COMPRESSSED_STITCHING_ORIG_CAMERA_TILE_WIDTH'])
        metadata.camera_tile_height = int(stitch['COMPRESSSED_STITCHING_ORIG_CAMERA_TILE_HEIGHT'])
        metadata.overlap_x = int(stitch['COMPRESSED_STITCHING_ORIG_CAMERA_TILE_OVERLAP_X'])
        metadata.overlap_y = int(stitch['COMPRESSED_STITCHING_ORIG_CAMERA_TILE_OVERLAP_Y'])
    
    return metadata


def _parse_wavelength(wavelength_str: str) -> Tuple[float, float]:
    """Parse wavelength string like '377±50' into center and bandwidth."""
    if '±' in wavelength_str:
        center_str, width_str = wavelength_str.split('±')
        return float(center_str), float(width_str)
    else:
        # Fallback for other formats
        return float(wavelength_str), 0.0


def _parse_color(color_str: str) -> Tuple[int, int, int]:
    """Parse color string like '(255,0,0)' into RGB tuple."""
    # Remove parentheses and split
    color_str = color_str.strip('()')
    r, g, b = map(int, color_str.split(','))
    return (r, g, b)


if __name__ == "__main__":
    # Test with one of the slides
    test_path = Path("/Volumes/OmicsDisk/Cardiac Adhesive/E-Animal/Aneurysm_Protect/Pathology/Fibronectin+Integrinbeta1+YAP+Factin/MB-21/Slidedat.ini")
    metadata = parse_slidedat_ini(test_path)
    print(f"Parsed slide: {metadata.slide_id}")
    print(f"Filters: {len(metadata.filters)}")
    for f in metadata.filters:
        print(f"  {f.name}: {f.excitation_wavelength}±{f.excitation_bandwidth} → {f.emission_wavelength}±{f.emission_bandwidth}")