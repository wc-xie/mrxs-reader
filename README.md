# mrxs-reader

**Python reader module for MRXS multi-channel fluorescence whole-slide images.**

`mrxs-reader` parses the MIRAX (`.mrxs`) file format. It handles `Slidedat.ini`
metadata, linked-list page traversal in `Index.dat`, and JPEG tile decoding
from `Data*.dat` files.

---

## Features

| Feature | Description |
|---------|-------------|
| **Pure Python** | Only requires NumPy + Pillow — works everywhere Python runs |
| **Multi-channel** | Reads all fluorescence channels (DAPI, SpGreen, SpOrange, CY5, SpAqua, …) |
| **Pyramid support** | Access any zoom level from full resolution to thumbnail |
| **Composites** | Generate false-colour multi-channel overlays with auto-normalisation |
| **OME-TIFF export** | Export channels as standards-compliant OME-TIFF with pixel size and wavelength metadata |
| **CLI** | Extract channels or create composites from the command line |
| **Lazy I/O** | Tiles read on demand — minimal memory footprint |

## Installation

```bash
pip install mrxs-reader
```

With TIFF export support:

```bash
pip install mrxs-reader[tiff]
```

With interactive napari viewer dependencies:

```bash
pip install mrxs-reader[viewer]
```

## Quick Start

### Python API

```python
from mrxs_reader import MrxsSlide

with MrxsSlide("filename.mrxs") as slide:
    # Slide metadata
    print(slide.channel_names)        # ['DAPI', 'SpGreen', 'SpOrange', 'CY5', 'SpAqua']
    print(slide.dimensions)           # (83968, 186624)
    print(slide.level_count)          # 10

    # Read a single channel at zoom level 5 (32× downsampled)
    dapi = slide.read_channel("DAPI", zoom_level=5)
    print(dapi.shape)                 # (5832, 2624)

    # Quick thumbnail
    thumb = slide.get_thumbnail("DAPI")

    # False-colour composite
    composite = slide.create_composite(
        channels=["DAPI", "SpGreen", "CY5"],
        zoom_level=7,
        normalize=True,
    )

    # Export all channels as a multi-channel OME-TIFF
    slide.export_ome_tiff(
        "output.ome.tiff",
        channels=["DAPI", "SpGreen", "CY5"],
        zoom_level=5,
    )
```

### Command Line

```bash
# Show slide information
mrxs-reader info filename.mrxs

# Extract all channels at zoom level 5
mrxs-reader extract filename.mrxs output/ --level 5

# Extract specific channels as PNG
mrxs-reader extract filename.mrxs output/ --channels DAPI SpGreen --level 7 --format png

# Create a composite image
mrxs-reader composite filename.mrxs composite.png --channels DAPI SpGreen CY5 --level 7

# Export all channels as a multi-channel OME-TIFF stack
mrxs-reader ometiff filename.mrxs output.ome.tiff

# Export specific channels as OME-TIFF at zoom level 5
mrxs-reader ometiff filename.mrxs output.ome.tiff --channels DAPI CY5 --level 5

# Extract channels as individual per-channel OME-TIFF files
mrxs-reader extract filename.mrxs output/ --format ome-tiff --level 5
```

Or via `python -m`:

```bash
python -m mrxs_reader info filename.mrxs
```

## MRXS Format Overview

A MRXS slide consists of:

| File | Purpose |
|------|---------|
| `*.mrxs` | Empty anchor file |
| `<name>/Slidedat.ini` | Metadata — channels, zoom levels, tile grid, pixel sizes |
| `<name>/Index.dat` | Binary index — linked-list pages mapping tiles to data file locations |
| `<name>/Data*.dat` | JPEG tiles — multiple fluorescence channels packed into RGB planes |

### Channel Packing

Each JPEG tile stores multiple fluorescence channels in its RGB colour
planes.  The `storing_channel_number` from `Slidedat.ini` determines which
RGB plane (0=R, 1=G, 2=B) holds which fluorescence signal:

| FilterLevel | R (ch 0) | G (ch 1) | B (ch 2) |
|-------------|----------|----------|----------|
| FilterLevel_0 | DAPI | SpGreen | SpOrange |
| FilterLevel_1 | CY5 | SpAqua | *(unused)* |

## API Reference

### `MrxsSlide`

The main entry point.  Use as a context manager.

| Property / Method | Description |
|---|---|
| `slide_id` | UUID string |
| `channel_names` | List of channel name strings |
| `channels` | List of `FilterChannel` dataclass instances |
| `dimensions` | `(width, height)` at full resolution |
| `level_count` | Number of pyramid levels |
| `level_dimensions` | List of `(w, h)` per level |
| `tile_size` | Tile edge length in pixels |
| `get_level_pixel_size(level)` | µm/pixel at *level* |
| `get_channel(name)` | `FilterChannel` or `None` |
| `read_channel(name, zoom_level=0)` | 2-D `uint8` ndarray |
| `get_thumbnail(name, max_size=512)` | 2-D `uint8` ndarray |
| `create_composite(channels, zoom_level, normalize)` | 3-D RGB `uint8` ndarray |
| `export_ome_tiff(output_path, channels, zoom_level)` | Write a multi-channel OME-TIFF with pixel size and wavelength metadata |
| `get_slide_info()` | Dict with all metadata |

### Low-level modules

| Module | Key classes |
|--------|------------|
| `mrxs_reader.ini_parser` | `MrxsMetadata`, `FilterChannel`, `ZoomLevel`, `parse_slidedat_ini()` |
| `mrxs_reader.index_parser` | `IndexParser`, `TileEntry`, `HierRecord` |
| `mrxs_reader.data_reader` | `DataReader` |

## Requirements

- Python ≥ 3.9
- NumPy ≥ 1.22
- Pillow ≥ 9.0
- *(optional)* tifffile — for TIFF export with metadata
- *(optional)* napari + dask — for interactive slide viewing

## License

MIT — see [LICENSE](LICENSE).

---

## Changelog

### 0.2.1
- Added OME-TIFF export: `MrxsSlide.export_ome_tiff()` writes a standards-compliant multi-channel OME-TIFF with physical pixel size and excitation/emission wavelength metadata.
- New `mrxs-reader ometiff` CLI command exports all (or selected) channels into a single stacked OME-TIFF file.
- `mrxs-reader extract --format ome-tiff` writes per-channel OME-TIFF files.
- GUI Export tab: added **OME-TIFF** format radio-button and **Export OME-TIFF Stack** button.
- Fixed shape mismatch when channels crop to slightly different heights at full resolution.
