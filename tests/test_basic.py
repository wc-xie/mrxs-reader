"""Basic smoke tests for mrxs_reader package."""

from __future__ import annotations


def test_import():
    """Package imports without error."""
    import mrxs_reader

    assert hasattr(mrxs_reader, "MrxsSlide")
    assert hasattr(mrxs_reader, "__version__")


def test_version_string():
    from mrxs_reader import __version__

    assert isinstance(__version__, str)
    parts = __version__.split(".")
    assert len(parts) >= 2


def test_public_api():
    """All expected symbols are importable."""
    from mrxs_reader import (
        MrxsSlide,
        MrxsMetadata,
        FilterChannel,
        ZoomLevel,
        IndexParser,
        TileEntry,
        HierRecord,
        DataReader,
        parse_slidedat_ini,
    )
