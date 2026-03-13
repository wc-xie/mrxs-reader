"""
Index Parser for MRXS Index.dat files.

This module parses the Index.dat binary file using the MIRAX linked-list page 
structure (as documented by the OpenSlide project). The index maps each tile
to its exact location (data file number, byte offset, byte size) at each 
zoom level and filter level.

Index.dat layout:
  - Version string "01.02" (5 bytes)
  - Slide UUID string (variable length, matches SLIDE_ID from Slidedat.ini)
  - hier_root: int32 pointer to hierarchical table base
  - nonhier_root: int32 pointer to non-hierarchical table base
  
  Hierarchical table:
  - Array of int32 pointers, one per hier record
  - Record order is sequential across HIER layers:
      HIER_0 (zoom):    zoom_levels  records -> records 0 .. Z-1
      HIER_1 (mask):    mask_levels  records -> records Z .. Z+M-1
      HIER_2 (filter):  filter_levels records -> records Z+M .. Z+M+F-1
      where Z=zoom_levels, M=mask_levels, F=filter_levels

  FilterLevel_N maps to HIER_2 entry N, so its record index is
  Z + M + N (not necessarily 2*Z + N).
  
  Each record pointer -> list_head:
    - int32 = 0 (initial_zero sentinel)
    - int32 = first_data_page_ptr
  
  Data page:
    - int32 entries_count (number of entries in this page)
    - int32 next_page_ptr (0 if last page)
    - entries_count × Entry

  Entry (16 bytes):
    - int32 image_index:  linear tile position = y * IMAGENUMBER_X + x
    - int32 offset:       byte offset in the data file
    - int32 size:         byte length of JPEG tile
    - int32 fileno:       data file number (index into FILE_{} in DATAFILE section)
"""

import struct
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from pathlib import Path


INDEX_VERSION = "01.02"


@dataclass
class TileEntry:
    """A single tile entry from the index, giving its exact location in a data file."""
    image_index: int   # Linear position: y * images_x + x
    offset: int        # Byte offset in the data file
    size: int          # Byte length of JPEG data
    fileno: int        # Data file number (index into DATAFILE list)


@dataclass
class HierRecord:
    """All tile entries for one hierarchical record (one zoom level of one layer)."""
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
    position in the Data*.dat files.
    
    The hierarchical table contains records organised as:
      Records 0 .. Z-1           : HIER_0 zoom layer (FilterLevel_0 packed tiles)
      Records Z .. Z+M-1         : HIER_1 mask layer
      Records Z+M .. Z+M+F-1     : HIER_2 filter layers (one entry per FilterLevel_N)
    where Z=zoom_levels, M=mask_levels, F=filter_levels.

    FilterLevel_N in HIER_2 is at record index Z + M + N.
    With 5 filter levels the mapping is:
      FilterLevel_0 → HIER_2[0] (DAPI/SpGreen/SpOrange packed)
      FilterLevel_1 → HIER_2[1]
      FilterLevel_2 → HIER_2[2]
      FilterLevel_3 → HIER_2[3] (CY5)
      FilterLevel_4 → HIER_2[4] (SpAqua)
    """
    
    def __init__(self, index_path: Path, slide_id: str, zoom_levels: int,
                 mask_levels: int = 2, filter_levels: int = 2,
                 hier_layout=None, zoom_hier_index: int = 0,
                 filter_hier_index: int = 2):
        """
        Initialize parser.

        Args:
            index_path: Path to Index.dat file
            slide_id: Slide UUID from Slidedat.ini GENERAL/SLIDE_ID
            zoom_levels: Number of zoom levels (HIER_0_COUNT typically)
            mask_levels: Mask levels count (legacy, used if hier_layout is None)
            filter_levels: Filter levels count (legacy, used if hier_layout is None)
            hier_layout: Ordered list of (hier_index, count) from MrxsMetadata.
                         Defines the record layout in the index file.
            zoom_hier_index: Which HIER holds zoom records
            filter_hier_index: Which HIER holds filter records
        """
        self.index_path = index_path
        self.slide_id = slide_id
        self.zoom_levels = zoom_levels
        self.mask_levels = mask_levels
        self.filter_levels = filter_levels

        # Compute record base offsets from the ordered hierarchy layout.
        #
        # Each HIER layer always contributes exactly `zoom_levels` records in the
        # binary index file, regardless of HIER_N_COUNT.  COUNT encodes the number
        # of channels packed per tile in that layer (e.g. 3 for FL0, 2 for FL1),
        # not the number of records.  Using cumulative COUNT values as offsets
        # therefore gives wrong bases on slides where COUNT ≠ zoom_levels.
        #
        # Correct formula:  base[hier_idx] = layer_order × zoom_levels
        self._hier_bases: Dict[int, int] = {}
        if hier_layout:
            for order, (hier_idx, _count) in enumerate(hier_layout):
                self._hier_bases[hier_idx] = order * zoom_levels
        else:
            # Legacy fallback: HIER_0=zoom, HIER_1=mask, HIER_2=filter
            self._hier_bases = {0: 0, 1: zoom_levels, 2: 2 * zoom_levels}

        self._zoom_hier_index = zoom_hier_index
        self._filter_hier_index = filter_hier_index

        self._file_handle = None
        self._hier_table_base: int = 0
        self._nonhier_table_base: int = 0
        self._record_cache: Dict[int, HierRecord] = {}
    
    def __enter__(self):
        self._file_handle = open(self.index_path, 'rb')
        self._parse_header()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None
    
    def _parse_header(self):
        """Parse the index file header to find table base pointers."""
        f = self._file_handle
        f.seek(0)
        
        # Read and verify version
        version = f.read(len(INDEX_VERSION)).decode('ascii')
        if version != INDEX_VERSION:
            raise ValueError(f"Unexpected Index.dat version: {version!r}, expected {INDEX_VERSION!r}")
        
        # Read and verify slide ID
        found_id = f.read(len(self.slide_id)).decode('ascii')
        if found_id != self.slide_id:
            raise ValueError(f"Slide ID mismatch: {found_id!r} != {self.slide_id!r}")
        
        # hier_root is right after version + slide_id
        # Read table base pointers
        self._hier_table_base = struct.unpack('<i', f.read(4))[0]
        self._nonhier_table_base = struct.unpack('<i', f.read(4))[0]
    
    def _read_int32(self) -> int:
        """Read a little-endian int32 from the current file position."""
        data = self._file_handle.read(4)
        if len(data) != 4:
            raise EOFError("Unexpected end of Index.dat")
        return struct.unpack('<i', data)[0]
    
    def _read_record(self, record_index: int) -> HierRecord:
        """
        Read all tile entries for a hierarchical record by traversing its linked list.
        
        Args:
            record_index: Zero-based index into the hier table
            
        Returns:
            HierRecord with all tile entries
        """
        if record_index in self._record_cache:
            return self._record_cache[record_index]
        
        f = self._file_handle
        record = HierRecord(record_index=record_index)
        
        # Read table pointer for this record
        f.seek(self._hier_table_base + record_index * 4)
        list_head_ptr = self._read_int32()
        
        if list_head_ptr <= 0:
            self._record_cache[record_index] = record
            return record
        
        # Navigate to list head
        f.seek(list_head_ptr)
        initial_zero = self._read_int32()
        if initial_zero != 0:
            # Not a valid list head
            self._record_cache[record_index] = record
            return record
        
        first_data_page = self._read_int32()
        if first_data_page <= 0:
            self._record_cache[record_index] = record
            return record
        
        # Traverse linked list of data pages
        current_page = first_data_page
        while current_page > 0:
            f.seek(current_page)
            entries_count = self._read_int32()
            next_page = self._read_int32()
            
            # Read entries
            for _ in range(entries_count):
                image_index = self._read_int32()
                offset = self._read_int32()
                size = self._read_int32()
                fileno = self._read_int32()
                
                record.entries.append(TileEntry(
                    image_index=image_index,
                    offset=offset,
                    size=size,
                    fileno=fileno
                ))
            
            current_page = next_page
        
        self._record_cache[record_index] = record
        return record
    
    def get_zoom_record(self, zoom_level: int) -> HierRecord:
        """
        Get tile entries for FilterLevel_0 at a specific zoom level.

        Args:
            zoom_level: 0 = full resolution, higher = more downsampled

        Returns:
            HierRecord with all tile entries at this zoom level
        """
        if zoom_level < 0 or zoom_level >= self.zoom_levels:
            raise ValueError(f"Zoom level {zoom_level} out of range [0, {self.zoom_levels})")
        base = self._hier_bases[self._zoom_hier_index]
        return self._read_record(base + zoom_level)
    
    def get_filter_record(self, filter_level_index: int, zoom_level: int) -> HierRecord:
        """
        Get tile entries for the filter hierarchy layer at a specific zoom level.

        The filter hierarchy stores all filter channels packed together (just
        like the zoom hierarchy stores all FL0 channels packed in RGB).

        Args:
            filter_level_index: Accepted but unused (all FL>0 channels share
                the same filter hierarchy)
            zoom_level: 0 = full resolution, higher = more downsampled

        Returns:
            HierRecord with all tile entries at this zoom level
        """
        if zoom_level < 0 or zoom_level >= self.zoom_levels:
            raise ValueError(f"Zoom level {zoom_level} out of range [0, {self.zoom_levels})")
        base = self._hier_bases[self._filter_hier_index]
        return self._read_record(base + zoom_level)
    
    def get_mask_record(self, zoom_level: int) -> HierRecord:
        """
        Get mask tile entries at a specific zoom level.
        """
        if zoom_level < 0 or zoom_level >= self.zoom_levels:
            raise ValueError(f"Zoom level {zoom_level} out of range [0, {self.zoom_levels})")
        mask_hier = 1  # default
        for idx in self._hier_bases:
            if idx != self._zoom_hier_index and idx != self._filter_hier_index:
                mask_hier = idx
                break
        return self._read_record(self._hier_bases[mask_hier] + zoom_level)
    
    def get_record_for_filter_level(self, filter_level_name: str, zoom_level: int) -> HierRecord:
        """
        Get tile entries for a named filter level at a zoom level.

        FilterLevel_0 tiles are stored in HIER_0 (zoom layer).
        FilterLevel_N (N>=1) tiles are stored in HIER_2 at index N.
        
        Args:
            filter_level_name: e.g. "FilterLevel_0", "FilterLevel_1", "FilterLevel_3"
            zoom_level: Zoom level (0 = full resolution)
            
        Returns:
            HierRecord with tile entries
        """
        if filter_level_name == "FilterLevel_0":
            return self.get_zoom_record(zoom_level)
        elif filter_level_name.startswith("FilterLevel_"):
            idx = int(filter_level_name.split("_")[-1])
            return self.get_filter_record(idx, zoom_level)
        else:
            raise ValueError(f"Unknown filter level: {filter_level_name}")
    
    def get_summary(self) -> Dict:
        """Get a summary of the index structure for debugging."""
        summary = {
            'file_size': self.index_path.stat().st_size,
            'hier_table_base': self._hier_table_base,
            'nonhier_table_base': self._nonhier_table_base,
            'zoom_levels': self.zoom_levels,
            'records': {}
        }
        
        # Read all records: HIER_0 (zoom_levels) + HIER_1 (mask_levels) + HIER_2 (zoom_levels)
        total_records = self.zoom_levels + self.mask_levels + self.zoom_levels
        for i in range(total_records):
            try:
                record = self._read_record(i)
                if i < self._hier1_base:
                    layer = "zoom/FL0"
                    level = i
                elif i < self._hier2_base:
                    layer = "mask"
                    level = i - self._hier1_base
                else:
                    layer = "filter/FL1+"
                    level = i - self._hier2_base
                files = sorted(record.data_files_used) if record.entries else []
                summary['records'][i] = {
                    'layer': layer,
                    'level': level,
                    'tile_count': record.tile_count,
                    'data_files': files
                }
            except Exception:
                break
        
        return summary
