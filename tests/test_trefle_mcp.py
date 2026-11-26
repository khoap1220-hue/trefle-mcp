"""Offline tests: every HTTP call is mocked with respx. No token or network needed."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx
from trefle_mcp import server
from trefle_mcp.client import MAX_PAGE_SIZE, TrefleClient, TrefleError

BASE = "https://trefle.test/api/v1"
FAKE_TOKEN = "test-token-not-real"

SPECIES = {
    "id": 167888, "slug": "cocos-nucifera", "scientific_name": "Cocos nucifera",
    "common_name": "Coconut palm", "family": "Arecaceae", "genus": "Cocos", "year": 1753,
    "rank": "species", "status": "accepted", "image_url": "https://example.test/c.jpg",
    "links": {"self": "/api/v1/species/cocos-nucifera"}, "synonyms": ["x"] * 40,
}


def page_body(records, total=1, nxt=None):
    links = {"self": "/x"}
    if nxt:
        links["next"] = nxt
    return {"data": records, "links": links, "meta": {"total": total}}


@pytest.fixture
def client():
    c = TrefleClient(token=FAKE_TOKEN, base_url=BASE)
    yield c
    c.close()


@pytest.fixture
def wired(client):
    server.set_client_factory(lambda: client)
    yield client
    server.set_client_factory(TrefleClient)


def test_missing_token_fails_fast(monkeypatch):
    monkeypatch.delenv("TREFLE_TOKEN", raising=False)
    with pytest.raises(TrefleError) as e:
        TrefleClient(base_url=BASE)
    assert e.value.code == "missing_token"


@respx.mock
def test_token_sent_as_bearer_never_in_url(client):
    route = respx.get(f"{BASE}/species/search").mock(return_value=httpx.Response(200, json=page_body([SPECIES])))
    client.search_species("coconut")
    req = route.calls.last.request
    assert req.headers["Authorization"] == f"Bearer {FAKE_TOKEN}"
    assert FAKE_TOKEN not in str(req.url)
    assert "token" not in req.url.params


@respx.mock
def test_search_encodes_filters_ranges_and_caps_limit(client):
    route = respx.get(f"{BASE}/species/search").mock(
        return_value=httpx.Response(200, json=page_body([SPECIES], total=45, nxt="/p2")))
    page = client.search_species("palm", filters={"edible": "true", "flower_color": ["red", "white"]},
                                 ranges={"ph_minimum": "5,"}, limit=500)
    params = route.calls.last.request.url.params
    assert params["q"] == "palm"
    assert params["filter[edible]"] == "true"
    assert params["filter[flower_color]"] == "red,white"
    assert params["range[ph_minimum]"] == "5,"
    assert params["limit"] == str(MAX_PAGE_SIZE)
    out = page.as_dict()
    assert out["total"] == 45 and out["has_next_page"] is True
    assert "links" not in out["results"][0] and "synonyms" not in out["results"][0]


def test_unknown_filter_key_rejected_before_any_request(client):
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(f"{BASE}/plants")
        with pytest.raises(TrefleError) as e:
            client.list_plants(filters={"not_a_field": "1"})
        assert not route.called
    assert "not_a_field" in str(e.value)


@respx.mock
def test_error_envelope_is_mapped(client):
    respx.get(f"{BASE}/species/nope").mock(return_value=httpx.Response(
        404, json={"error": True, "code": "not_found", "message": "Couldn't find Species", "messages": "x"}))
    with pytest.raises(TrefleError) as e:
        client.get_species("nope")
    assert e.value.status == 404 and e.value.code == "not_found"


@respx.mock
def test_distribution_establishment_filter(client):
    route = respx.get(f"{BASE}/distributions/fra/species").mock(
        return_value=httpx.Response(200, json=page_body([SPECIES])))
    client.species_in_distribution("FRA", establishment="native")
    assert route.calls.last.request.url.params["filter[establishment]"] == "native"
    with pytest.raises(TrefleError):
        client.species_in_distribution("fra", establishment="weird")


def test_mcp_lists_four_tools():
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == {"search_species", "get_species", "list_plants", "species_in_distribution"}


def _call(name, args):
    result = asyncio.run(server.mcp.call_tool(name, args))
    # FastMCP returns (content, structured) in mcp>=1.10; fall back to content list otherwise.
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@respx.mock
def test_mcp_tool_call_end_to_end(wired):
    respx.get(f"{BASE}/species/search").mock(return_value=httpx.Response(200, json=page_body([SPECIES])))
    out = _call("search_species", {"q": "coconut", "limit": 5})
    assert out["results"][0]["slug"] == "cocos-nucifera"


def test_stdio_server_handshake_lists_tools():
    """Spawn the real stdio server as a subprocess and talk MCP to it (no network)."""
    import os
    import sys
    from pathlib import Path

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "TREFLE_TOKEN": FAKE_TOKEN, "TREFLE_BASE_URL": BASE,
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(root)}
    params = StdioServerParameters(command=sys.executable, args=["-B", "-m", "trefle_mcp.server"],
                                   env=env, cwd=str(root))

    async def go():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return [t.name for t in (await session.list_tools()).tools]

    assert sorted(asyncio.run(go())) == ["get_species", "list_plants", "search_species",
                                         "species_in_distribution"]


@respx.mock
def test_mcp_tool_returns_structured_error(wired):
    respx.get(f"{BASE}/species/search").mock(return_value=httpx.Response(
        401, json={"error": True, "code": "unauthorized", "message": "Invalid access token"}))
    out = _call("search_species", {"q": "oak"})
    assert out == {"error": True, "message": "Invalid access token", "status": 401, "code": "unauthorized"}
