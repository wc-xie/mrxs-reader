"""
MRXS Reader - A pure Python package for reading multi-channel MRXS fluorescence slides.

Parses the proprietary 3DHISTECH MRXS format:
  - Slidedat.ini: Slide metadata, channel definitions, zoom levels
  - Index.dat:    Linked-list page structure mapping tiles to data file locations
  - Data*.dat:    JPEG tile storage (multiple channels packed in RGB)

Usage:
    from mrxs_reader import MrxsSlide

    with MrxsSlide("MB-21") as slide:
        dapi = slide.read_channel("DAPI", zoom_level=5)
"""

from .slide import MrxsSlide

__version__ = "0.2.0"
__author__ = "MRXS Research Team"

__all__ = ["MrxsSlide"]