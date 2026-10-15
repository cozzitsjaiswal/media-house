"""Bounded, geography-independent Overpass configuration and offline plans."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .errors import DiscoveryError

DEFAULT_ENDPOINT = "https://overpass-api.de/api/interpreter"
MAX_RESULTS = 200
MAX_FILTERS = 5
MAX_BBOX_SPAN = 0.5
MAX_BBOX_AREA_KM2 = 500
MAX_CACHE_TTL = 7 * 24 * 60 * 60
SOURCE_NAME = "OpenStreetMap via Overpass"
ATTRIBUTION = "© OpenStreetMap contributors"
ATTRIBUTION_URL = "https://www.openstreetmap.org/copyright"
LICENSE = "ODbL-1.0"
LICENSE_URL = "https://opendatacommons.org/licenses/odbl/1-0/"


def invalid_config(message: str) -> DiscoveryError:
    return DiscoveryError(message, code="invalid_config")


def bounded_integer(value: int, minimum: int, maximum: int, field: str) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise invalid_config(f"{field} must be an integer from {minimum} to {maximum}")


def source_metadata() -> dict[str, str]:
    return {
        "source_name": SOURCE_NAME,
        "attribution": ATTRIBUTION,
        "attribution_url": ATTRIBUTION_URL,
        "license": LICENSE,
        "license_url": LICENSE_URL,
    }


@dataclass(frozen=True)
class BoundingBox:
    """South, west, north, east; no antimeridian crossing or unbounded areas."""

    south: float
    west: float
    north: float
    east: float

    def __post_init__(self) -> None:
        values = (self.south, self.west, self.north, self.east)
        if any(type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)) for value in values):
            raise invalid_config("bbox coordinates must be finite numbers")
        if not (-90 <= self.south < self.north <= 90 and -180 <= self.west < self.east <= 180):
            raise invalid_config("bbox must be south,west,north,east within latitude/longitude ranges")
        if self.north - self.south > MAX_BBOX_SPAN or self.east - self.west > MAX_BBOX_SPAN:
            raise invalid_config(f"bbox latitude and longitude spans must each be at most {MAX_BBOX_SPAN} degrees")
        if self.area_km2 > MAX_BBOX_AREA_KM2:
            raise invalid_config(f"bbox must cover at most {MAX_BBOX_AREA_KM2} square kilometres")

    @property
    def area_km2(self) -> float:
        return (
            6371.0088 ** 2
            * math.radians(self.east - self.west)
            * (math.sin(math.radians(self.north)) - math.sin(math.radians(self.south)))
        )

    def as_list(self) -> list[float]:
        return [float(self.south), float(self.west), float(self.north), float(self.east)]

    @classmethod
    def parse(cls, text: str) -> "BoundingBox":
        try:
            values = [float(part.strip()) for part in text.split(",")]
            if len(values) != 4:
                raise ValueError
            return cls(*values)
        except (TypeError, ValueError) as exc:
            raise invalid_config("bbox must contain four comma-separated numbers: south,west,north,east") from exc


@dataclass(frozen=True, order=True)
class CategoryFilter:
    key: str
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_:.-]{0,63}", self.key):
            raise invalid_config("filter keys must be simple OSM tag keys, at most 64 characters")
        if (
            not isinstance(self.value, str)
            or not self.value.strip()
            or len(self.value) > 100
            or any(ord(character) < 32 or ord(character) == 127 for character in self.value)
        ):
            raise invalid_config("filter values must be nonempty, at most 100 characters, without control characters")
        try:
            self.value.encode("utf-8")
        except UnicodeError as exc:
            raise invalid_config("filter values must contain valid Unicode text") from exc

    @classmethod
    def parse(cls, text: str) -> "CategoryFilter":
        key, separator, value = text.partition("=")
        if not separator:
            raise invalid_config("filters must use key=value, for example amenity=restaurant or shop=*")
        return cls(key.strip(), value.strip())

    def __str__(self) -> str:
        return f"{self.key}={self.value}"

    def as_ql(self) -> str:
        # JSON string quoting also safely escapes QL quotes and backslashes.
        key = json.dumps(self.key, ensure_ascii=False)
        if self.value == "*":
            return f"[{key}]"
        return f"[{key}={json.dumps(self.value, ensure_ascii=False)}]"


@dataclass(frozen=True)
class DiscoveryConfig:
    bbox: BoundingBox
    geography: str
    filters: tuple[CategoryFilter, ...]
    max_results: int = 25
    endpoint: str = DEFAULT_ENDPOINT
    timeout: int = 25
    cache_dir: Path = Path(".media_house/discovery-cache")
    cache_ttl: int = 86400

    def __post_init__(self) -> None:
        if not isinstance(self.bbox, BoundingBox):
            raise invalid_config("bbox must be a BoundingBox")
        if (
            not isinstance(self.geography, str)
            or not self.geography.strip()
            or len(self.geography) > 200
            or any(ord(char) < 32 or ord(char) == 127 for char in self.geography)
        ):
            raise invalid_config("a geography label of 1–200 characters is required")
        try:
            self.geography.encode("utf-8")
        except UnicodeError as exc:
            raise invalid_config("geography must contain valid Unicode text") from exc
        object.__setattr__(self, "geography", self.geography.strip())
        if not isinstance(self.filters, (tuple, list)) or not 1 <= len(self.filters) <= MAX_FILTERS:
            raise invalid_config(f"supply between 1 and {MAX_FILTERS} category filters")
        if any(not isinstance(item, CategoryFilter) for item in self.filters):
            raise invalid_config("filters must be CategoryFilter values")
        if len(set(self.filters)) != len(self.filters):
            raise invalid_config("category filters must not be repeated identically")
        object.__setattr__(self, "filters", tuple(sorted(self.filters)))
        bounded_integer(self.max_results, 1, MAX_RESULTS, "max_results")
        bounded_integer(self.timeout, 1, 60, "timeout")
        bounded_integer(self.cache_ttl, 0, MAX_CACHE_TTL, "cache_ttl")
        try:
            if not isinstance(self.endpoint, str) or len(self.endpoint) > 2048:
                raise ValueError
            self.endpoint.encode("ascii")  # Non-ASCII URL components must be percent/IDNA encoded.
            endpoint = urlsplit(self.endpoint)
            if (
                endpoint.scheme not in {"http", "https"}
                or not endpoint.hostname
                or endpoint.username is not None
                or endpoint.password is not None
                or endpoint.fragment
                or any(character.isspace() or ord(character) < 32 for character in self.endpoint)
            ):
                raise ValueError
            endpoint.port  # Validate the port without accessing the network.
        except ValueError as exc:
            raise invalid_config("endpoint must be an absolute HTTP(S) URL without credentials or fragment") from exc
        try:
            object.__setattr__(self, "cache_dir", Path(self.cache_dir).expanduser())
        except TypeError as exc:
            raise invalid_config("cache_dir must be a filesystem path") from exc


@dataclass(frozen=True)
class DiscoveryPlan:
    config: DiscoveryConfig
    query: str
    cache_key: str

    def as_dict(self) -> dict[str, object]:
        return {
            "endpoint": self.config.endpoint,
            "geography": self.config.geography,
            "bbox": self.config.bbox.as_list(),
            "filters": [str(item) for item in self.config.filters],
            "filter_combination": "OR",
            "max_results": self.config.max_results,
            "timeout": self.config.timeout,
            "query": self.query,
            "cache_key": self.cache_key,
            **source_metadata(),
        }


def build_plan(config: DiscoveryConfig) -> DiscoveryPlan:
    bbox = ",".join(format(number, ".15g") for number in config.bbox.as_list())
    clauses = "\n".join(f"  nwr{item.as_ql()}({bbox});" for item in config.filters)
    query = (
        f"[out:json][timeout:{config.timeout}][maxsize:33554432];\n"
        f"(\n{clauses}\n);\nout body center {config.max_results};"
    )
    key_input = json.dumps(
        {"schema_version": 1, "endpoint": config.endpoint, "geography": config.geography,
         "bbox": config.bbox.as_list(), "query": query},
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    return DiscoveryPlan(config, query, hashlib.sha256(key_input).hexdigest())
