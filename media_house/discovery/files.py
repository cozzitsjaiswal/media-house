"""Bounded JSON reads and replace-on-success writes for results and cache."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .errors import DiscoveryError

MAX_JSON_BYTES = 8 * 1024 * 1024


def decode_json(data: bytes) -> Any:
    """Decode bounded JSON bytes, rejecting non-standard constants and duplicate keys."""

    if len(data) > MAX_JSON_BYTES:
        raise DiscoveryError("JSON exceeds the 8 MiB response/file limit; reduce the request", code="response_limit")

    def reject_constant(value: str) -> None:
        """Reject non-standard JSON constants such as NaN or Infinity."""

        raise ValueError(f"non-JSON constant {value}")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        """Build a JSON object hook that rejects duplicate keys."""

        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    try:
        return json.loads(data.decode("utf-8-sig"), parse_constant=reject_constant, object_pairs_hook=unique_object)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise DiscoveryError("Source or saved result is not valid UTF-8 JSON", code="malformed_json") from exc


def read_json(path: Path) -> Any:
    """Read and decode a bounded JSON file."""

    try:
        with path.open("rb") as handle:
            return decode_json(handle.read(MAX_JSON_BYTES + 1))
    except OSError as exc:
        raise DiscoveryError(f"Cannot read discovery JSON {path}: {exc}", code="file_read") from exc


def atomic_write_json(path: Path, value: Any) -> None:
    """Serialize a value to JSON and write it atomically via a staged temp file."""

    temporary: Path | None = None
    try:
        encoded = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
        if len(encoded) > MAX_JSON_BYTES:
            raise DiscoveryError("Saved discovery exceeds the 8 MiB file limit", code="response_limit")
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except (OSError, ValueError) as exc:
        raise DiscoveryError(f"Cannot save discovery JSON {path}: {exc}", code="file_write") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
