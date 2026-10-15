from __future__ import annotations

import errno
import os
import platform
import socket
import sqlite3
import stat
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

import media_house.finder.linux_storage as storage
from media_house.finder.errors import FinderError


class _BoundaryProxy:
    """Delegate real descriptor work while injecting one named syscall fault."""

    def __init__(self, real: object, database: Path) -> None:
        self._real = real
        self.database = database
        self.openat2_calls = 0
        self.link_calls = 0
        self.fsync_calls = 0
        self.tmpfile_fds: list[int] = []
        self.stage_fd: int | None = None
        self.delete_before_target_open = False
        self.fail_openat2 = False
        self.fail_link = False
        self.fail_write = False
        self.short_write = False
        self.fail_fsync_call: int | None = None
        self.fail_probe_unlink = False
        self.create_target_after_probe = False
        self.fail_stage_close = False
        self.fail_parent_close = False
        self.substitute_parent_after_link = False
        self.substituted_parent: Path | None = None
        self._stage_close_failed = False
        self.parent_fd: int | None = None

    def open_root(self) -> int:
        return self._real.open_root()  # type: ignore[attr-defined]

    def openat2(self, dirfd: int, name: str, flags: int, *, mode: int = 0, resolve: int) -> int:
        self.openat2_calls += 1
        if self.fail_openat2:
            raise OSError(errno.ENOSYS, "openat2 unavailable")
        is_target_open = name == self.database.name and flags == (
            os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW
        )
        if self.delete_before_target_open and is_target_open and self.database.exists():
            self.database.unlink()
            self.delete_before_target_open = False
        fd = self._real.openat2(dirfd, name, flags, mode=mode, resolve=resolve)  # type: ignore[attr-defined]
        if flags == (os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW):
            self.parent_fd = fd
        if (flags & os.O_TMPFILE) == os.O_TMPFILE:
            self.tmpfile_fds.append(fd)
            if len(self.tmpfile_fds) == 2:
                self.stage_fd = fd
        return fd

    def link_empty(self, source_fd: int, parent_fd: int, basename: str) -> None:
        self.link_calls += 1
        if self.fail_link and self.link_calls >= 2:
            raise OSError(errno.EIO, "injected link failure")
        result = self._real.link_empty(source_fd, parent_fd, basename)  # type: ignore[attr-defined]
        if self.substitute_parent_after_link and self.link_calls >= 2:
            parent = self.database.parent
            backup = parent.with_name(parent.name + "-old")
            parent.rename(backup)
            parent.mkdir()
            self.substituted_parent = backup
            self.substitute_parent_after_link = False
        return result

    def close(self, fd: int) -> None:
        if self.fail_parent_close and fd == self.parent_fd:
            self.fail_parent_close = False
            raise OSError(errno.EIO, "injected parent descriptor cleanup failure")
        if self.fail_stage_close and fd == self.stage_fd and not self._stage_close_failed:
            self._stage_close_failed = True
            self._real.close(fd)  # type: ignore[attr-defined]
            raise OSError(errno.EIO, "injected post-install close failure")
        return self._real.close(fd)  # type: ignore[attr-defined]

    def fsync(self, fd: int) -> None:
        self.fsync_calls += 1
        if self.fail_fsync_call == self.fsync_calls:
            raise OSError(errno.EIO, "injected fsync failure")
        return self._real.fsync(fd)  # type: ignore[attr-defined]

    def fstat(self, fd: int) -> os.stat_result:
        return self._real.fstat(fd)  # type: ignore[attr-defined]

    def read(self, fd: int, size: int, offset: int) -> bytes:
        return self._real.read(fd, size, offset)  # type: ignore[attr-defined]

    def write(self, fd: int, payload: bytes) -> int:
        if self.fail_write:
            raise OSError(errno.EIO, "injected write failure")
        if self.short_write:
            self.short_write = False
            partial = payload[:-1]
            if partial:
                self._real.write(fd, partial)  # type: ignore[attr-defined]
            return len(partial)
        return self._real.write(fd, payload)  # type: ignore[attr-defined]

    def unlink(self, parent_fd: int, basename: str) -> None:
        if self.fail_probe_unlink:
            self.fail_probe_unlink = False
            raise OSError(errno.EIO, "injected probe unlink failure")
        result = self._real.unlink(parent_fd, basename)  # type: ignore[attr-defined]
        if self.create_target_after_probe and not self.database.exists():
            self.database.write_bytes(b"target appeared during capability preflight")
            self.create_target_after_probe = False
        return result


class FinderLinuxStorageTests(unittest.TestCase):
    """Offline syscall-boundary tests for the Linux Finder image backend."""

    @classmethod
    def setUpClass(cls) -> None:
        if sys.platform != "linux" or platform.machine() not in {"x86_64", "aarch64"}:
            raise unittest.SkipTest("Linux x86_64/aarch64 storage backend")
        try:
            storage._LinuxSyscalls()
            with tempfile.TemporaryDirectory(prefix="media-house-finder-linux-capability-") as directory:
                probe = storage.open_target(
                    Path(directory) / "capability.sqlite3",
                    workspace_root=Path(__file__).resolve().parents[1],
                )
                probe.close()
        except storage.StorageFailure as exc:
            raise unittest.SkipTest(f"Linux storage primitives unavailable: {exc}") from exc

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="media-house-finder-linux-")
        self.directory = Path(self.temp.name)
        self.database = self.directory / "finder.sqlite3"
        self.workspace = Path(__file__).resolve().parents[1]
        self.real_connect = sqlite3.connect
        self.connect_calls: list[str] = []
        self.connect_guard = patch.object(sqlite3, "connect", side_effect=self._guarded_connect)
        self.connect_guard.start()
        self.addCleanup(self.connect_guard.stop)
        self.socket_guard = patch.object(socket, "socket", side_effect=AssertionError("network is forbidden"))
        self.socket_guard.start()
        self.addCleanup(self.socket_guard.stop)
        self.connection_guard = patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("network is forbidden"),
        )
        self.connection_guard.start()
        self.addCleanup(self.connection_guard.stop)
        self.url_guard = patch.object(
            urllib.request,
            "urlopen",
            side_effect=AssertionError("network is forbidden"),
        )
        self.url_guard.start()
        self.addCleanup(self.url_guard.stop)
        self.addCleanup(self.temp.cleanup)

    def tearDown(self) -> None:
        self.assertTrue(all(target == ":memory:" for target in self.connect_calls))

    def _guarded_connect(self, target: object, *args: object, **kwargs: object) -> sqlite3.Connection:
        if target != ":memory:":
            raise AssertionError(f"filesystem SQLite target forbidden: {target!r}")
        self.connect_calls.append(str(target))
        return self.real_connect(target, *args, **kwargs)

    def image(self) -> bytes:
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE TABLE fabricated_finder (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT INTO fabricated_finder(value) VALUES ('offline')")
            connection.commit()
            return connection.serialize()
        finally:
            connection.close()

    def _proxy(self) -> _BoundaryProxy:
        proxy = _BoundaryProxy(storage._LinuxSyscalls(), self.database)
        self.factory_guard = patch.object(storage, "_new_syscalls", return_value=proxy)
        self.factory_guard.start()
        self.addCleanup(self.factory_guard.stop)
        return proxy

    def _open(self) -> storage._FileTarget:
        target = storage.open_target(self.database, workspace_root=self.workspace)
        self.assertFalse(target.memory)
        return target

    def _assert_single_link(self) -> None:
        self.assertTrue(self.database.exists())
        self.assertTrue(stat.S_ISREG(self.database.stat().st_mode))
        self.assertEqual(self.database.stat().st_nlink, 1)

    def test_memory_target_does_not_probe_filesystem(self) -> None:
        with patch.object(storage, "_new_syscalls", side_effect=AssertionError("memory must not probe")):
            target = storage.open_target(":memory:", workspace_root=self.workspace)
        self.assertTrue(target.memory)
        self.assertFalse(target.exists)
        self.assertIsNone(target.read_image())
        target.verify_entry()
        target.close()

    def test_unavailable_openat2_is_rejected_before_existing_target_open(self) -> None:
        payload = self.image()
        self.database.write_bytes(payload)
        proxy = self._proxy()
        proxy.fail_openat2 = True
        with self.assertRaises(storage.StorageFailure) as raised:
            storage.open_target(self.database, workspace_root=self.workspace)
        self.assertEqual(raised.exception.code, "directory_pin_failed")
        self.assertFalse(raised.exception.installed)
        self.assertEqual(self.database.read_bytes(), payload)
        self.assertGreaterEqual(proxy.openat2_calls, 1)

    def test_existing_file_deleted_before_final_open_is_not_treated_as_new(self) -> None:
        self.database.write_bytes(self.image())
        proxy = self._proxy()
        proxy.delete_before_target_open = True
        with self.assertRaises(storage.StorageFailure) as raised:
            storage.open_target(self.database, workspace_root=self.workspace, expected_exists=True)
        self.assertEqual(raised.exception.code, "target_disappeared")
        self.assertFalse(raised.exception.installed)
        self.assertFalse(self.database.exists())

    def test_target_appearing_during_capability_preflight_is_rejected(self) -> None:
        proxy = self._proxy()
        proxy.create_target_after_probe = True
        with self.assertRaises(storage.StorageFailure) as raised:
            storage.open_target(self.database, workspace_root=self.workspace)
        self.assertEqual(raised.exception.code, "target_race")
        self.assertFalse(raised.exception.installed)
        self.assertEqual(b"target appeared during capability preflight", self.database.read_bytes())

    def test_existing_fifo_is_rejected_without_blocking_on_database_open(self) -> None:
        os.mkfifo(self.database)
        with self.assertRaises(storage.StorageFailure) as raised:
            storage.open_target(self.database, workspace_root=self.workspace)
        self.assertEqual(raised.exception.code, "unsafe_database_target")
        self.assertTrue(self.database.is_fifo())

    def test_existing_descriptor_detects_deletion_after_pin(self) -> None:
        payload = self.image()
        self.database.write_bytes(payload)
        target = self._open()
        self.database.unlink()
        with self.assertRaises(storage.StorageFailure) as raised:
            target.read_image()
        self.assertIn(raised.exception.code, {"entry_missing", "entry_substituted"})
        self.assertFalse(raised.exception.installed)
        target.close()

    def test_existing_symlink_substitution_is_rejected(self) -> None:
        self.database.write_bytes(self.image())
        target = self._open()
        replacement = self.directory / "replacement.sqlite3"
        replacement.write_bytes(self.image())
        self.database.unlink()
        self.database.symlink_to(replacement.name)
        with self.assertRaises(storage.StorageFailure):
            target.verify_entry()
        target.close()

    def test_existing_hardlink_substitution_is_rejected(self) -> None:
        self.database.write_bytes(self.image())
        target = self._open()
        replacement = self.directory / "replacement.sqlite3"
        replacement.write_bytes(self.image())
        self.database.unlink()
        os.link(replacement, self.database)
        with self.assertRaises(storage.StorageFailure):
            target.verify_entry()
        target.close()

    def test_stable_in_place_target_mutation_is_rejected(self) -> None:
        self.database.write_bytes(self.image())
        target = self._open()
        with self.database.open("ab") as handle:
            handle.write(b"mutation")
        with self.assertRaises(storage.StorageFailure) as raised:
            target.read_image()
        self.assertEqual(raised.exception.code, "entry_substituted")
        target.close()

    def test_finder_error_with_descriptor_cleanup_failure_reports_canonical_artifact(self) -> None:
        proxy = self._proxy()
        proxy.fail_parent_close = True

        with self.assertRaises(FinderError) as raised:
            with storage.open_target(self.database, workspace_root=self.workspace) as target:
                target.install_image(self.image())
                self.assertTrue(target.installed)
                raise FinderError(
                    "synthetic Finder operation failed",
                    code="synthetic_operation_failed",
                    report={"error": "synthetic Finder operation failed"},
                )

        report = raised.exception.report
        assert report is not None
        self.assertTrue(report["cleanup_required"])
        self.assertTrue(report["installed"])
        self.assertTrue(report["database_write_performed"])
        self.assertFalse(report["preview_only"])
        self.assertTrue(self.database.exists())
        self.assertEqual(["pinned-parent"], report["cleanup_artifacts"])
        self.assertEqual(report["cleanup_artifacts"], report["artifacts"])
        self.assertIn("synthetic Finder operation failed", report["error"])
        self.assertIn("cleanup failure", report["error"])

    def test_parent_path_substitution_fails_before_install(self) -> None:
        parent = self.directory / "nested"
        parent.mkdir()
        self.database = parent / "finder.sqlite3"
        target = self._open()
        backup = self.directory / "nested-old"
        parent.rename(backup)
        parent.mkdir()
        with self.assertRaises(storage.StorageFailure) as raised:
            target.install_image(self.image())
        self.assertEqual(raised.exception.code, "namespace_changed")
        self.assertFalse(raised.exception.installed)
        target.close()
        self.assertFalse((backup / "finder.sqlite3").exists())
        self.assertFalse((parent / "finder.sqlite3").exists())

    def test_parent_path_substitution_after_link_reports_installed_state(self) -> None:
        parent = self.directory / "nested"
        parent.mkdir()
        self.database = parent / "finder.sqlite3"
        proxy = self._proxy()
        proxy.substitute_parent_after_link = True
        target = self._open()
        payload = self.image()
        with self.assertRaises(storage.StorageFailure) as raised:
            target.install_image(payload)
        self.assertEqual(raised.exception.code, "namespace_changed")
        self.assertTrue(raised.exception.installed)
        target.close()
        assert proxy.substituted_parent is not None
        self.assertEqual((proxy.substituted_parent / self.database.name).read_bytes(), payload)
        self.assertFalse((parent / self.database.name).exists())

    def test_target_exists_race_refuses_to_modify_sentinel(self) -> None:
        sentinel = self.image()
        target = self._open()
        self.database.write_bytes(sentinel)
        with self.assertRaises(storage.StorageFailure) as raised:
            target.install_image(self.image())
        self.assertFalse(raised.exception.installed)
        self.assertEqual(raised.exception.code, "target_exists")
        target.close()
        self.assertEqual(self.database.read_bytes(), sentinel)
        self.assertEqual(self.database.stat().st_nlink, 1)

    def test_new_install_rejects_final_symlink(self) -> None:
        target = self._open()
        replacement = self.directory / "symlink-sentinel.sqlite3"
        replacement.write_bytes(self.image())
        self.database.symlink_to(replacement.name)
        with self.assertRaises(storage.StorageFailure) as raised:
            target.install_image(self.image())
        self.assertFalse(raised.exception.installed)
        self.assertEqual(raised.exception.code, "target_exists")
        target.close()
        self.assertTrue(self.database.is_symlink())
        self.assertEqual(replacement.stat().st_nlink, 1)

    def test_new_install_rejects_final_hardlink(self) -> None:
        target = self._open()
        replacement = self.directory / "hardlink-sentinel.sqlite3"
        replacement.write_bytes(self.image())
        os.link(replacement, self.database)
        with self.assertRaises(storage.StorageFailure) as raised:
            target.install_image(self.image())
        self.assertFalse(raised.exception.installed)
        self.assertEqual(raised.exception.code, "target_exists")
        target.close()
        self.assertEqual(replacement.stat().st_nlink, 2)
        self.assertEqual(self.database.read_bytes(), replacement.read_bytes())

    def test_anonymous_stage_has_no_pathname_to_substitute(self) -> None:
        payload = self.image()
        target = self._open()
        target.install_image(payload)
        target.close()
        self.assertEqual({item.name for item in self.directory.iterdir()}, {self.database.name})
        self._assert_single_link()
        reopened = storage.open_target(self.database, workspace_root=self.workspace)
        try:
            self.assertEqual(reopened.read_image(), payload)
        finally:
            reopened.close()

    def test_link_failure_leaves_destination_absent(self) -> None:
        proxy = self._proxy()
        proxy.fail_link = True
        target = self._open()
        with self.assertRaises(storage.StorageFailure) as raised:
            target.install_image(self.image())
        self.assertFalse(raised.exception.installed)
        target.close()
        self.assertFalse(self.database.exists())
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_short_write_is_rejected_before_install(self) -> None:
        proxy = self._proxy()
        proxy.short_write = True
        target = self._open()
        with self.assertRaises(storage.StorageFailure) as raised:
            target.install_image(self.image())
        self.assertEqual(raised.exception.code, "short_image_write")
        self.assertFalse(raised.exception.installed)
        target.close()
        self.assertFalse(self.database.exists())

    def test_failed_write_is_rejected_before_install(self) -> None:
        proxy = self._proxy()
        proxy.fail_write = True
        target = self._open()
        with self.assertRaises(storage.StorageFailure) as raised:
            target.install_image(self.image())
        self.assertEqual(raised.exception.code, "image_write_failed")
        self.assertFalse(raised.exception.installed)
        target.close()
        self.assertFalse(self.database.exists())

    def test_stage_fsync_failure_is_preinstall(self) -> None:
        proxy = self._proxy()
        proxy.fail_fsync_call = 1
        target = self._open()
        with self.assertRaises(storage.StorageFailure) as raised:
            target.install_image(self.image())
        self.assertEqual(raised.exception.code, "image_fsync_failed")
        self.assertFalse(raised.exception.installed)
        target.close()
        self.assertFalse(self.database.exists())

    def test_parent_fsync_failure_retains_complete_single_link_image(self) -> None:
        payload = self.image()
        proxy = self._proxy()
        proxy.fail_fsync_call = 2
        target = self._open()
        with self.assertRaises(storage.StorageFailure) as raised:
            target.install_image(payload)
        self.assertEqual(raised.exception.code, "parent_fsync_failed")
        self.assertTrue(raised.exception.installed)
        self.assertFalse(raised.exception.durability_confirmed)
        target.close()
        self.assertEqual(self.database.read_bytes(), payload)
        self._assert_single_link()
        reopened = storage.open_target(self.database, workspace_root=self.workspace)
        try:
            self.assertEqual(reopened.read_image(), payload)
        finally:
            reopened.close()

    def test_capability_probe_unlink_failure_reports_recoverable_artifact(self) -> None:
        proxy = self._proxy()
        proxy.fail_probe_unlink = True
        with self.assertRaises(storage.StorageFailure) as raised:
            storage.open_target(self.database, workspace_root=self.workspace)
        self.assertEqual(raised.exception.code, "capability_probe_cleanup_failed")
        self.assertTrue(raised.exception.cleanup_required)
        artifacts = [self.directory / name for name in raised.exception.artifacts if name.endswith(".probe")]
        self.assertTrue(artifacts)
        for artifact in artifacts:
            if artifact.exists() or artifact.is_symlink():
                artifact.unlink()

    def test_postinstall_close_failure_reports_commit_and_next_run_is_usable(self) -> None:
        payload = self.image()
        proxy = self._proxy()
        proxy.fail_stage_close = True
        target = self._open()
        with self.assertRaises(storage.StorageFailure) as raised:
            target.install_image(payload)
        self.assertTrue(raised.exception.installed)
        self.assertTrue(raised.exception.cleanup_required)
        self.assertEqual(raised.exception.code, "descriptor_close_failed")
        target.close()
        self.assertEqual(self.database.read_bytes(), payload)
        self._assert_single_link()
        reopened = storage.open_target(self.database, workspace_root=self.workspace)
        try:
            self.assertEqual(reopened.read_image(), payload)
        finally:
            reopened.close()

    def test_inconsistent_existing_image_read_fails_closed(self) -> None:
        payload = self.image()
        self.database.write_bytes(payload)
        proxy = self._proxy()
        target = self._open()
        original_read = proxy.read
        read_calls = 0

        def inconsistent_read(fd: int, size: int, offset: int) -> bytes:
            nonlocal read_calls
            read_calls += 1
            result = original_read(fd, size, offset)
            if read_calls == 1:
                return result[:-1]
            return result

        proxy.read = inconsistent_read  # type: ignore[method-assign]
        with self.assertRaises(storage.StorageFailure) as raised:
            target.read_image()
        self.assertEqual(raised.exception.code, "inconsistent_image_read")
        target.close()


if __name__ == "__main__":
    unittest.main()
