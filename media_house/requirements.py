"""Validation and hashing for the client-requirements handoff contract.

The canonical field names and enums mirror the retained
``client_requirements.schema.json`` source-of-truth.  This module deliberately
implements only the validation the local store needs; it does not add a JSON
Schema runtime dependency or promote any source statement into approval.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping

from .errors import ValidationError
from .normalization import canonical_text
from .timeutil import parse_utc_timestamp

DOCUMENT_STATUSES = frozenset(
    {"DRAFT", "NEEDS_CLARIFICATION", "READY_FOR_REVIEW", "APPROVED", "SUPERSEDED"}
)
CAPTURE_STATUSES = frozenset({"DRAFT", "NEEDS_CLARIFICATION", "READY_FOR_REVIEW"})
REQUIREMENT_STATUSES = frozenset(
    {"CLIENT_STATED", "OBSERVED", "PROPOSED", "ASSUMPTION", "OPEN_QUESTION", "APPROVED", "OUT_OF_SCOPE"}
)
REQUIREMENT_PRIORITIES = frozenset({"MUST", "SHOULD", "COULD", "WONT"})
HOSTING_MODES = frozenset({"CLIENT_HOSTED", "CLIENT_LOCAL", "MEDIA_HOUSE_MANAGED", "HYBRID", "UNDECIDED"})
LANGUAGES = frozenset({"hi", "mr", "en"})
_REQUIREMENT_ID = re.compile(r"^REQ-[0-9]{3,}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def canonical_json(value: object) -> str:
    """Serialize a validated JSON value deterministically for storage/export."""

    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValidationError(f"requirements document is not JSON serializable: {exc}") from exc


def content_hash(document: Mapping[str, object]) -> str:
    """Hash scope/version content while excluding mutable lifecycle metadata."""

    payload = copy.deepcopy(dict(document))
    payload.pop("approval", None)
    payload.pop("status", None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def normalize_approval_timestamp(value: object) -> str:
    """Normalize an approval datetime and reject date-only approval metadata."""

    if not isinstance(value, str):
        raise ValidationError("approval.approved_at must be an ISO datetime string")
    text = canonical_text(value)
    if len(text) == 10:
        raise ValidationError("approval.approved_at must be an ISO datetime, not a date-only value")
    try:
        return parse_utc_timestamp(text, field_name="approval.approved_at")
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValidationError(str(exc)) from exc


def _require_mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValidationError(f"{field} must be an object")
    return value


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not canonical_text(value):
        raise ValidationError(f"{field} must be a non-empty string")
    return value


def _optional_string(value: object, field: str) -> None:
    if value is not None and not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")


def _string_list(value: object, field: str, *, required: bool = False) -> None:
    if value is None and not required:
        return
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValidationError(f"{field} must be a list of strings")


def _number_or_null(value: object, field: str) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
        raise ValidationError(f"{field} must be a number or null")


def _validate_client(value: object) -> None:
    client = _require_mapping(value, "client")
    _required_string(client.get("business_name"), "client.business_name")
    _required_string(client.get("industry"), "client.industry")
    for field in ("region", "decision_maker_role"):
        _optional_string(client.get(field), f"client.{field}")
    languages = client.get("preferred_languages")
    if languages is not None:
        if not isinstance(languages, list) or any(item not in LANGUAGES for item in languages):
            raise ValidationError("client.preferred_languages must contain only hi, mr, or en")


def _validate_business_problem(value: object) -> None:
    problem = _require_mapping(value, "business_problem")
    _required_string(problem.get("client_statement"), "business_problem.client_statement")
    _string_list(problem.get("current_workflow"), "business_problem.current_workflow", required=True)
    _string_list(problem.get("impact_evidence"), "business_problem.impact_evidence")
    _string_list(problem.get("existing_systems"), "business_problem.existing_systems")


def _validate_scope(value: object) -> None:
    scope = _require_mapping(value, "scope")
    _string_list(scope.get("included"), "scope.included", required=True)
    _string_list(scope.get("excluded"), "scope.excluded", required=True)
    _string_list(scope.get("dependencies"), "scope.dependencies")
    _optional_string(scope.get("change_control"), "scope.change_control")


def _validate_data_handling(value: object) -> None:
    data = _require_mapping(value, "data_handling")
    if data.get("hosting_mode") not in HOSTING_MODES:
        raise ValidationError("data_handling.hosting_mode has an unsupported value")
    _string_list(data.get("data_categories"), "data_handling.data_categories", required=True)
    _string_list(data.get("external_processors"), "data_handling.external_processors", required=True)
    _required_string(data.get("retention"), "data_handling.retention")
    _required_string(data.get("export_and_deletion"), "data_handling.export_and_deletion")
    if "health_data_in_scope" in data and not isinstance(data["health_data_in_scope"], bool):
        raise ValidationError("data_handling.health_data_in_scope must be a boolean")


def _validate_requirements(value: object) -> None:
    if not isinstance(value, list):
        raise ValidationError("requirements must be a list")
    seen: set[str] = set()
    for index, raw in enumerate(value, start=1):
        item = _require_mapping(raw, f"requirements[{index}]")
        identifier = _required_string(item.get("id"), f"requirements[{index}].id")
        if not _REQUIREMENT_ID.fullmatch(identifier):
            raise ValidationError(f"requirements[{index}].id must match REQ-001 format")
        if identifier in seen:
            raise ValidationError(f"duplicate requirement id {identifier}")
        seen.add(identifier)
        status = item.get("status")
        if status not in REQUIREMENT_STATUSES:
            raise ValidationError(f"requirements[{index}].status has an unsupported value")
        if item.get("priority") not in REQUIREMENT_PRIORITIES:
            raise ValidationError(f"requirements[{index}].priority has an unsupported value")
        _required_string(item.get("description"), f"requirements[{index}].description")
        _required_string(item.get("acceptance_test"), f"requirements[{index}].acceptance_test")
        _optional_string(item.get("notes"), f"requirements[{index}].notes")


def _validate_acceptance_criteria(value: object) -> None:
    if not isinstance(value, list):
        raise ValidationError("acceptance_criteria must be a list")
    seen: set[str] = set()
    for index, raw in enumerate(value, start=1):
        item = _require_mapping(raw, f"acceptance_criteria[{index}]")
        identifier = _required_string(item.get("id"), f"acceptance_criteria[{index}].id")
        if identifier in seen:
            raise ValidationError(f"duplicate acceptance criteria id {identifier}")
        seen.add(identifier)
        _string_list(item.get("steps"), f"acceptance_criteria[{index}].steps", required=True)
        _required_string(item.get("expected_result"), f"acceptance_criteria[{index}].expected_result")
        _optional_string(item.get("evidence"), f"acceptance_criteria[{index}].evidence")


def _validate_optional_sections(document: Mapping[str, object]) -> None:
    goals = document.get("goals")
    if goals is not None:
        if not isinstance(goals, list):
            raise ValidationError("goals must be a list")
        for index, raw in enumerate(goals, start=1):
            item = _require_mapping(raw, f"goals[{index}]")
            for field in ("metric", "measurement_method"):
                _optional_string(item.get(field), f"goals[{index}].{field}")
            for field in ("baseline", "target"):
                _number_or_null(item.get(field), f"goals[{index}].{field}")

    integrations = document.get("integrations")
    if integrations is not None:
        if not isinstance(integrations, list):
            raise ValidationError("integrations must be a list")
        for index, raw in enumerate(integrations, start=1):
            item = _require_mapping(raw, f"integrations[{index}]")
            for field in ("provider", "purpose", "failure_fallback"):
                _optional_string(item.get(field), f"integrations[{index}].{field}")
            if "access_confirmed" in item and not isinstance(item["access_confirmed"], bool):
                raise ValidationError(f"integrations[{index}].access_confirmed must be a boolean")

    ai_constraints = document.get("ai_constraints")
    if ai_constraints is not None:
        constraints = _require_mapping(ai_constraints, "ai_constraints")
        _string_list(constraints.get("allowed_actions"), "ai_constraints.allowed_actions")
        _string_list(constraints.get("human_approval_actions"), "ai_constraints.human_approval_actions")
        for field in ("human_handoff", "ai_disclosure"):
            _optional_string(constraints.get(field), f"ai_constraints.{field}")

    commercial = document.get("commercial")
    if commercial is not None:
        values = _require_mapping(commercial, "commercial")
        _optional_string(values.get("currency"), "commercial.currency")
        _number_or_null(values.get("setup_fee"), "commercial.setup_fee")
        _number_or_null(values.get("recurring_fee"), "commercial.recurring_fee")
        _string_list(values.get("payment_milestones"), "commercial.payment_milestones")
        _optional_string(values.get("support_scope"), "commercial.support_scope")

    _string_list(document.get("open_questions_and_risks"), "open_questions_and_risks")


def _validate_approval(value: object, *, required: bool) -> None:
    if value is None:
        if required:
            raise ValidationError("approved requirements require approval metadata")
        return
    approval = _require_mapping(value, "approval")
    _required_string(approval.get("approved_by"), "approval.approved_by")
    normalize_approval_timestamp(approval.get("approved_at"))
    digest = approval.get("scope_version_hash")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ValidationError("approval.scope_version_hash must be a lowercase SHA-256 digest")


def validate_document(
    document: Mapping[str, object],
    *,
    expected_meeting_id: int | None = None,
    expected_prospect_id: int | None = None,
    require_ready_content: bool = False,
    allow_approved: bool = True,
) -> dict[str, object]:
    """Validate a schema-compatible requirements document and return a copy."""

    if not isinstance(document, Mapping):
        raise ValidationError("requirements document must be an object")
    required = {
        "client",
        "document_version",
        "status",
        "business_problem",
        "requirements",
        "scope",
        "data_handling",
        "acceptance_criteria",
    }
    missing = sorted(required - set(document))
    if missing:
        raise ValidationError("requirements document is missing: " + ", ".join(missing))

    version = document.get("document_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValidationError("document_version must be an integer greater than zero")
    status = document.get("status")
    if status not in DOCUMENT_STATUSES:
        raise ValidationError("status has an unsupported value")
    if not allow_approved and status not in CAPTURE_STATUSES:
        raise ValidationError("capture status must be DRAFT, NEEDS_CLARIFICATION, or READY_FOR_REVIEW")

    for field, expected in (("meeting_id", expected_meeting_id), ("prospect_id", expected_prospect_id)):
        if expected is not None and document.get(field) != expected:
            raise ValidationError(f"{field} does not match the linked record")
        if field in document and (isinstance(document[field], bool) or not isinstance(document[field], int)):
            raise ValidationError(f"{field} must be an integer")

    _validate_client(document["client"])
    _validate_business_problem(document["business_problem"])
    _validate_requirements(document["requirements"])
    _validate_scope(document["scope"])
    _validate_data_handling(document["data_handling"])
    _validate_acceptance_criteria(document["acceptance_criteria"])
    _validate_optional_sections(document)

    approval = document.get("approval")
    if status in {"APPROVED", "SUPERSEDED"}:
        if not allow_approved:
            raise ValidationError("capture cannot mark a document approved or superseded")
        _validate_approval(approval, required=True)
    elif approval is not None:
        raise ValidationError("draft requirements must not contain approval metadata")

    if require_ready_content and (not document["requirements"] or not document["acceptance_criteria"]):
        raise ValidationError("READY_FOR_REVIEW requires at least one requirement and acceptance criterion")
    return copy.deepcopy(dict(document))


def approve_document(document: Mapping[str, object], *, actor: str, approved_at: str, scope_hash: str) -> dict[str, object]:
    """Return an explicitly approved copy without promoting requirement labels."""

    if document.get("status") != "READY_FOR_REVIEW":
        raise ValidationError("only READY_FOR_REVIEW requirements can be approved")
    requirements = document.get("requirements")
    if not isinstance(requirements, list) or any(
        not isinstance(item, dict) or item.get("status") not in {"APPROVED", "OUT_OF_SCOPE"}
        for item in requirements
    ):
        raise ValidationError("every requirement must be explicitly APPROVED or OUT_OF_SCOPE before baseline approval")
    result = copy.deepcopy(dict(document))
    result["status"] = "APPROVED"
    result["approval"] = {
        "approved_by": actor,
        "approved_at": approved_at,
        "scope_version_hash": scope_hash,
    }
    return validate_document(result, require_ready_content=True, allow_approved=True)
