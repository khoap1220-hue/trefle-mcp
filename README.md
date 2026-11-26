# trefle-mcp (proof of concept)

A small, read-only [Model Context Protocol](https://modelcontextprotocol.io) server for the public
[Trefle](https://trefle.io) botanical REST API, built as a concrete answer to
[treflehq/trefle-api#225](https://github.com/treflehq/trefle-api/issues/225) ("Official MCP server exposing
Trefle search and species data").

It is a clean-room implementation: written from the public API contract (routes, query keys, error
envelope) and not derived from Trefle's source code. Not affiliated with or endorsed by Trefle.

## Tools

| Tool | Trefle endpoint | Notes |
| --- | --- | --- |
| `search_species(q, filters?, ranges?, page, limit)` | `GET /api/v1/species/search` | full-text search, optional `filter[...]` / `range[...]` |
| `get_species(id_or_slug)` | `GET /api/v1/species/{id}` | full species card |
| `list_plants(filters?, ranges?, page, limit)` | `GET /api/v1/plants` | "plants by growing conditions" |
| `species_in_distribution(zone, establishment?, page, limit)` | `GET /api/v1/distributions/{zone}/species` | `native` / `introduced` filter |

These are the four endpoints proposed in the issue (search, species show, plants list with filters,
distributions).

## Design decisions (the open questions in #225)

- **Transport:** stdio. Each user brings their own token, so there is no hosted state or shared quota.
  A Streamable HTTP mode is a small follow-up if Trefle wants a hosted endpoint.
- **Auth:** `TREFLE_TOKEN` env var only. Sent as `Authorization: Bearer`, never as `?token=`, so it does
  not leak into URLs, logs or tool output. The server fails fast with a clear message if it is missing.
- **Context budget:** list results are trimmed to id/slug/names/family/genus/year/image and `limit` is capped
  at 50 per call; `has_next_page` and `total` tell the agent whether to paginate.
- **Validation:** unknown `filter`/`range` keys are rejected locally (same allow-list as the API) before
  any request, so the agent gets an actionable error without spending a request.
- **Errors:** Trefle's `{error, code, message, details}` envelope is returned to the agent as structured JSON
  instead of a stack trace.
- **Repo placement:** works as a standalone package (`treflehq/trefle-mcp`) or as a folder in the main repo.

## Run

```bash
pip install -e ".[test]"
export TREFLE_TOKEN=...            # from your trefle.io account
python -m trefle_mcp.server        # stdio
```

Claude Desktop / Cursor config:

```json
{ "mcpServers": { "trefle": { "command": "python", "args": ["-m", "trefle_mcp.server"],
  "env": { "TREFLE_TOKEN": "<your token>" } } } }
```

## Tests

Fully offline: HTTP is mocked with `respx`, and one test spawns the real stdio server and runs the MCP
handshake + `tools/list` against it. No token or network needed.

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -q
```

## Not done yet (production scope)

Retry with `Retry-After` on 429/5xx, `lang` parameter for localized common names, `species/{id}/facts`
provenance tool, Streamable HTTP transport, packaging + CI publish, MCP Registry `server.json`.

## Need a Custom MCP Server for Your API?

Built by **Khoa Phan** ([khoaphan.dev/services](https://khoaphan.dev/services/) • [0%-Fee Escrow on Contra](https://contra.com/s/Ll3krLoU-custom-mcp-server-for-claude-desktop-cursor-and-ai-agents)).

License: MIT
