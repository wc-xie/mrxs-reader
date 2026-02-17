"""
mrxs_reader — Pure-Python reader for 3DHISTECH MRXS multi-channel fluorescence slides.

Parses the proprietary MIRAX/MRXS format without any native dependencies:

* ``Slidedat.ini`` — slide metadata, channel definitions, zoom pyramid
* ``Index.dat``    — linked-list page structure mapping tiles to data files
* ``Data*.dat``    — JPEG tile storage (multiple channels packed in RGB)

Quick start::

    from mrxs_reader import MrxsSlide

    with MrxsSlide("filename") as slide:
        print(slide.channel_names)          # ['DAPI', 'SpGreen', ...]
        dapi = slide.read_channel("DAPI", zoom_level=5)

See Also
--------
* Project repository: https://github.com/your-org/mrxs-reader
* Index.dat format derived from the OpenSlide project (openslide.org)
"""

from .slide import MrxsSlide
from .ini_parser import (
    MrxsMetadata,
    FilterChannel,
    ZoomLevel,
    parse_slidedat_ini,
)
from .index_parser import IndexParser, TileEntry, HierRecord
from .data_reader import DataReader

__version__ = "0.1.0"
__all__ = [
    "MrxsSlide",
    "MrxsMetadata",
    "FilterChannel",
    "ZoomLevel",
    "IndexParser",
    "TileEntry",
    "HierRecord",
    "DataReader",
    "parse_slidedat_ini",
]
