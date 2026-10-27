"""HTTP scraper with a temporary export of the unchanged legacy CLI scraper."""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from app.scrapers.betexplorer.client import BetExplorerScraper

# The package shadows betexplorer.py. Keep the CLI on that original implementation.
_legacy_spec = spec_from_file_location(
    "app.scrapers._legacy_betexplorer", Path(__file__).resolve().parent.with_suffix(".py")
)
assert _legacy_spec is not None and _legacy_spec.loader is not None
_legacy_module = module_from_spec(_legacy_spec)
sys.modules[_legacy_spec.name] = _legacy_module
_legacy_spec.loader.exec_module(_legacy_module)
Scraper = _legacy_module.Scraper

__all__ = ["BetExplorerScraper", "Scraper"]
