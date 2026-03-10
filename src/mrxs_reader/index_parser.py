"""
Index Parser for MRXS Index.dat files.

This module parses the Index.dat binary file using the MIRAX linked-list page
structure (as documented by the OpenSlide project).  The index maps each tile
to its exact location (data file number, byte offset, byte size) at each
zoom level and filter level.

Index.dat layout
----------------

::

    Version string   "01.02"  (5 bytes)
    Slide UUID       variable length, matches SLIDE_ID from Slidedat.ini
    hier_root        int32    pointer to hierarchical table base
    nonhier_root     int32    pointer to non-hierarchical table base

Hierarchical table — array of int32 pointers, one per record.

Record order is sequential across HIER layers::

    HIER_0 (zoom):    zoom_levels records  →  records  0 .. N-1
    HIER_1 (mask):    zoom_levels records  →  records  N .. 2N-1
    HIER_2 (filter):  zoom_levels records  →  records 2N .. 3N-1

Each record pointer → ``list_head``::

    int32  initial_zero     sentinel (always 0)
    int32  first_data_page  file offset of first data page

Data page::

    int32  entries_count
    int32  next_page_ptr    (0 if this is the last page)
    entries_count × Entry

Entry (16 bytes)::

    int32  image_index      linear tile position = y * IMAGENUMBER_X + x
    int32  offset           byte offset in the data file
    int32  size             byte length of JPEG tile
    int32  fileno           data file number
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

INDEX_VERSION = "01.02"


@dataclass
class TileEntry:
    """Location of one JPEG tile inside a ``Data*.dat`` file."""

    image_index: int  # y * images_x + x
    offset: int       # byte offset
    size: int         # byte length of JPEG data
    fileno: int       # index into DATAFILE list


@dataclass
class HierRecord:
    """All tile entries for one hierarchical record (one zoom × layer)."""

    record_index: int
    entries: List[TileEntry] = field(default_factory=list)

    @property
    def tile_count(self) -> int:
        return len(self.entries)

    @property
    def data_files_used(self) -> set:
        return {e.fileno for e in self.entries}


class IndexParser:
    """
    Parser for MRXS Index.dat files.

    Uses the MIRAX linked-list page structure to locate every tile's exact
    position in the ``Data*.dat`` files.

    The hierarchical table contains records organised as:

    * Records ``0..N-1``    — Zoom layer (``FilterLevel_0``)
    * Records ``N..2N-1``   — Mask layer
    * Records ``2N..3N-1``  — Filter layer (``FilterLevel_1``)

    Parameters
    ----------
    index_path : Path
        Path to the ``Index.dat`` file.
    slide_id : str
        Slide UUID (from ``Slidedat.ini`` ``GENERAL/SLIDE_ID``).
    zoom_levels : int
        Number of zoom levels (from ``Slidedat.ini``).
    """

    def __init__(self, index_path: Path, slide_id: str, zoom_levels: int):
        self.index_path = Path(index_path)
        self.slide_id = slide_id
        self.zoom_levels = zoom_levels

        self._file_handle = None
        self._hier_table_base: int = 0
        self._nonhier_table_base: int = 0
        self._record_cache: Dict[int, HierRecord] = {}

    # -- context manager -----------------------------------------------------

    def __enter__(self):
        self._file_handle = open(self.index_path, "rb")
        self._parse_header()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None

    # -- internal ------------------------------------------------------------

    def _parse_header(self):
        f = self._file_handle
        f.seek(0)

        version = f.read(len(INDEX_VERSION)).decode("ascii")
        if version != INDEX_VERSION:
            raise ValueError(
                f"Unexpected Index.dat version: {version!r} (expected {INDEX_VERSION!r})"
            )

        found_id = f.read(len(self.slide_id)).decode("ascii")
        if found_id != self.slide_id:
            raise ValueError(f"Slide ID mismatch: {found_id!r} != {self.slide_id!r}")

        self._hier_table_base = struct.unpack("<i", f.read(4))[0]
        self._nonhier_table_base = struct.unpack("<i", f.read(4))[0]

    def _read_int32(self) -> int:
        data = self._file_handle.read(4)
        if len(data) != 4:
            raise EOFError("Unexpected end of Index.dat")
        return struct.unpack("<i", data)[0]

    def _read_record(self, record_index: int) -> HierRecord:
        if record_index in self._record_cache:
            return self._record_cache[record_index]

        f = self._file_handle
        record = HierRecord(record_index=record_index)

        f.seek(self._hier_table_base + record_index * 4)
        list_head_ptr = self._read_int32()

        if list_head_ptr <= 0:
            self._record_cache[record_index] = record
            return record

        f.seek(list_head_ptr)
        initial_zero = self._read_int32()
        if initial_zero != 0:
            self._record_cache[record_index] = record
            return record

        first_data_page = self._read_int32()
        if first_data_page <= 0:
            self._record_cache[record_index] = record
            return record

        current_page = first_data_page
        while current_page > 0:
            f.seek(current_page)
            entries_count = self._read_int32()
            next_page = self._read_int32()

            for _ in range(entries_count):
                record.entries.append(
                    TileEntry(
                        image_index=self._read_int32(),
                        offset=self._read_int32(),
                        size=self._read_int32(),
                        fileno=self._read_int32(),
                    )
                )

            current_page = next_page

        self._record_cache[record_index] = record
        return record

    # -- public API ----------------------------------------------------------

    def get_zoom_record(self, zoom_level: int) -> HierRecord:
        """Get FilterLevel_0 tile entries at *zoom_level*."""
        if not 0 <= zoom_level < self.zoom_levels:
            raise ValueError(
                f"zoom_level {zoom_level} out of range [0, {self.zoom_levels})"
            )
        return self._read_record(zoom_level)

    def get_mask_record(self, zoom_level: int) -> HierRecord:
        """Get mask tile entries at *zoom_level*."""
        if not 0 <= zoom_level < self.zoom_levels:
            raise ValueError(
                f"zoom_level {zoom_level} out of range [0, {self.zoom_levels})"
            )
        return self._read_record(self.zoom_levels + zoom_level)

    def get_filter_record(self, zoom_level: int) -> HierRecord:
        """Get FilterLevel_1 tile entries at *zoom_level*."""
        if not 0 <= zoom_level < self.zoom_levels:
            raise ValueError(
                f"zoom_level {zoom_level} out of range [0, {self.zoom_levels})"
            )
        return self._read_record(2 * self.zoom_levels + zoom_level)

    def get_record_for_filter_level(
        self, filter_level_name: str, zoom_level: int
    ) -> HierRecord:
        """
        Get tile entries for a named filter level at a zoom level.

        Parameters
        ----------
        filter_level_name : str
            ``"FilterLevel_0"`` or ``"FilterLevel_1"``.
        zoom_level : int
            0 = full resolution.
        """
        if filter_level_name == "FilterLevel_0":
            return self.get_zoom_record(zoom_level)
        if filter_level_name == "FilterLevel_1":
            return self.get_filter_record(zoom_level)
        raise ValueError(f"Unknown filter level: {filter_level_name}")

    def get_summary(self) -> Dict:
        """Return a dict summarising every record (useful for debugging)."""
        summary: Dict = {
            "file_size": self.index_path.stat().st_size,
            "hier_table_base": self._hier_table_base,
            "nonhier_table_base": self._nonhier_table_base,
            "zoom_levels": self.zoom_levels,
            "records": {},
        }

        total_records = 3 * self.zoom_levels
        for i in range(total_records):
            try:
                record = self._read_record(i)
                if i < self.zoom_levels:
                    layer = "zoom/FL0"
                elif i < 2 * self.zoom_levels:
                    layer = "mask"
                else:
                    layer = "filter/FL1"
                summary["records"][i] = {
                    "layer": layer,
                    "level": i % self.zoom_levels,
                    "tile_count": record.tile_count,
                    "data_files": sorted(record.data_files_used) if record.entries else [],
                }
            except Exception:
                break

        return summary
