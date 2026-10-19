from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from media_house.cli import MAX_REQUIREMENTS_INPUT_BYTES, _load_requirements_document
from media_house.errors import ValidationError
from media_house.store import MediaHouseStore
from tests.test_requirements import lead_row, requirements_document, write_csv


SIZE_ERROR = (
    "requirements JSON could not be read: "
    f"requirements JSON exceeds the {MAX_REQUIREMENTS_INPUT_BYTES}-byte limit"
)


class RequirementsInputTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.input_path = self.directory / "requirements.json"

    def test_read_is_binary_bounded_and_context_managed(self) -> None:
        stream = io.BytesIO(b'{"synthetic": true}')
        with (
            patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded reads are forbidden")),
            patch.object(Path, "open", return_value=stream) as open_file,
            patch.object(stream, "read", wraps=stream.read) as read,
        ):
            self.assertEqual({"synthetic": True}, _load_requirements_document(self.input_path))
            open_file.assert_called_once_with("rb")
            read.assert_called_once_with(MAX_REQUIREMENTS_INPUT_BYTES + 1)
        self.assertTrue(stream.closed)

    def test_valid_json_below_and_exactly_at_limit_is_accepted(self) -> None:
        payload = b'{"synthetic": true}'
        for size in (len(payload), MAX_REQUIREMENTS_INPUT_BYTES - 1, MAX_REQUIREMENTS_INPUT_BYTES):
            with self.subTest(size=size):
                self.input_path.write_bytes(payload.ljust(size, b" "))
                self.assertEqual({"synthetic": True}, _load_requirements_document(self.input_path))

    def test_limit_plus_one_and_larger_are_rejected_before_json_parsing(self) -> None:
        for size in (MAX_REQUIREMENTS_INPUT_BYTES + 1, MAX_REQUIREMENTS_INPUT_BYTES * 2):
            with self.subTest(size=size):
                self.input_path.write_bytes(b"x" * size)
                with patch("media_house.cli.json.loads") as loads:
                    with self.assertRaises(ValidationError) as raised:
                        _load_requirements_document(self.input_path)
                    loads.assert_not_called()
                self.assertEqual(SIZE_ERROR, str(raised.exception))

    def test_utf8_json_below_and_exactly_at_byte_limit_is_accepted(self) -> None:
        prefix, suffix = b'{"text":"', b'"}'
        character = "€"
        for size in (MAX_REQUIREMENTS_INPUT_BYTES - 1, MAX_REQUIREMENTS_INPUT_BYTES):
            with self.subTest(size=size):
                text = "x" * (size - len(prefix + suffix) - len(character.encode("utf-8"))) + character
                payload = prefix + text.encode("utf-8") + suffix
                self.assertEqual(size, len(payload))
                self.input_path.write_bytes(payload)
                self.assertEqual({"text": text}, _load_requirements_document(self.input_path))

    def test_oversize_utf8_split_at_read_bound_reports_size_before_decoding(self) -> None:
        prefix = b'{"text":"'
        payload = prefix + b"x" * (MAX_REQUIREMENTS_INPUT_BYTES - len(prefix)) + "€".encode("utf-8") + b'"}'
        self.input_path.write_bytes(payload)
        with patch("media_house.cli.json.loads") as loads:
            with self.assertRaises(ValidationError) as raised:
                _load_requirements_document(self.input_path)
            loads.assert_not_called()
        self.assertEqual(SIZE_ERROR, str(raised.exception))

    def test_invalid_json_utf8_constants_and_non_objects_remain_validation_errors(self) -> None:
        for payload in (b"{", b'{"text":"\xff"}', b'{"value":NaN}', b'{"value":Infinity}', b"[]"):
            with self.subTest(payload=payload):
                self.input_path.write_bytes(payload)
                with self.assertRaises(ValidationError):
                    _load_requirements_document(self.input_path)

    def test_cli_oversize_is_clean_and_does_not_mutate_requirements_or_audit(self) -> None:
        database = self.directory / "synthetic.sqlite3"
        with MediaHouseStore(database) as store:
            store.import_csv(write_csv(self.directory, [lead_row()]), researcher="human-researcher")
            meeting = store.record_meeting(
                store.list_prospects()[0]["id"],
                meeting_at="2026-10-20T10:00:00Z",
                notes="Synthetic requirements input test",
                notes_permission="permission_granted",
                recording_policy="not_recorded",
                actor="founder",
            )
            store.capture_requirements(int(meeting["id"]), requirements_document(), actor="founder")
            before = list(store.connection.iterdump())
        payload = json.dumps(requirements_document()).encode("utf-8")
        self.input_path.write_bytes(payload.ljust(MAX_REQUIREMENTS_INPUT_BYTES + 1, b" "))

        result = subprocess.run(
            [
                sys.executable, "-m", "media_house.cli", "--db", str(database),
                "requirements-capture", str(meeting["id"]), str(self.input_path), "--actor", "founder",
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertEqual(f"error: {SIZE_ERROR}\n", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        with MediaHouseStore(database) as store:
            self.assertEqual(before, list(store.connection.iterdump()))


if __name__ == "__main__":
    unittest.main()
