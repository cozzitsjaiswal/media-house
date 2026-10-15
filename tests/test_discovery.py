from __future__ import annotations

import copy
import io
import json
import sqlite3
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs

from media_house.cli import main
from media_house.discovery import BoundingBox, CategoryFilter, DiscoveryConfig, DiscoveryError, build_plan
from media_house.discovery.config import ATTRIBUTION, LICENSE
from media_house.discovery.files import MAX_JSON_BYTES, atomic_write_json, decode_json
from media_house.discovery.integration import FIT_REASON, import_discovery, preview_import, record_to_csv_row
from media_house.discovery.overpass import discover, load_result, parse_overpass, result_from_dict, save_result
from media_house.errors import ValidationError
from media_house.store import MediaHouseStore


FIXTURE = Path(__file__).parent / "fixtures" / "overpass_synthetic.json"
RETRIEVED_AT = "2026-10-13T12:00:00Z"
NOW = 1791892800.0


def config(directory: Path, **changes) -> DiscoveryConfig:
    values = {
        "bbox": BoundingBox(1, 2, 1.1, 2.1),
        "geography": "Synthetic district",
        "filters": (CategoryFilter("amenity", "restaurant"), CategoryFilter("shop", "*")),
        "cache_dir": directory / "cache",
    }
    values.update(changes)
    return DiscoveryConfig(**values)


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class Response(io.BytesIO):
    status = 200
    headers: dict[str, str] = {}


def opener_for(payload: object) -> Mock:
    return Mock(side_effect=lambda *args, **kwargs: Response(json.dumps(payload).encode("utf-8")))


class OfflineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.config = config(self.directory)
        self.plan = build_plan(self.config)
        self.result = parse_overpass(fixture(), self.plan, retrieved_at=RETRIEVED_AT)
        # Any accidental real urllib call, including redirects, fails every test.
        self.network_guard = patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Live network forbidden in tests"))
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)

    def assert_code(self, code: str, function, *args, **kwargs) -> DiscoveryError:
        with self.assertRaises(DiscoveryError) as caught:
            function(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)
        return caught.exception


class DiscoveryConfigTests(OfflineTestCase):
    def test_plan_bounds_union_output_and_attribution(self) -> None:
        plan = self.plan.as_dict()
        self.assertIn('[out:json][timeout:25][maxsize:33554432]', plan["query"])
        self.assertIn('nwr["amenity"="restaurant"](1,2,1.1,2.1);', plan["query"])
        self.assertIn('nwr["shop"](1,2,1.1,2.1);', plan["query"])
        self.assertTrue(plan["query"].endswith("out body center 25;"))
        self.assertEqual("OR", plan["filter_combination"])
        self.assertEqual(ATTRIBUTION, plan["attribution"])
        self.assertEqual(LICENSE, plan["license"])
        self.assertEqual(64, len(plan["cache_key"]))
        self.assertFalse(self.config.cache_dir.exists())

    def test_query_values_are_quoted_not_interpreted(self) -> None:
        attack = 'restaurant"];out;["x"="back\\slash'
        item = CategoryFilter("amenity", attack)
        quoted = f'["amenity"={json.dumps(attack)}]'
        self.assertEqual(quoted, item.as_ql())
        self.assertIn(quoted, build_plan(replace(self.config, filters=(item,))).query)
        for text in ('bad[key]=x', 'amenity=', 'amenity', 'amenity=bad\nvalue'):
            with self.subTest(text=text):
                self.assert_code("invalid_config", CategoryFilter.parse, text)

    def test_bbox_invalid_ranges_sizes_and_nonfinite(self) -> None:
        invalid = ((1, 2, 1, 2.1), (2, 2, 1, 2.1), (-91, 2, -90.9, 2.1),
                   (1, 179.9, 1.1, -179.9), (1, 2, 2, 2.1), (0, 0, .5, .5),
                   (float("nan"), 2, 1.1, 2.1), (1, 2, float("inf"), 2.1), (True, 2, 1.1, 2.1),
                   (10 ** 400, 2, 1.1, 2.1))
        for values in invalid:
            with self.subTest(values=values):
                self.assert_code("invalid_config", BoundingBox, *values)
        for text in ("1,2,3", "a,2,3,4", "1,2,3,4,5"):
            self.assert_code("invalid_config", BoundingBox.parse, text)
        self.assertEqual(BoundingBox(-1.1, -2.1, -1, -2), BoundingBox.parse("-1.1,-2.1,-1,-2"))

    def test_config_caps_and_endpoint_validation(self) -> None:
        cases = [
            {"max_results": 0}, {"max_results": 201}, {"max_results": True}, {"max_results": 1.5},
            {"filters": ()}, {"filters": tuple(CategoryFilter("shop", str(n)) for n in range(6))},
            {"filters": (CategoryFilter("shop", "*"),) * 2}, {"timeout": 0}, {"timeout": 61},
            {"cache_ttl": -1}, {"cache_ttl": 604801}, {"geography": " "},
            {"endpoint": "file:///tmp/data"}, {"endpoint": "https://user:secret@example.com"},
            {"endpoint": "https://example.com/#fragment"}, {"endpoint": "https://example.com:wrong"},
            {"endpoint": "https://example.com/\nquery"}, {"endpoint": "https://[broken"},
            {"endpoint": "https://example.com/☃"}, {"geography": "\ud800"},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                self.assert_code("invalid_config", config, self.directory, **changes)
        self.assertEqual("http://127.0.0.1:12345/interpreter", config(self.directory, endpoint="http://127.0.0.1:12345/interpreter").endpoint)

    def test_cache_key_tracks_request_not_cache_policy(self) -> None:
        self.assertEqual(self.plan.cache_key, build_plan(replace(self.config, cache_ttl=1, cache_dir=self.directory)).cache_key)
        self.assertEqual(self.plan.cache_key, build_plan(replace(self.config, filters=tuple(reversed(self.config.filters)))).cache_key)
        for changes in ({"endpoint": "https://other.example/interpreter"}, {"geography": "Different label"}, {"max_results": 2}):
            self.assertNotEqual(self.plan.cache_key, build_plan(replace(self.config, **changes)).cache_key)


class DiscoveryParsingTests(OfflineTestCase):
    def test_names_categories_locations_public_fields_and_unknowns(self) -> None:
        node, way, relation = self.result.records
        self.assertEqual("Synthetic Café", node.name)
        self.assertEqual("node/101", node.source_object_id)
        self.assertEqual("https://www.openstreetmap.org/node/101", node.source_url)
        self.assertEqual({"amenity": "restaurant"}, node.category_tags)
        self.assertEqual({"shop": "bakery"}, way.category_tags)
        self.assertEqual(("shop=*",), way.matched_filters)
        self.assertEqual((1.06, 2.06, "center"), (way.latitude, way.longitude, way.location_kind))
        self.assertEqual("https://www.cafe.example/menu", node.website)
        self.assertEqual("https://dining.example", relation.website)
        self.assertIsNone(way.website)
        self.assertEqual("hello@cafe.example", node.public_contacts["contact:email"])
        self.assertNotIn("contact:name", node.public_contacts)
        for record in self.result.records:
            value = record.as_dict()
            self.assertTrue(value["discovered"])
            self.assertTrue(value["source_returned"])
            for flag in ("verified", "qualified", "customer_pain_confirmed", "name_missing"):
                self.assertIs(False, value[flag])
            self.assertEqual("unknown", value["automation_status"])
            self.assertEqual("unknown", value["customer_need"])
            self.assertEqual(RETRIEVED_AT, value["retrieved_at"])
            self.assertEqual(ATTRIBUTION, value["attribution"])
            self.assertEqual(LICENSE, value["license"])

    def test_missing_malformed_duplicate_and_mismatched_elements_are_counted(self) -> None:
        payload = fixture()
        good = payload["elements"][0]
        payload["elements"] += [
            None, [], {"type": "node", "id": True, "tags": {}},
            {**good, "id": 404, "tags": {"amenity": "restaurant"}},
            {**good, "id": 405, "tags": {"name": 123}},
            {**good, "id": 406, "lat": "1.1"},
            {"type": "way", "id": 407, "tags": good["tags"]},
            {**good, "id": 408, "tags": {"name": "Other", "amenity": "school"}},
            good,
        ]
        result = parse_overpass(payload, self.plan, retrieved_at=RETRIEVED_AT)
        self.assertEqual(3, result.counts["discovered"])
        self.assertEqual({"malformed_element": 5, "missing_name": 1, "missing_location": 1,
                          "category_mismatch": 1, "duplicate_source_object": 1}, result.skipped_reasons)
        self.assertEqual(9, result.counts["skipped"])
        self.assertEqual(7, result.counts["invalid"])
        self.assertFalse(result.cacheable)
        self.assertEqual("not_cached_malformed", result.cache_status)

    def test_result_cap_is_enforced_locally_even_if_server_ignores_it(self) -> None:
        plan = build_plan(replace(self.config, max_results=1))
        result = parse_overpass(fixture(), plan, retrieved_at=RETRIEVED_AT)
        self.assertEqual(1, len(result.records))
        self.assertEqual({"result_cap": 2}, result.skipped_reasons)

    def test_nodes_outside_bbox_are_skipped_but_way_centers_may_be_outside(self) -> None:
        payload = fixture()
        payload["elements"][0]["lat"] = 8.0
        payload["elements"][1]["center"]["lat"] = 8.0
        result = parse_overpass(payload, self.plan, retrieved_at=RETRIEVED_AT)
        self.assertEqual({"outside_bbox": 1}, result.skipped_reasons)
        self.assertEqual(["way/202", "relation/303"], [record.source_object_id for record in result.records])
        self.assertEqual(result.as_dict(), result_from_dict(result.as_dict()).as_dict())

    def test_empty_and_missing_name_results_are_distinguishable_from_failure(self) -> None:
        self.assertEqual(0, parse_overpass({"elements": []}, self.plan, retrieved_at=RETRIEVED_AT).counts["discovered"])
        result = parse_overpass({"elements": [{"type": "node", "id": 1}]}, self.plan, retrieved_at=RETRIEVED_AT)
        self.assertEqual({"missing_name": 1}, result.skipped_reasons)
        self.assertTrue(result.cacheable)

    def test_bad_envelopes_malformed_json_and_partial_service_results_fail(self) -> None:
        for value in ({}, {"elements": None}, {"elements": {}}, [], None):
            self.assert_code("malformed_response", parse_overpass, value, self.plan, retrieved_at=RETRIEVED_AT)
        for data in (b"{", b"<html>unavailable</html>", b"\xff", b'{"elements": NaN}', b'{"elements": {}, "elements": []}'):
            self.assert_code("malformed_json", decode_json, data)
        self.assert_code("service_unavailable", parse_overpass, {**fixture(), "remark": "runtime error: timed out"}, self.plan, retrieved_at=RETRIEVED_AT)
        self.assert_code("response_limit", decode_json, b" " * (MAX_JSON_BYTES + 1))

    def test_extreme_malformed_fields_are_skipped_without_crashing(self) -> None:
        good = fixture()["elements"][0]
        elements = [
            {**good, "lat": 10 ** 400}, {**good, "lon": float("nan")}, {**good, "lat": True},
            {**good, "tags": {"name": "\ud800", "amenity": "restaurant"}},
            {**good, "type": {}}, {**good, "tags": []},
        ]
        result = parse_overpass({"elements": elements}, self.plan, retrieved_at=RETRIEVED_AT)
        self.assertEqual({"malformed_element": 6}, result.skipped_reasons)
        self.assertEqual(0, result.counts["discovered"])
        self.assertFalse(result.cacheable)

    def test_saved_result_round_trip_and_evidence_tamper_rejection(self) -> None:
        path = self.directory / "result.json"
        save_result(path, self.result)
        self.assertEqual(self.result.as_dict(), load_result(path).as_dict())
        for field, changed in (("verified", True), ("qualified", True), ("customer_pain_confirmed", True),
                               ("source_url", "https://other.example"), ("license", "invented"), ("name", 3),
                               ("public_contacts", {"contact:name": "Someone"}), ("latitude", "1.2")):
            value = copy.deepcopy(self.result.as_dict())
            value["records"][0][field] = changed
            with self.subTest(field=field):
                self.assert_code("invalid_saved_result", result_from_dict, value)
        value = self.result.as_dict()
        value["counts"]["discovered"] = 999
        self.assert_code("invalid_saved_result", result_from_dict, value)

    def test_shipped_offline_preview_fixture_is_valid(self) -> None:
        result = load_result(FIXTURE.parent / "discovery_synthetic.json")
        self.assertEqual("Synthetic Offline Café", result.records[0].name)
        self.assertTrue(preview_import(result)["preview_only"])


class DiscoveryFetchTests(OfflineTestCase):
    def fetch(self, opener: Mock, **kwargs):
        return discover(self.config, confirm_network=True, opener=opener, clock=lambda: NOW, **kwargs)

    def cache_path(self) -> Path:
        return self.config.cache_dir / f"{self.plan.cache_key}.json"

    def test_post_encoding_user_agent_timeout_and_atomic_cache(self) -> None:
        opener = opener_for(fixture())
        result = self.fetch(opener)
        opener.assert_called_once()
        request = opener.call_args.args[0]
        self.assertEqual("POST", request.method)
        self.assertEqual({"data": [self.plan.query]}, parse_qs(request.data.decode("ascii")))
        self.assertIn("MediaHouse-LeadDiscovery", request.get_header("User-agent"))
        self.assertEqual("application/x-www-form-urlencoded", request.get_header("Content-type"))
        self.assertEqual(30, opener.call_args.kwargs["timeout"])
        self.assertEqual(RETRIEVED_AT, result.retrieved_at)
        self.assertTrue(self.cache_path().exists())
        self.assertEqual([self.cache_path()], list(self.config.cache_dir.iterdir()))

    def test_adapter_requires_network_gate_even_with_existing_cache(self) -> None:
        self.fetch(opener_for(fixture()))
        opener = Mock(side_effect=AssertionError("must not call opener"))
        self.assert_code("network_confirmation_required", discover, self.config, opener=opener)
        opener.assert_not_called()

    def test_cache_hit_and_cache_only_preserve_original_retrieval_time(self) -> None:
        self.fetch(opener_for(fixture()))
        opener = Mock(side_effect=AssertionError("must use cache"))
        for cache_only in (False, True):
            result = discover(self.config, confirm_network=not cache_only, cache_only=cache_only, opener=opener, clock=lambda: NOW + 20)
            self.assertEqual("hit", result.cache_status)
            self.assertEqual(RETRIEVED_AT, result.retrieved_at)
        opener.assert_not_called()

    def test_cache_expiry_boundary_refresh_and_zero_ttl(self) -> None:
        self.fetch(opener_for(fixture()))
        self.assert_code("cache_expired", discover, self.config, cache_only=True, clock=lambda: NOW + 86400)
        self.assert_code("cache_expired", discover, replace(self.config, cache_ttl=0), cache_only=True, clock=lambda: NOW)
        opener = opener_for(fixture())
        result = discover(self.config, confirm_network=True, opener=opener, clock=lambda: NOW + 86400)
        opener.assert_called_once()
        self.assertNotEqual(RETRIEVED_AT, result.retrieved_at)

    def test_cache_only_miss_and_incompatible_flags_never_call_source(self) -> None:
        opener = Mock()
        self.assert_code("cache_miss", discover, self.config, cache_only=True, opener=opener)
        self.assert_code("invalid_config", discover, self.config, cache_only=True, force_refresh=True, opener=opener)
        opener.assert_not_called()
        self.assertFalse(self.config.cache_dir.exists())

    def test_corrupt_cache_requires_explicit_refresh(self) -> None:
        atomic_write_json(self.cache_path(), {"wrong": "shape"})
        opener = opener_for(fixture())
        self.assert_code("cache_invalid", self.fetch, opener)
        opener.assert_not_called()
        self.fetch(opener, force_refresh=True)
        opener.assert_called_once()
        self.assertEqual(3, load_result(self.cache_path()).counts["discovered"])

    def test_future_cache_timestamp_is_not_treated_as_fresh(self) -> None:
        self.fetch(opener_for(fixture()))
        opener = Mock(side_effect=AssertionError("No silent network fallback"))
        self.assert_code("cache_invalid", discover, self.config, confirm_network=True, clock=lambda: NOW - 1, opener=opener)
        opener.assert_not_called()

    def test_http_failures_report_retry_after_without_retry_or_cache(self) -> None:
        for status, code in ((429, "rate_limited"), (403, "forbidden"), (500, "service_unavailable"), (503, "service_unavailable"), (302, "http_error"), (400, "http_error")):
            for retry_after in ("120", "Wed, 14 Oct 2026 12:00:00 GMT"):
                with self.subTest(status=status, retry_after=retry_after):
                    failure = urllib.error.HTTPError(self.config.endpoint, status, "error", {"Retry-After": retry_after}, None)
                    opener = Mock(side_effect=failure)
                    caught = self.assert_code(code, self.fetch, opener)
                    self.assertEqual(status, caught.status)
                    self.assertEqual(retry_after, caught.retry_after)
                    self.assertIn("No automatic retry", str(caught))
                    opener.assert_called_once()
                    self.assertFalse(self.cache_path().exists())

    def test_network_and_timeout_errors_are_typed_and_not_cached(self) -> None:
        for failure in (TimeoutError("late"), urllib.error.URLError("unreachable"), OSError("reset")):
            opener = Mock(side_effect=failure)
            self.assert_code("network_error", self.fetch, opener)
            opener.assert_called_once()
            self.assertFalse(self.cache_path().exists())

    def test_malformed_response_and_partial_source_failure_are_not_cached(self) -> None:
        for body, code in ((b"not-json", "malformed_json"), (b'{"elements": {}}', "malformed_response"),
                           (b'{"elements": [], "remark": "timed out"}', "service_unavailable"),
                           (b" " * (MAX_JSON_BYTES + 1), "response_limit")):
            opener = Mock(return_value=Response(body))
            self.assert_code(code, self.fetch, opener)
            self.assertFalse(self.cache_path().exists())
        payload = fixture()
        payload["elements"].append(None)
        result = self.fetch(opener_for(payload))
        self.assertEqual(3, result.counts["discovered"])
        self.assertEqual("not_cached_malformed", result.cache_status)
        self.assertFalse(self.cache_path().exists())

    def test_failed_refresh_preserves_previous_cache_and_cleans_temporary_file(self) -> None:
        self.fetch(opener_for(fixture()))
        before = self.cache_path().read_bytes()
        with patch("media_house.discovery.files.os.replace", side_effect=OSError("disk failure")):
            self.assert_code("file_write", self.fetch, opener_for(fixture()), force_refresh=True)
        self.assertEqual(before, self.cache_path().read_bytes())
        self.assertEqual([self.cache_path()], list(self.config.cache_dir.iterdir()))
        self.assert_code("network_error", self.fetch, Mock(side_effect=TimeoutError()), force_refresh=True)
        self.assertEqual(before, self.cache_path().read_bytes())

    def test_atomic_first_write_failure_leaves_no_result(self) -> None:
        output = self.directory / "review.json"
        with patch("media_house.discovery.files.os.replace", side_effect=OSError("disk failure")):
            self.assert_code("file_write", save_result, output, self.result)
        self.assertFalse(output.exists())
        self.assertEqual([], list(self.directory.iterdir()))


class DiscoveryIntegrationTests(OfflineTestCase):
    def database(self) -> Path:
        return self.directory / "leads.sqlite3"

    def approve(self, result=None, **kwargs):
        return import_discovery(result or self.result, self.database(), confirm_import=True, researcher="reviewer", **kwargs)

    def test_conversion_facts_unknowns_source_metadata_and_no_inferences(self) -> None:
        row = record_to_csv_row(self.result.records[0])
        for field, expected in (("verified_status", "discovered"), ("lead_status", "new"), ("contact_channel", "email"),
                                ("fit_confidence", "unknown"), ("channel_permission_status", "unknown"),
                                ("contact_name", "unknown"), ("contact_role", "unknown"), ("opt_out", "unknown")):
            self.assertEqual(expected, row[field])
        self.assertEqual(FIT_REASON, row["fit_reason"])
        self.assertEqual(RETRIEVED_AT, row["source_checked_at"])
        notes = json.loads(row["notes"])["discovery"]
        self.assertEqual("node/101", notes["source_object_id"])
        self.assertEqual(ATTRIBUTION, notes["attribution"])
        self.assertEqual(LICENSE, notes["license"])
        self.assertEqual("hello@cafe.example", notes["public_contacts"]["contact:email"])
        self.assertEqual("unknown", notes["customer_need"])
        self.assertFalse(notes["qualified"])
        self.assertEqual("phone", record_to_csv_row(self.result.records[1])["contact_channel"])
        self.assertEqual("unknown", record_to_csv_row(self.result.records[2])["contact_channel"])

    def test_preview_requires_no_database_even_with_researcher(self) -> None:
        with patch("media_house.discovery.integration.MediaHouseStore", side_effect=AssertionError("Preview must not open SQLite")):
            result = import_discovery(self.result, self.database(), researcher="reviewer")
        self.assertTrue(result["preview_only"])
        self.assertEqual(3, result["counts"]["accepted"])
        self.assertEqual(0, result["counts"]["imported"])
        self.assertFalse(result["database_deduplication_checked"])
        self.assertFalse(self.database().exists())

    def test_confirmed_import_requires_researcher_before_database_open(self) -> None:
        for researcher in (None, "", "   "):
            self.assert_code("researcher_required", import_discovery, self.result, self.database(), confirm_import=True, researcher=researcher)
        self.assertFalse(self.database().exists())

    def test_repeated_import_deduplicates_via_store_and_never_creates_drafts(self) -> None:
        with patch.object(MediaHouseStore, "create_outreach_draft", side_effect=AssertionError("No drafting")), patch.object(MediaHouseStore, "approve_draft", side_effect=AssertionError("No approval")):
            first = self.approve()
            second = self.approve()
        self.assertEqual(3, first["counts"]["discovered"])
        self.assertEqual(3, first["counts"]["imported"])
        self.assertEqual(3, first["counts"]["new_leads"])
        self.assertEqual(0, first["counts"]["duplicate_records"])
        self.assertEqual(0, second["counts"]["new_leads"])
        self.assertEqual(3, second["counts"]["duplicate_records"])
        self.assertEqual(0, second["store_import"]["source_evidence_added"])
        with MediaHouseStore(self.database()) as store:
            self.assertEqual(3, len(store.list_prospects()))
            lead = store.get_prospect(1)
            self.assertEqual("discovered", lead["verified_status"])
            self.assertEqual("new", lead["lead_status"])
            self.assertEqual("unknown", lead["channel_permission_status"])
            self.assertIsNone(lead["opt_out"])
            self.assertIsNone(lead["fit_confidence"])
            self.assertTrue(any("not fully verified" in reason for reason in lead["score_reasons"]))
            evidence = store.list_source_evidence(1)[0]
            self.assertEqual("reviewer", evidence["researcher"])
            self.assertEqual(RETRIEVED_AT, evidence["observed_at"])
            self.assertIn("node/101", evidence["notes"])
            self.assertEqual([], store.list_drafts())
            self.assertEqual([], store.list_audit_events())
        self.assertFalse(first["send_performed"])

    def test_domain_and_name_region_dedupe_within_one_import(self) -> None:
        payload = fixture()
        payload["elements"].append({**payload["elements"][0], "id": 111})
        payload["elements"].append({**payload["elements"][1], "id": 222})
        result = self.approve(parse_overpass(payload, self.plan, retrieved_at=RETRIEVED_AT))
        self.assertEqual(5, result["counts"]["accepted"])
        self.assertEqual(3, result["counts"]["new_leads"])
        self.assertEqual(2, result["counts"]["duplicate_records"])
        self.assertEqual(5, result["store_import"]["source_evidence_added"])

    def test_stable_osm_identity_survives_name_website_and_geography_changes(self) -> None:
        self.approve()
        payload = fixture()
        for index, element in enumerate(payload["elements"]):
            element["tags"]["name"] = f"Renamed synthetic business {index}"
            element["tags"]["website"] = f"https://updated-{index}.example"
        plan = build_plan(replace(self.config, geography="New operator label"))
        result = self.approve(parse_overpass(payload, plan, retrieved_at="2026-10-14T12:00:00Z"))
        self.assertEqual(0, result["counts"]["new_leads"])
        self.assertEqual(3, result["counts"]["duplicate_records"])
        with MediaHouseStore(self.database()) as store:
            self.assertEqual(3, len(store.list_prospects()))
            self.assertEqual("updated-1.example", store.get_prospect(2)["normalized_domain"])
            self.assertEqual(2, store.get_prospect(2)["source_evidence_count"])

    def test_invalid_selected_csv_batch_is_rejected_before_database_creation(self) -> None:
        payload = fixture()
        payload["elements"][1]["tags"]["website"] = "bad website value"
        result = parse_overpass(payload, self.plan, retrieved_at=RETRIEVED_AT)
        preview = preview_import(result)
        self.assertFalse(preview["importable"])
        self.assertEqual(1, preview["counts"]["invalid"])
        self.assertEqual(2, preview["counts"]["accepted"])
        with self.assertRaises(ValidationError):
            self.approve(result)
        self.assertFalse(self.database().exists())
        selected = self.approve(result, source_ids=["node/101"])
        self.assertEqual(1, selected["counts"]["imported"])
        self.assertEqual(2, selected["counts"]["skipped"])

    def test_source_domain_identity_conflict_rolls_back_entire_transaction(self) -> None:
        self.approve()
        with MediaHouseStore(self.database()) as store:
            before = list(store.connection.iterdump())
        payload = fixture()
        new = {"type": "node", "id": 999, "lat": 1.05, "lon": 2.05,
               "tags": {"name": "Must roll back", "amenity": "restaurant"}}
        payload["elements"][1]["tags"]["website"] = "https://cafe.example"
        payload["elements"] = [new, payload["elements"][1]]
        with self.assertRaisesRegex(ValidationError, "conflicting company identity"):
            self.approve(parse_overpass(payload, self.plan, retrieved_at=RETRIEVED_AT))
        with MediaHouseStore(self.database()) as store:
            self.assertEqual(before, list(store.connection.iterdump()))

    def test_database_error_rolls_back_source_and_company_changes(self) -> None:
        self.approve()
        with MediaHouseStore(self.database()) as store:
            before = list(store.connection.iterdump())
        payload = fixture()
        payload["elements"][0]["tags"]["name"] = "Changed before score failure"
        with patch.object(MediaHouseStore, "_refresh_score", side_effect=sqlite3.OperationalError("synthetic failure")):
            with self.assertRaises(sqlite3.OperationalError):
                self.approve(parse_overpass(payload, self.plan, retrieved_at="2026-10-14T12:00:00Z"))
        with MediaHouseStore(self.database()) as store:
            self.assertEqual(before, list(store.connection.iterdump()))

    def test_existing_optout_and_permission_block_are_not_cleared(self) -> None:
        self.approve()
        with MediaHouseStore(self.database()) as store:
            store.connection.execute("UPDATE prospects SET opt_out = 1, channel_permission_status = 'blocked' WHERE id = 1")
            store.connection.commit()
        self.approve()
        with MediaHouseStore(self.database()) as store:
            self.assertTrue(store.get_prospect(1)["opt_out"])
            self.assertEqual("blocked", store.get_prospect(1)["channel_permission_status"])

    def test_discovery_does_not_downgrade_verified_or_erase_human_facts(self) -> None:
        self.approve()
        with MediaHouseStore(self.database()) as store:
            store.connection.execute(
                """
                UPDATE prospects
                SET verified_status = 'verified',
                    fit_reason = 'Confirmed customer pain from a human conversation',
                    fit_confidence = 0.95,
                    contact_channel = 'email',
                    channel_permission_status = 'granted',
                    lead_status = 'qualified'
                WHERE id = 1
                """
            )
            store.connection.commit()

        self.approve()
        with MediaHouseStore(self.database()) as store:
            lead = store.get_prospect(1)
            self.assertEqual("verified", lead["verified_status"])
            self.assertEqual("Confirmed customer pain from a human conversation", lead["fit_reason"])
            self.assertEqual(0.95, lead["fit_confidence"])
            self.assertEqual("email", lead["contact_channel"])
            self.assertEqual("granted", lead["channel_permission_status"])
            self.assertEqual("qualified", lead["lead_status"])

    def test_selection_and_skipped_reason_counts(self) -> None:
        payload = fixture()
        payload["elements"].append({"type": "node", "id": 7})
        result = parse_overpass(payload, self.plan, retrieved_at=RETRIEVED_AT)
        preview = preview_import(result, source_ids=["node/101"])
        self.assertEqual(3, preview["counts"]["discovered"])
        self.assertEqual(1, preview["counts"]["selected"])
        self.assertEqual(1, preview["counts"]["invalid"])
        self.assertEqual(3, preview["counts"]["skipped"])
        self.assert_code("invalid_selection", preview_import, result, source_ids=["node/9999"])


class DiscoveryCLITests(OfflineTestCase):
    def invoke(self, *args: str):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            status = main(["--db", str(self.directory / "untouched.sqlite3"), *args])
        return status, output.getvalue(), error.getvalue()

    def request_args(self, command: str) -> list[str]:
        return ["discover", command, "--bbox", "1,2,1.1,2.1", "--geography", "Synthetic district",
                "--filter", "amenity=restaurant", "--filter", "shop=*", "--cache-dir", str(self.config.cache_dir)]

    def saved(self) -> Path:
        path = self.directory / "saved.json"
        save_result(path, self.result)
        return path

    def test_help_lists_nested_workflow(self) -> None:
        for args in (("--help",), ("discover", "--help"), ("discover", "run", "--help"), ("discover", "import", "--help")):
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as caught:
                main(list(args))
            self.assertEqual(0, caught.exception.code)

    def test_plan_and_unconfirmed_run_are_offline_and_do_not_open_database(self) -> None:
        with patch("media_house.cli.MediaHouseStore", side_effect=AssertionError("No database")), patch("media_house.cli.discover", side_effect=AssertionError("No fetching")):
            status, output, error = self.invoke(*self.request_args("plan"))
            self.assertEqual(0, status)
            self.assertFalse(json.loads(output)["network_performed"])
            self.assertEqual("", error)
            status, output, error = self.invoke(*self.request_args("run"))
            self.assertEqual(2, status)
            self.assertIn("query", json.loads(output)["plan"])
            self.assertIn("--confirm-network", error)
        self.assertEqual([], list(self.directory.iterdir()))

    def test_confirmed_run_writes_reviewable_json_but_not_sqlite(self) -> None:
        output_path = self.directory / "review.json"
        with patch("media_house.discovery.overpass._open_request", opener_for(fixture())) as opener:
            status, output, error = self.invoke(*self.request_args("run"), "--confirm-network", "--output", str(output_path))
        self.assertEqual(0, status, error)
        opener.assert_called_once()
        self.assertEqual(3, json.loads(output)["counts"]["discovered"])
        self.assertEqual(0, json.loads(output)["counts"]["imported"])
        self.assertEqual(3, len(load_result(output_path).records))
        self.assertFalse((self.directory / "untouched.sqlite3").exists())

    def test_stdout_run_is_valid_saved_json_and_preview_goes_to_stderr(self) -> None:
        with patch("media_house.discovery.overpass._open_request", opener_for(fixture())):
            status, output, error = self.invoke(*self.request_args("run"), "--confirm-network")
        self.assertEqual(0, status)
        self.assertEqual(3, len(result_from_dict(json.loads(output)).records))
        self.assertEqual(3, len(json.loads(error)["preview"]))

    def test_cache_only_without_confirmation_is_offline(self) -> None:
        with patch("media_house.discovery.overpass._open_request", opener_for(fixture())):
            self.invoke(*self.request_args("run"), "--confirm-network")
        status, output, error = self.invoke(*self.request_args("run"), "--cache-only")
        self.assertEqual(0, status, error)
        self.assertEqual("hit", json.loads(output)["cache_status"])

    def test_failed_source_does_not_touch_existing_database_or_result_file(self) -> None:
        database = self.directory / "untouched.sqlite3"
        with MediaHouseStore(database):
            pass
        before = database.read_bytes()
        path = self.directory / "review.json"
        save_result(path, self.result)
        before_file = path.read_bytes()
        with patch("media_house.discovery.overpass._open_request", side_effect=urllib.error.URLError("synthetic offline error")):
            status, output, error = self.invoke(*self.request_args("run"), "--confirm-network", "--output", str(path))
        self.assertEqual(2, status)
        self.assertEqual("", output)
        self.assertIn("network_error", error)
        self.assertEqual(before, database.read_bytes())
        self.assertEqual(before_file, path.read_bytes())

    def test_import_preview_gate_and_confirmed_readback(self) -> None:
        path = self.saved()
        status, output, error = self.invoke("discover", "import", str(path), "--researcher", "reviewer")
        self.assertEqual(0, status, error)
        self.assertTrue(json.loads(output)["preview_only"])
        self.assertFalse((self.directory / "untouched.sqlite3").exists())
        status, output, error = self.invoke("discover", "import", str(path), "--confirm-import")
        self.assertEqual(2, status)
        self.assertIn("--researcher", error)
        self.assertFalse((self.directory / "untouched.sqlite3").exists())
        status, output, error = self.invoke("discover", "import", str(path), "--confirm-import", "--researcher", "reviewer", "--source-id", "node/101")
        self.assertEqual(0, status, error)
        self.assertEqual(1, json.loads(output)["counts"]["new_leads"])
        with MediaHouseStore(self.directory / "untouched.sqlite3") as store:
            self.assertEqual("discovered", store.get_prospect(1)["verified_status"])
            self.assertEqual([], store.list_drafts())

    def test_invalid_saved_json_and_invalid_preview_are_nonzero(self) -> None:
        path = self.directory / "bad.json"
        atomic_write_json(path, fixture())
        status, output, error = self.invoke("discover", "import", str(path), "--confirm-import", "--researcher", "reviewer")
        self.assertEqual(2, status)
        self.assertIn("invalid_saved_result", error)
        payload = fixture()
        payload["elements"][0]["tags"]["website"] = "bad website"
        save_result(path, parse_overpass(payload, self.plan, retrieved_at=RETRIEVED_AT))
        status, output, error = self.invoke("discover", "import", str(path))
        self.assertEqual(2, status)
        self.assertFalse(json.loads(output)["importable"])
        self.assertIn("invalid_import", error)
        self.assertFalse((self.directory / "untouched.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
