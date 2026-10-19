"""Command-line interface for the local Media House milestone."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from .errors import MediaHouseError, ValidationError
from .store import MediaHouseStore
from .discovery import BoundingBox, CategoryFilter, DiscoveryConfig, DiscoveryError, build_plan
from .discovery.config import DEFAULT_ENDPOINT
from .discovery.integration import import_discovery
from .discovery.overpass import discover, load_result, save_result
from .finder import FinderError, classify_saved_input, import_saved_input


DEFAULT_DATABASE = os.environ.get("MEDIA_HOUSE_DB", ".media_house/media_house.sqlite3")
MAX_REQUIREMENTS_INPUT_BYTES = 2 * 1024 * 1024


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value!r}")


def _load_requirements_document(path: Path) -> dict[str, object]:
    try:
        payload = path.read_bytes()
        if len(payload) > MAX_REQUIREMENTS_INPUT_BYTES:
            raise ValueError(f"requirements JSON exceeds the {MAX_REQUIREMENTS_INPUT_BYTES}-byte limit")
        value = json.loads(payload, parse_constant=_reject_json_constant)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValidationError(f"requirements JSON could not be read: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError("requirements JSON must contain an object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m media_house.cli",
        description="Local Media House prospect store; discovery network access is opt-in. No outbound sending.",
    )
    parser.add_argument("--db", default=DEFAULT_DATABASE, help="SQLite database path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import-csv", help="validate and atomically import lead-pipeline CSV")
    import_parser.add_argument("--db", dest="db", default=argparse.SUPPRESS, help="SQLite database path")
    import_parser.add_argument("csv_path", type=Path)
    import_parser.add_argument("--researcher", required=True, help="human researcher name/identifier")

    list_parser = subparsers.add_parser("list", help="list deduplicated prospects as JSON")
    list_parser.add_argument("--db", dest="db", default=argparse.SUPPRESS, help="SQLite database path")
    list_parser.add_argument("--status", dest="lead_status", help="filter by lead status")

    draft_parser = subparsers.add_parser("draft", help="create a PENDING_APPROVAL outreach draft")
    draft_parser.add_argument("--db", dest="db", default=argparse.SUPPRESS, help="SQLite database path")
    draft_parser.add_argument("prospect_id", type=int)
    draft_parser.add_argument("--channel", help="channel; defaults to the lead's contact channel")
    draft_parser.add_argument("--subject", default="")
    draft_parser.add_argument("--content", required=True)
    draft_parser.add_argument("--actor", required=True, help="drafting actor")

    approve_parser = subparsers.add_parser("approve-draft", help="human-approve without sending")
    approve_parser.add_argument("--db", dest="db", default=argparse.SUPPRESS, help="SQLite database path")
    approve_parser.add_argument("draft_id", type=int)
    approve_parser.add_argument("--actor", required=True, help="human approving actor")

    meeting_record = subparsers.add_parser(
        "meeting-record", help="record human-authored notes from a completed customer meeting"
    )
    meeting_record.add_argument("--db", dest="db", default=argparse.SUPPRESS, help="SQLite database path")
    meeting_record.add_argument("--prospect-id", type=int, required=True)
    meeting_record.add_argument("--meeting-at", required=True, help="meeting date/time as an ISO date or UTC datetime")
    meeting_record.add_argument("--notes", required=True, help="human-authored meeting notes")
    meeting_record.add_argument(
        "--notes-permission",
        required=True,
        choices=("permission_granted", "not_required_confirmed"),
        help="permission metadata for storing the notes",
    )
    meeting_record.add_argument(
        "--recording-policy",
        required=True,
        choices=("not_recorded", "permission_granted", "permission_denied"),
        help="recording metadata; the application never records audio",
    )
    meeting_record.add_argument("--actor", required=True, help="human actor recording the notes")

    meeting_list = subparsers.add_parser("meeting-list", help="list recorded customer meetings as JSON")
    meeting_list.add_argument("--db", dest="db", default=argparse.SUPPRESS, help="SQLite database path")
    meeting_list.add_argument("--prospect-id", type=int)

    requirements_capture = subparsers.add_parser(
        "requirements-capture", help="capture a versioned requirements draft from a completed meeting"
    )
    requirements_capture.add_argument("--db", dest="db", default=argparse.SUPPRESS, help="SQLite database path")
    requirements_capture.add_argument("meeting_id", type=int)
    requirements_capture.add_argument("json_path", type=Path)
    requirements_capture.add_argument("--actor", required=True, help="human actor capturing the requirements")
    requirements_capture.add_argument(
        "--status",
        choices=("DRAFT", "NEEDS_CLARIFICATION", "READY_FOR_REVIEW"),
        default="DRAFT",
    )
    requirements_capture.add_argument("--supersedes-id", type=int)

    requirements_list = subparsers.add_parser("requirements-list", help="list requirements document versions")
    requirements_list.add_argument("--db", dest="db", default=argparse.SUPPRESS, help="SQLite database path")
    requirements_list.add_argument("--prospect-id", type=int)
    requirements_list.add_argument("--meeting-id", type=int)

    requirements_show = subparsers.add_parser("requirements-show", help="show one requirements document version")
    requirements_show.add_argument("--db", dest="db", default=argparse.SUPPRESS, help="SQLite database path")
    requirements_show.add_argument("document_id", type=int)

    requirements_export = subparsers.add_parser(
        "requirements-export", help="export one schema-compatible requirements JSON document"
    )
    requirements_export.add_argument("--db", dest="db", default=argparse.SUPPRESS, help="SQLite database path")
    requirements_export.add_argument("document_id", type=int)
    requirements_export.add_argument("output", type=Path, help="JSON output path, or - for stdout")

    requirements_approve = subparsers.add_parser(
        "requirements-approve", help="explicitly approve a READY_FOR_REVIEW requirements baseline"
    )
    requirements_approve.add_argument("--db", dest="db", default=argparse.SUPPRESS, help="SQLite database path")
    requirements_approve.add_argument("document_id", type=int)
    requirements_approve.add_argument("--actor", required=True, help="human approving actor")
    requirements_approve.add_argument("--approved-at", help="optional ISO datetime for deterministic audit metadata")

    export_parser = subparsers.add_parser("export-csv", help="export the current lead view")
    export_parser.add_argument("--db", dest="db", default=argparse.SUPPRESS, help="SQLite database path")
    export_parser.add_argument("output", type=Path, help="output CSV path, or - for stdout")

    package_list = subparsers.add_parser("package-list", help="list active service packages as JSON")
    package_list.add_argument("--db", dest="db", default=argparse.SUPPRESS, help="SQLite database path")
    package_list.add_argument("--include-inactive", action="store_true", help="include inactive packages")

    package_create = subparsers.add_parser("package-create", help="create a local service package")
    package_create.add_argument("--db", dest="db", default=argparse.SUPPRESS, help="SQLite database path")
    package_create.add_argument("--name", required=True)
    package_create.add_argument("--description", required=True)
    package_create.add_argument("--price-amount", type=int, required=True, help="positive integer amount in the currency unit")
    package_create.add_argument("--currency", default="INR")
    package_create.add_argument("--inactive", action="store_true", help="create the package as inactive")
    package_create.add_argument("--delivery-notes", default="")
    package_create.add_argument("--actor", required=True, help="human actor creating the package")

    package_update = subparsers.add_parser("package-update", help="update a local service package")
    package_update.add_argument("--db", dest="db", default=argparse.SUPPRESS, help="SQLite database path")
    package_update.add_argument("package_id", type=int)
    package_update.add_argument("--name")
    package_update.add_argument("--description")
    package_update.add_argument("--price-amount", type=int)
    package_update.add_argument("--currency")
    package_update.add_argument("--active", choices=("true", "false"))
    package_update.add_argument("--delivery-notes")
    package_update.add_argument("--actor", required=True, help="human actor updating the package")

    order_create = subparsers.add_parser("order-create", help="create a REQUESTED local order intent")
    order_create.add_argument("--db", dest="db", default=argparse.SUPPRESS, help="SQLite database path")
    order_create.add_argument("--prospect-id", type=int, required=True)
    order_create.add_argument("--package-id", type=int, required=True)
    order_create.add_argument("--requested-by", required=True, help="human actor requesting the order")
    order_create.add_argument("--notes", default="")

    order_list = subparsers.add_parser("order-list", help="list local order intents")
    order_list.add_argument("--db", dest="db", default=argparse.SUPPRESS, help="SQLite database path")
    order_list.add_argument("--prospect-id", type=int)

    payment_create = subparsers.add_parser("payment-request-create", help="create a pending local payment request intent")
    payment_create.add_argument("--db", dest="db", default=argparse.SUPPRESS, help="SQLite database path")
    payment_create.add_argument("--order-id", type=int, required=True)
    payment_create.add_argument("--created-by", required=True, help="human actor creating the request")
    payment_create.add_argument("--provider-reference")
    payment_create.add_argument("--payment-link")
    payment_create.add_argument("--upi-uri")

    payment_list = subparsers.add_parser("payment-request-list", help="list local payment request intents")
    payment_list.add_argument("--db", dest="db", default=argparse.SUPPRESS, help="SQLite database path")
    payment_list.add_argument("--order-id", type=int)

    discovery_parser = subparsers.add_parser("discover", help="plan, fetch for review, or import public OSM business records")
    discovery_commands = discovery_parser.add_subparsers(dest="discovery_command", required=True)
    plan_parser = discovery_commands.add_parser("plan", help="show the bounded query without network or database access")
    run_parser = discovery_commands.add_parser("run", help="fetch only with explicit network confirmation, or reuse cache-only")
    for command_parser in (plan_parser, run_parser):
        command_parser.add_argument("--bbox", required=True, help="south,west,north,east; use --bbox=... for negative coordinates")
        command_parser.add_argument("--geography", required=True, help="operator-supplied geography label (not a verified address)")
        command_parser.add_argument("--filter", dest="filters", action="append", required=True, help="repeat 1–5 key=value filters (OR); quote shop=*")
        command_parser.add_argument("--max-results", type=int, default=25, help="output cap, 1–200 (default 25)")
        command_parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="Overpass HTTP(S) interpreter endpoint")
        command_parser.add_argument("--timeout", type=int, default=25, help="server seconds, 1–60; HTTP timeout adds 5 seconds")
        command_parser.add_argument("--cache-dir", type=Path, default=Path(".media_house/discovery-cache"))
        command_parser.add_argument("--cache-ttl", type=int, default=86400, help="freshness in seconds, 0–604800 (default one day)")
    run_parser.add_argument("--confirm-network", action="store_true", help="explicitly permit one source request if cache is absent/expired")
    run_parser.add_argument("--cache-only", action="store_true", help="use only a fresh cache entry; never access the network")
    run_parser.add_argument("--force-refresh", action="store_true", help="bypass cache; requires --confirm-network")
    run_parser.add_argument("--output", type=Path, default=Path("-"), help="reviewable result JSON path, or - for stdout (default)")

    discovery_import = discovery_commands.add_parser("import", help="preview saved JSON; write only with --confirm-import and --researcher")
    discovery_import.add_argument("json_path", type=Path)
    discovery_import.add_argument("--db", default=argparse.SUPPRESS, help="SQLite database path")
    discovery_import.add_argument("--confirm-import", action="store_true", help="approve the selected records for atomic local import")
    discovery_import.add_argument("--researcher", help="required human researcher name/identifier for confirmed imports")
    discovery_import.add_argument("--source-id", action="append", help="select a reviewed OSM object, e.g. node/123; repeat (default all)")

    finder_parser = subparsers.add_parser("finder", help="offline saved-input First Customer Finder")
    finder_commands = finder_parser.add_subparsers(dest="finder_command", required=True)
    finder_classify = finder_commands.add_parser("classify", help="classify saved JSON without writing SQLite")
    finder_classify.add_argument("json_path", type=Path)
    finder_classify.add_argument("--synthetic-db", required=True, help="disposable SQLite path outside the workspace")
    finder_import = finder_commands.add_parser("import", help="preview or explicitly import entirely NEW saved records")
    finder_import.add_argument("json_path", type=Path)
    finder_import.add_argument("--synthetic-db", required=True, help="disposable SQLite path outside the workspace")
    finder_import.add_argument("--confirm-import", action="store_true", help="permit the insert-only synthetic import")
    finder_import.add_argument("--researcher", help="required human researcher for confirmed imports")
    return parser


def _discovery_command(arguments: argparse.Namespace) -> None:
    if arguments.discovery_command == "import":
        result = import_discovery(
            load_result(arguments.json_path), arguments.db,
            confirm_import=arguments.confirm_import, researcher=arguments.researcher, source_ids=arguments.source_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        if not result["importable"]:
            raise DiscoveryError("Preview contains invalid selected rows; review invalid_rows before import", code="invalid_import")
        return

    config = DiscoveryConfig(
        bbox=BoundingBox.parse(arguments.bbox), geography=arguments.geography,
        filters=tuple(CategoryFilter.parse(item) for item in arguments.filters),
        max_results=arguments.max_results, endpoint=arguments.endpoint, timeout=arguments.timeout,
        cache_dir=arguments.cache_dir, cache_ttl=arguments.cache_ttl,
    )
    plan = build_plan(config)
    if arguments.discovery_command == "plan" or (not arguments.confirm_network and not arguments.cache_only):
        print(json.dumps({
            "plan": plan.as_dict(),
            "cache_dir": str(config.cache_dir), "cache_ttl": config.cache_ttl,
            "network_performed": False, "database_write_performed": False,
        }, ensure_ascii=False, indent=2, sort_keys=True))
        if arguments.discovery_command != "plan":
            raise DiscoveryError("Plan only: network access refused. Supply --confirm-network or --cache-only.", code="network_confirmation_required")
        return
    result = discover(
        config, confirm_network=arguments.confirm_network,
        cache_only=arguments.cache_only, force_refresh=arguments.force_refresh,
    )
    preview = {
        "result_file": str(arguments.output),
        "cache_status": result.cache_status,
        "retrieved_at": result.retrieved_at,
        "counts": {**result.counts, "imported": 0, "new_leads": 0, "duplicate_records": 0},
        "skipped_reasons": result.skipped_reasons,
        "preview": [record.as_dict() for record in result.records[:5]],
        "preview_limit": 5,
        "send_performed": False,
    }
    if str(arguments.output) == "-":
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        print(json.dumps(preview, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
    else:
        save_result(arguments.output, result)
        print(json.dumps(preview, ensure_ascii=False, indent=2, sort_keys=True))


def _finder_command(arguments: argparse.Namespace) -> int:
    if arguments.finder_command == "classify":
        report = classify_saved_input(arguments.json_path, arguments.synthetic_db)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        if report.get("rejected"):
            print(f"error: {report.get('error', 'finder batch rejected; review the classification report')}", file=sys.stderr)
            return 2
        return 0

    report = import_saved_input(
        arguments.json_path,
        arguments.synthetic_db,
        confirm_import=arguments.confirm_import,
        researcher=arguments.researcher,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report.get("rejected"):
        print(f"error: {report.get('error', 'finder batch rejected; review the classification report')}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        # Planning, fetching and previewing must not even initialize SQLite.
        if arguments.command == "discover":
            _discovery_command(arguments)
            return 0
        if arguments.command == "finder":
            return _finder_command(arguments)
        with MediaHouseStore(arguments.db) as store:
            if arguments.command == "import-csv":
                result = store.import_csv(arguments.csv_path, researcher=arguments.researcher)
                print(json.dumps(result.as_dict(), sort_keys=True))
            elif arguments.command == "list":
                print(json.dumps(store.list_prospects(lead_status=arguments.lead_status), ensure_ascii=False, indent=2))
            elif arguments.command == "draft":
                result = store.create_outreach_draft(
                    arguments.prospect_id,
                    channel=arguments.channel,
                    content=arguments.content,
                    created_by=arguments.actor,
                    subject=arguments.subject,
                )
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            elif arguments.command == "approve-draft":
                result = store.approve_draft(arguments.draft_id, human_actor=arguments.actor)
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            elif arguments.command == "meeting-record":
                result = store.record_meeting(
                    arguments.prospect_id,
                    meeting_at=arguments.meeting_at,
                    notes=arguments.notes,
                    notes_permission=arguments.notes_permission,
                    recording_policy=arguments.recording_policy,
                    actor=arguments.actor,
                )
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            elif arguments.command == "meeting-list":
                print(
                    json.dumps(
                        store.list_meetings(prospect_id=arguments.prospect_id),
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            elif arguments.command == "requirements-capture":
                result = store.capture_requirements(
                    arguments.meeting_id,
                    _load_requirements_document(arguments.json_path),
                    actor=arguments.actor,
                    status=arguments.status,
                    supersedes_document_id=arguments.supersedes_id,
                )
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            elif arguments.command == "requirements-list":
                print(
                    json.dumps(
                        store.list_requirements_documents(
                            prospect_id=arguments.prospect_id,
                            meeting_id=arguments.meeting_id,
                        ),
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                )
            elif arguments.command == "requirements-show":
                print(
                    json.dumps(
                        store.get_requirements_document(arguments.document_id),
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                )
            elif arguments.command == "requirements-export":
                if str(arguments.output) == "-":
                    store.export_requirements(arguments.document_id, sys.stdout)
                else:
                    store.export_requirements(arguments.document_id, arguments.output)
                    print(json.dumps({"exported": True, "output": str(arguments.output)}, sort_keys=True), file=sys.stderr)
            elif arguments.command == "requirements-approve":
                result = store.approve_requirements(
                    arguments.document_id,
                    actor=arguments.actor,
                    approved_at=arguments.approved_at,
                )
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            elif arguments.command == "export-csv":
                if str(arguments.output) == "-":
                    count = store.export_csv(sys.stdout)
                else:
                    count = store.export_csv(arguments.output)
                print(json.dumps({"rows_exported": count}, sort_keys=True), file=sys.stderr)
            elif arguments.command == "package-list":
                print(json.dumps(store.list_service_packages(include_inactive=arguments.include_inactive), ensure_ascii=False, indent=2))
            elif arguments.command == "package-create":
                result = store.create_service_package(
                    name=arguments.name,
                    description=arguments.description,
                    price_amount=arguments.price_amount,
                    currency=arguments.currency,
                    active=not arguments.inactive,
                    delivery_notes=arguments.delivery_notes,
                    actor=arguments.actor,
                )
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            elif arguments.command == "package-update":
                result = store.update_service_package(
                    arguments.package_id,
                    actor=arguments.actor,
                    name=arguments.name,
                    description=arguments.description,
                    price_amount=arguments.price_amount,
                    currency=arguments.currency,
                    active=arguments.active,
                    delivery_notes=arguments.delivery_notes,
                )
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            elif arguments.command == "order-create":
                result = store.create_order_request(
                    arguments.prospect_id,
                    arguments.package_id,
                    requested_by=arguments.requested_by,
                    notes=arguments.notes,
                )
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            elif arguments.command == "order-list":
                print(json.dumps(store.list_order_requests(prospect_id=arguments.prospect_id), ensure_ascii=False, indent=2))
            elif arguments.command == "payment-request-create":
                result = store.create_payment_request(
                    arguments.order_id,
                    created_by=arguments.created_by,
                    provider_reference=arguments.provider_reference,
                    payment_link=arguments.payment_link,
                    upi_uri=arguments.upi_uri,
                )
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            elif arguments.command == "payment-request-list":
                print(json.dumps(store.list_payment_requests(order_id=arguments.order_id), ensure_ascii=False, indent=2))
            else:  # pragma: no cover - argparse enforces the command set.
                parser.error(f"unknown command {arguments.command}")
    except (MediaHouseError, OSError, sqlite3.Error) as exc:
        if isinstance(exc, FinderError) and exc.report is not None:
            print(json.dumps(exc.report, ensure_ascii=False, indent=2, sort_keys=True))
        code = f" [{exc.code}]" if isinstance(exc, (DiscoveryError, FinderError)) else ""
        print(f"error{code}: {exc}", file=sys.stderr)
        if isinstance(exc, MediaHouseError) and getattr(exc, "errors", None):
            for detail in exc.errors:
                if detail != str(exc):
                    print(f" - {detail}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
