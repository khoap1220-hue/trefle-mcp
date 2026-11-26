"""Thin, typed HTTP client for the public Trefle API v1.

Written against the public API contract only (routes, query keys and the
error envelope documented in treflehq/trefle-api). No Trefle code is copied.

Security choices:
- The token is read from the TREFLE_TOKEN environment variable only.
- It is sent as an ``Authorization: Bearer`` header, never as ``?token=``,
  so it does not end up in URLs, proxy logs or tool output.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://trefle.io/api/v1"
MAX_PAGE_SIZE = 50  # local cap so one tool call cannot flood the model context

# Public query keys accepted by /species and /plants list + search endpoints.
FILTER_FIELDS = frozenset(
    """author biological_type bloom_months common_name complete_data completion_ratio
    duration establishment edible edible_part family_common_name family_name flower_color
    flower_conspicuous foliage_color foliage_texture fruit_color fruit_conspicuous
    fruit_months fruit_seed_persistence fruit_shape genus_name growth_form growth_habit
    growth_months growth_rate inflorescence_type leaf_retention ligneous_type
    nitrogen_fixation rank scientific_name sexuality status vegetable""".split()
)
RANGE_FIELDS = frozenset(
    """atmospheric_humidity average_height_cm completion_ratio frost_free_days_minimum
    ground_humidity images_count light maximum_height_cm maximum_precipitation_mm
    maximum_temperature_deg_c maximum_temperature_deg_f minimum_precipitation_mm
    minimum_root_depth_cm minimum_temperature_deg_c minimum_temperature_deg_f ph_maximum
    ph_minimum planting_days_to_harvest planting_row_spacing_cm planting_spread_cm
    soil_nutriments soil_salinity soil_texture sources_count synonyms_count toxicity year""".split()
)
ESTABLISHMENTS = frozenset({"native", "introduced"})

SUMMARY_KEYS = (
    "id", "slug", "scientific_name", "common_name", "family", "genus", "year", "rank",
    "status", "image_url",
)


class TrefleError(Exception):
    """Raised for configuration problems and for Trefle error envelopes."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None,
                 details: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"error": True, "message": str(self)}
        if self.status is not None:
            out["status"] = self.status
        if self.code:
            out["code"] = self.code
        if self.details is not None:
            out["details"] = self.details
        return out


@dataclass(frozen=True)
class Page:
    data: list[dict[str, Any]]
    total: int | None
    next_page: bool

    def as_dict(self) -> dict[str, Any]:
        return {"count": len(self.data), "total": self.total, "has_next_page": self.next_page,
                "results": self.data}


def summarize(record: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the fields an assistant needs; drop links and bulky nested data."""
    return {k: record.get(k) for k in SUMMARY_KEYS if record.get(k) is not None}


def _check_keys(kind: str, given: Mapping[str, Any] | None, allowed: frozenset[str]) -> None:
    if not given:
        return
    unknown = sorted(set(given) - allowed)
    if unknown:
        raise TrefleError(
            f"Unknown {kind} key(s): {', '.join(unknown)}. Valid {kind} keys: {', '.join(sorted(allowed))}",
            code="invalid_argument",
        )


def _page_size(limit: int) -> int:
    if limit < 1:
        raise TrefleError("limit must be >= 1", code="invalid_argument")
    return min(limit, MAX_PAGE_SIZE)


class TrefleClient:
    def __init__(self, token: str | None = None, base_url: str | None = None,
                 timeout: float = 15.0, transport: httpx.BaseTransport | None = None) -> None:
        token = token if token is not None else os.environ.get("TREFLE_TOKEN", "")
        if not token.strip():
            raise TrefleError(
                "TREFLE_TOKEN is not set. Create a token at https://trefle.io and export it.",
                code="missing_token",
            )
        base = (base_url or os.environ.get("TREFLE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._http = httpx.Client(
            base_url=base,
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token.strip()}",
                "Accept": "application/json",
                "User-Agent": "trefle-mcp-poc/0.1",
            },
        )

    def close(self) -> None:
        self._http.close()

    # -- low level -------------------------------------------------------
    def _get(self, path: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        try:
            resp = self._http.get(path, params={k: v for k, v in (params or {}).items() if v is not None})
        except httpx.HTTPError as exc:  # network, timeout
            raise TrefleError(f"Request to Trefle failed: {exc.__class__.__name__}", code="network") from exc
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code >= 400 or (isinstance(body, dict) and body.get("error") is True):
            env = body if isinstance(body, dict) else {}
            message = env.get("message") or env.get("messages") or f"HTTP {resp.status_code}"
            raise TrefleError(str(message), status=resp.status_code, code=env.get("code"),
                              details=env.get("details"))
        if not isinstance(body, dict):
            raise TrefleError("Trefle returned a non-JSON response", status=resp.status_code)
        return body

    @staticmethod
    def _query(filters: Mapping[str, Any] | None, ranges: Mapping[str, Any] | None,
               page: int, limit: int) -> dict[str, Any]:
        _check_keys("filter", filters, FILTER_FIELDS)
        _check_keys("range", ranges, RANGE_FIELDS)
        if page < 1:
            raise TrefleError("page must be >= 1", code="invalid_argument")
        params: dict[str, Any] = {"page": page, "limit": _page_size(limit)}
        for k, v in (filters or {}).items():
            params[f"filter[{k}]"] = ",".join(map(str, v)) if isinstance(v, (list, tuple)) else str(v)
        for k, v in (ranges or {}).items():
            params[f"range[{k}]"] = str(v)
        return params

    @staticmethod
    def _page(body: dict[str, Any]) -> Page:
        data = body.get("data") or []
        meta = body.get("meta") or {}
        links = body.get("links") or {}
        return Page(data=[summarize(r) for r in data], total=meta.get("total"),
                    next_page=bool(links.get("next")))

    # -- endpoints used by the MCP tools ---------------------------------
    def search_species(self, q: str, *, filters: Mapping[str, Any] | None = None,
                       ranges: Mapping[str, Any] | None = None, page: int = 1, limit: int = 10) -> Page:
        if not q or not q.strip():
            raise TrefleError("q must be a non-empty search string", code="invalid_argument")
        params = self._query(filters, ranges, page, limit)
        params["q"] = q.strip()
        return self._page(self._get("/species/search", params))

    def get_species(self, id_or_slug: str | int) -> dict[str, Any]:
        ref = str(id_or_slug).strip()
        if not ref or "/" in ref:
            raise TrefleError("id_or_slug must be a numeric id or a slug like 'quercus-rotundifolia'",
                              code="invalid_argument")
        body = self._get(f"/species/{ref}")
        return {"species": body.get("data"), "meta": body.get("meta")}

    def list_plants(self, *, filters: Mapping[str, Any] | None = None,
                    ranges: Mapping[str, Any] | None = None, page: int = 1, limit: int = 10) -> Page:
        return self._page(self._get("/plants", self._query(filters, ranges, page, limit)))

    def species_in_distribution(self, zone: str, *, establishment: str | None = None,
                                page: int = 1, limit: int = 10) -> Page:
        zone = zone.strip().lower()
        if not zone or "/" in zone:
            raise TrefleError("zone must be a distribution slug or TDWG code, e.g. 'fra'",
                              code="invalid_argument")
        filters: dict[str, Any] = {}
        if establishment:
            if establishment not in ESTABLISHMENTS:
                raise TrefleError("establishment must be 'native' or 'introduced'", code="invalid_argument")
            filters["establishment"] = establishment
        return self._page(self._get(f"/distributions/{zone}/species",
                                    self._query(filters, None, page, limit)))
