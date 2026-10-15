"""Standard-library Overpass adapter, independent of the lead store and CLI."""

from __future__ import annotations

import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..normalization import canonical_text, is_unknown
from ..timeutil import parse_utc_timestamp
from .config import BoundingBox, CategoryFilter, DiscoveryConfig, DiscoveryPlan, build_plan, source_metadata
from .errors import DiscoveryError
from .files import MAX_JSON_BYTES, atomic_write_json, decode_json, read_json

USER_AGENT = "MediaHouse-LeadDiscovery/1.0 (bounded, operator-triggered OSM research; Python urllib)"
PUBLIC_WEBSITE_TAGS = ("website", "contact:website")
PUBLIC_CONTACT_TAGS = frozenset({
    "phone", "contact:phone", "mobile", "contact:mobile", "email", "contact:email",
    "fax", "contact:fax", "contact:facebook", "contact:instagram", "contact:twitter",
    "contact:linkedin", "contact:whatsapp",
})
SKIP_REASONS = frozenset({
    "malformed_element", "missing_name", "missing_location", "category_mismatch",
    "duplicate_source_object", "result_cap", "outside_bbox",
})
INVALID_REASONS = frozenset({"malformed_element", "missing_name", "missing_location"})
CACHE_STATUSES = frozenset({"network", "hit", "not_cached_malformed"})
SOURCE_ID_PATTERN = re.compile(r"(node|way|relation)/([1-9][0-9]{0,18})\Z")


@dataclass(frozen=True)
class DiscoveryRecord:
    source_object_id: str
    source_url: str
    name: str
    geography: str
    category_tags: dict[str, str]
    matched_filters: tuple[str, ...]
    latitude: float
    longitude: float
    location_kind: str
    website: str | None
    public_websites: dict[str, str]
    public_contacts: dict[str, str]
    retrieved_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "matched_filters": list(self.matched_filters),
            **source_metadata(),
            "name_missing": False,
            "evidence_label": "source_returned_unverified",
            "discovered": True,
            "source_returned": True,
            "verified": False,
            "qualified": False,
            "customer_pain_confirmed": False,
            "customer_need": "unknown",
            "automation_status": "unknown",
        }


@dataclass(frozen=True)
class DiscoveryResult:
    plan: DiscoveryPlan
    retrieved_at: str
    records: tuple[DiscoveryRecord, ...]
    returned_elements: int
    skipped_reasons: dict[str, int]
    cache_status: str = "network"

    @property
    def counts(self) -> dict[str, int]:
        return {
            "source_returned": self.returned_elements,
            "discovered": len(self.records),
            "invalid": sum(count for reason, count in self.skipped_reasons.items() if reason in INVALID_REASONS),
            "skipped": sum(self.skipped_reasons.values()),
        }

    @property
    def cacheable(self) -> bool:
        return not any(self.skipped_reasons.get(reason) for reason in ("malformed_element", "missing_location"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": "media-house-discovery",
            "schema_version": 1,
            "plan": self.plan.as_dict(),
            "retrieved_at": self.retrieved_at,
            "records": [record.as_dict() for record in self.records],
            "counts": self.counts,
            "skipped_reasons": self.skipped_reasons,
            "cache_status": self.cache_status,
        }


def _valid_tags(tags: object) -> bool:
    if not isinstance(tags, dict) or len(tags) > 256:
        return False
    for key, value in tags.items():
        if not isinstance(key, str) or not isinstance(value, str) or not 1 <= len(key) <= 255 or len(value) > 4096:
            return False
        try:
            key.encode("utf-8")
            value.encode("utf-8")
        except UnicodeError:
            return False
    return True


def _coordinates(location: object) -> tuple[float, float] | None:
    if not isinstance(location, dict):
        return None
    latitude, longitude = location.get("lat"), location.get("lon")
    if (
        type(latitude) not in (float, int) or type(longitude) not in (float, int)
        or not -90 <= latitude <= 90 or not -180 <= longitude <= 180
    ):
        return None
    return float(latitude), float(longitude)


def _parse_element(
    element: object, plan: DiscoveryPlan, retrieved_at: str,
) -> tuple[DiscoveryRecord | None, str | None]:
    if not isinstance(element, dict):
        return None, "malformed_element"
    kind, object_id = element.get("type"), element.get("id")
    if kind not in ("node", "way", "relation") or type(object_id) is not int or not 1 <= object_id < 10 ** 19:
        return None, "malformed_element"
    tags = element.get("tags", {})
    if not _valid_tags(tags):
        return None, "malformed_element"
    name = canonical_text(tags.get("name"))
    if is_unknown(name):
        return None, "missing_name"
    if len(name) > 512:
        return None, "malformed_element"
    matched = tuple(item for item in plan.config.filters if canonical_text(tags.get(item.key)) and (
        item.value == "*" or tags[item.key] == item.value
    ))
    if not matched:
        return None, "category_mismatch"
    location = element if kind == "node" else element.get("center")
    if location is None or (isinstance(location, dict) and ("lat" not in location or "lon" not in location)):
        return None, "missing_location"
    coordinates = _coordinates(location)
    if coordinates is None:
        return None, "malformed_element"
    bbox = plan.config.bbox
    if kind == "node" and not (bbox.south <= coordinates[0] <= bbox.north and bbox.west <= coordinates[1] <= bbox.east):
        return None, "outside_bbox"
    # A way/relation can intersect the bbox while its center lies outside it.
    websites = {key: canonical_text(tags[key]) for key in PUBLIC_WEBSITE_TAGS if key in tags and not is_unknown(tags[key])}
    contacts = {key: canonical_text(tags[key]) for key in sorted(PUBLIC_CONTACT_TAGS) if key in tags and not is_unknown(tags[key])}
    source_object_id = f"{kind}/{object_id}"
    return DiscoveryRecord(
        source_object_id=source_object_id,
        source_url=f"https://www.openstreetmap.org/{source_object_id}",
        name=name,
        geography=plan.config.geography,
        category_tags={item.key: tags[item.key] for item in matched},
        matched_filters=tuple(str(item) for item in matched),
        latitude=coordinates[0],
        longitude=coordinates[1],
        location_kind="node" if kind == "node" else "center",
        website=next(iter(websites.values()), None),
        public_websites=websites,
        public_contacts=contacts,
        retrieved_at=retrieved_at,
    ), None


def parse_overpass(payload: object, plan: DiscoveryPlan, *, retrieved_at: str) -> DiscoveryResult:
    """Reject invalid envelopes/service remarks; count and skip invalid individual elements."""
    try:
        retrieved_at = parse_utc_timestamp(retrieved_at, field_name="retrieved_at")
    except (ValueError, TypeError, AttributeError) as exc:
        raise DiscoveryError("retrieved_at must be an ISO timestamp", code="invalid_timestamp") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise DiscoveryError("Overpass JSON must contain an elements array", code="malformed_response")
    if payload.get("remark") or payload.get("error"):
        # Even HTTP 200 may contain a timeout/partial-result error. Do not accept or cache it.
        raise DiscoveryError(
            "Overpass reported an error or partial result; reduce the bbox/cap or retry manually later",
            code="service_unavailable",
        )
    elements = payload["elements"]
    skipped: Counter[str] = Counter()
    records: list[DiscoveryRecord] = []
    seen: set[str] = set()
    for element in elements[:plan.config.max_results]:
        record, reason = _parse_element(element, plan, retrieved_at)
        if reason:
            skipped[reason] += 1
        elif record is not None:
            if record.source_object_id in seen:
                skipped["duplicate_source_object"] += 1
            else:
                records.append(record)
                seen.add(record.source_object_id)
    if len(elements) > plan.config.max_results:
        skipped["result_cap"] = len(elements) - plan.config.max_results
    result = DiscoveryResult(plan, retrieved_at, tuple(records), len(elements), dict(sorted(skipped.items())))
    if not result.cacheable:
        result = replace(result, cache_status="not_cached_malformed")
    return result


def _same_json(left: object, right: object) -> bool:
    # Unlike Python equality, this distinguishes booleans from integer flags.
    return json.dumps(left, sort_keys=True, ensure_ascii=False) == json.dumps(right, sort_keys=True, ensure_ascii=False)


def result_from_dict(value: object) -> DiscoveryResult:
    """Validate saved results offline, including their source fields and unverified flags.

    Re-parse each record's source fields with the same adapter parser. Saved
    flags, counts and metadata cannot substitute for the generated safe values.
    This checks consistency, not authenticity of an operator's local file.
    """
    try:
        if not isinstance(value, dict) or value.get("format") != "media-house-discovery" or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
            raise ValueError("unsupported discovery file format/version")
        raw_plan = value["plan"]
        if not isinstance(raw_plan, dict) or not isinstance(raw_plan["bbox"], list) or len(raw_plan["bbox"]) != 4:
            raise ValueError("invalid saved plan")
        if not isinstance(raw_plan["filters"], list):
            raise ValueError("invalid saved filters")
        config = DiscoveryConfig(
            bbox=BoundingBox(*raw_plan["bbox"]), geography=raw_plan["geography"],
            filters=tuple(CategoryFilter.parse(item) for item in raw_plan["filters"]),
            max_results=raw_plan["max_results"], endpoint=raw_plan["endpoint"], timeout=raw_plan["timeout"],
        )
        plan = build_plan(config)
        if not _same_json(plan.as_dict(), raw_plan):
            raise ValueError("saved plan/query/cache key is inconsistent")
        retrieved_at = parse_utc_timestamp(value["retrieved_at"], field_name="retrieved_at")
        raw_records = value["records"]
        if not isinstance(raw_records, list) or len(raw_records) > config.max_results:
            raise ValueError("saved record count exceeds the request cap")
        records: list[DiscoveryRecord] = []
        seen: set[str] = set()
        for raw in raw_records:
            if not isinstance(raw, dict) or not isinstance(raw.get("source_object_id"), str):
                raise ValueError("invalid saved record")
            match = SOURCE_ID_PATTERN.fullmatch(raw["source_object_id"])
            if not match or raw["source_object_id"] in seen:
                raise ValueError("invalid or duplicate source object id")
            for field, allowed in (
                ("category_tags", {item.key for item in config.filters}),
                ("public_websites", set(PUBLIC_WEBSITE_TAGS)),
                ("public_contacts", PUBLIC_CONTACT_TAGS),
            ):
                if not _valid_tags(raw[field]) or not set(raw[field]) <= allowed:
                    raise ValueError(f"invalid {field}")
            element = {
                "type": match[1], "id": int(match[2]),
                "tags": {"name": raw["name"], **raw["category_tags"], **raw["public_websites"], **raw["public_contacts"]},
            }
            location = {"lat": raw["latitude"], "lon": raw["longitude"]}
            if match[1] == "node":
                element.update(location)
            else:
                element["center"] = location
            record, reason = _parse_element(element, plan, retrieved_at)
            if reason or record is None or not _same_json(record.as_dict(), raw):
                raise ValueError("saved record has invalid fields, metadata, or evidence flags")
            records.append(record)
            seen.add(record.source_object_id)
        skipped = value["skipped_reasons"]
        if not isinstance(skipped, dict) or any(
            reason not in SKIP_REASONS or type(count) is not int or count <= 0 for reason, count in skipped.items()
        ):
            raise ValueError("invalid skipped counts")
        returned = len(records) + sum(skipped.values())
        status = value["cache_status"]
        if status not in CACHE_STATUSES:
            raise ValueError("invalid cache status")
        result = DiscoveryResult(plan, retrieved_at, tuple(records), returned, skipped, status)
        if not _same_json(result.as_dict(), value):
            raise ValueError("saved result counts or metadata are inconsistent")
        return result
    except (KeyError, TypeError, ValueError, AttributeError, DiscoveryError) as exc:
        raise DiscoveryError(f"Invalid saved discovery result: {exc}", code="invalid_saved_result") from exc


def load_result(path: Path) -> DiscoveryResult:
    return result_from_dict(read_json(path))


def save_result(path: Path, result: DiscoveryResult) -> None:
    atomic_write_json(path, result.as_dict())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_request(request: urllib.request.Request, *, timeout: int):
    # A redirect is reported for operator review instead of silently issuing a second request.
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)


def _http_error(status: int, retry_after: str | None) -> DiscoveryError:
    retry_after = canonical_text(retry_after)[:200] or None
    if status == 429:
        guidance = "Rate limited; wait before another operator-triggered request"
        code = "rate_limited"
    elif status == 403:
        guidance = "Access forbidden; check endpoint usage policy before requesting again"
        code = "forbidden"
    elif 500 <= status <= 599:
        guidance = "Overpass is unavailable; try cache-only or retry manually later"
        code = "service_unavailable"
    else:
        guidance = "Request rejected; review the endpoint and query (redirects are not followed)"
        code = "http_error"
    if retry_after:
        guidance += f". Retry-After: {retry_after} (seconds or HTTP date)"
    return DiscoveryError(f"HTTP {status}: {guidance}. No automatic retry was attempted.", code=code, status=status, retry_after=retry_after)


def discover(
    config: DiscoveryConfig,
    *,
    confirm_network: bool = False,
    cache_only: bool = False,
    force_refresh: bool = False,
    opener: Callable[..., Any] | None = None,
    clock: Callable[[], float] | None = None,
) -> DiscoveryResult:
    """Read one fresh cache entry or issue exactly one explicitly permitted POST.

    There are no retries, pagination, secondary website fetches, or database calls.
    Cache-only requires an unexpired entry; corrupt caches fail closed until a
    separately confirmed force refresh. Retrieval times survive cache reuse.
    """
    if cache_only and force_refresh:
        raise DiscoveryError("--cache-only cannot be combined with --force-refresh", code="invalid_config")
    if not cache_only and not confirm_network:
        raise DiscoveryError("Network access requires --confirm-network; use --cache-only for offline reuse", code="network_confirmation_required")
    plan = build_plan(config)
    cache_path = config.cache_dir / f"{plan.cache_key}.json"
    now = (clock or time.time)()
    if not force_refresh and cache_path.exists():
        try:
            cached = load_result(cache_path)
            if cached.plan.cache_key != plan.cache_key or not cached.cacheable:
                raise DiscoveryError("Cache does not match this request or contains malformed records", code="cache_invalid")
            age = now - datetime.fromisoformat(cached.retrieved_at.replace("Z", "+00:00")).timestamp()
            if age < 0:
                raise DiscoveryError("Cache retrieval time is in the future; check the clock", code="cache_invalid")
        except DiscoveryError as exc:
            raise DiscoveryError(f"Cache is unusable: {exc}. Review/remove it or confirm a --force-refresh.", code="cache_invalid") from exc
        if age < config.cache_ttl:
            return replace(cached, plan=plan, cache_status="hit")
        if cache_only:
            raise DiscoveryError("Cache entry has expired; review the TTL or explicitly confirm a refresh", code="cache_expired")
    if cache_only:
        raise DiscoveryError("No fresh cache entry exists for this exact plan", code="cache_miss")

    request = urllib.request.Request(
        config.endpoint,
        data=urllib.parse.urlencode({"data": plan.query}).encode("ascii"),
        method="POST",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with (opener or _open_request)(request, timeout=config.timeout + 5) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise _http_error(status, response.headers.get("Retry-After"))
            body = response.read(MAX_JSON_BYTES + 1)
    except urllib.error.HTTPError as exc:
        failure = _http_error(exc.code, exc.headers.get("Retry-After") if exc.headers else None)
        exc.close()
        raise failure from exc
    except (TimeoutError, urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        raise DiscoveryError(
            f"Overpass network/timeout failure: {exc}. No automatic retry; use cache-only or retry manually later.",
            code="network_error",
        ) from exc
    retrieved_at = datetime.fromtimestamp((clock or time.time)(), timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    result = parse_overpass(decode_json(body), plan, retrieved_at=retrieved_at)
    if result.cacheable:
        save_result(cache_path, result)
    return result
