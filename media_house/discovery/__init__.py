"""Optional, explicitly invoked OSM discovery; no import-time network activity."""

from .config import BoundingBox, CategoryFilter, DiscoveryConfig, DiscoveryPlan, build_plan
from .errors import DiscoveryError

__all__ = [
    "BoundingBox", "CategoryFilter", "DiscoveryConfig", "DiscoveryError",
    "DiscoveryPlan", "build_plan",
]
