"""MCP server (stdio) exposing four read-only Trefle tools.

Run:  TREFLE_TOKEN=... python -m trefle_mcp.server
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import TrefleClient, TrefleError

mcp = FastMCP("trefle")

_client: TrefleClient | None = None
_factory: Callable[[], TrefleClient] = TrefleClient


def set_client_factory(factory: Callable[[], TrefleClient]) -> None:
    """Test hook: swap how the shared client is built."""
    global _client, _factory
    _client = None
    _factory = factory


def _get_client() -> TrefleClient:
    global _client
    if _client is None:
        _client = _factory()
    return _client


def _run(fn: Callable[[TrefleClient], Any]) -> dict[str, Any]:
    """Tool errors come back as a structured dict instead of a stack trace."""
    try:
        out = fn(_get_client())
    except TrefleError as exc:
        return exc.as_dict()
    return out.as_dict() if hasattr(out, "as_dict") else out


@mcp.tool()
def search_species(q: str, filters: dict[str, str] | None = None, ranges: dict[str, str] | None = None,
                   page: int = 1, limit: int = 10) -> dict[str, Any]:
    """Full-text search of plant species by common or scientific name.

    filters: exact-match keys, e.g. {"edible": "true", "flower_color": "red,white"}.
    ranges: "min,max" bounds, e.g. {"ph_minimum": "5,", "light": "6,10"}.
    limit is capped at 50.
    """
    return _run(lambda c: c.search_species(q, filters=filters, ranges=ranges, page=page, limit=limit))


@mcp.tool()
def get_species(id_or_slug: str) -> dict[str, Any]:
    """Full species card (growth conditions, images, distributions, synonyms) by id or slug."""
    return _run(lambda c: c.get_species(id_or_slug))


@mcp.tool()
def list_plants(filters: dict[str, str] | None = None, ranges: dict[str, str] | None = None,
                page: int = 1, limit: int = 10) -> dict[str, Any]:
    """List plants filtered by growing conditions, e.g. ranges={"minimum_temperature_deg_c": "-10,"}."""
    return _run(lambda c: c.list_plants(filters=filters, ranges=ranges, page=page, limit=limit))


@mcp.tool()
def species_in_distribution(zone: str, establishment: str | None = None, page: int = 1,
                            limit: int = 10) -> dict[str, Any]:
    """Species recorded in a geographic zone (slug or TDWG code like 'fra').

    establishment: optional 'native' or 'introduced'.
    """
    return _run(lambda c: c.species_in_distribution(zone, establishment=establishment, page=page, limit=limit))


def main() -> None:
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
