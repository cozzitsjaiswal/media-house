"""Descriptor-pinned, Linux-only storage for disposable Finder SQLite images.

SQLite is only ever connected to ``:memory:``.  Existing files are immutable
snapshots; new images get their first name via linkat(AT_EMPTY_PATH).  There is
deliberately no path-based or named-staging fallback.  Namespace observations
detect interference, but do not replace the kernel's descriptor boundaries or
promise protection against privileged tampering after the operation.
"""

from __future__ import annotations

import ctypes
import errno
import os
import platform
import re
import sqlite3
import stat
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

MAX_IMAGE_BYTES = 16 * 1024 * 1024
_IO_CHUNK = 64 * 1024
_AT_EMPTY_PATH = 0x1000
_RESOLVE_NO_XDEV = 0x01
_RESOLVE_NO_MAGICLINKS = 0x02
_RESOLVE_NO_SYMLINKS = 0x04
_RESOLVE_BENEATH = 0x08
_ANCHOR_RESOLVE = _RESOLVE_BENEATH | _RESOLVE_NO_MAGICLINKS | _RESOLVE_NO_SYMLINKS
_CHILD_RESOLVE = _ANCHOR_RESOLVE | _RESOLVE_NO_XDEV
_SYSCALL_OPENAT2 = {"x86_64": 437, "aarch64": 437}
_AT_FDCWD = getattr(os, "AT_FDCWD", -100)
_ANONYMOUS_FILE_MODE = 0o600

__all__ = ["MAX_IMAGE_BYTES", "StorageFailure", "SyscallBoundary", "open_target"]


class StorageFailure(Exception):
    """A failure receipt, including whether this operation installed an image."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "storage_failure",
        installed: bool = False,
        cleanup_required: bool = False,
        durability_confirmed: bool = False,
        state: str = "pre_install_failed",
        artifacts: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.installed = installed
        self.cleanup_required = cleanup_required
        self.durability_confirmed = durability_confirmed
        self.state = state
        self.artifacts = artifacts
        self.cleanup_errors: tuple[str, ...] = ()

    def _add_cleanup(self, other: StorageFailure) -> None:
        """Keep the primary error/code while also retaining cleanup evidence."""
        self.installed = self.installed or other.installed
        self.durability_confirmed = self.durability_confirmed or other.durability_confirmed
        self.cleanup_required = self.cleanup_required or other.cleanup_required
        self.artifacts += other.artifacts
        self.cleanup_errors += (str(other),) + other.cleanup_errors
        self.args = (f"{self}; cleanup failure: {other}",)
        if self.installed and not self.state.startswith("installed_"):
            self.state = other.state


def _attach_failure_report(report: dict[str, Any], failure: StorageFailure) -> None:
    """Attach the recovery-relevant storage receipt fields to an app report."""

    artifacts = list(failure.artifacts)
    report["error"] = f"{report.get('error', str(failure))}; cleanup failure: {failure}"
    report["rejected"] = True
    report["importable"] = False
    report["database_write_performed"] = bool(failure.installed)
    report["preview_only"] = not bool(failure.installed)
    report["imported"] = report.get("imported", 0) if failure.installed else 0
    report["installation_state"] = failure.state
    report["cleanup_required"] = bool(failure.cleanup_required)
    report["storage_code"] = failure.code
    report["installed"] = bool(failure.installed)
    report["durability_confirmed"] = bool(failure.durability_confirmed)
    report["cleanup_artifacts"] = artifacts
    # Preserve the pre-existing field for callers that consumed the alias
    # before cleanup_artifacts became the canonical receipt field.
    report["artifacts"] = artifacts
    report["cleanup_errors"] = list(failure.cleanup_errors)


class _OpenHow(ctypes.Structure):
    _fields_ = [("flags", ctypes.c_uint64), ("mode", ctypes.c_uint64), ("resolve", ctypes.c_uint64)]


class _LinuxSyscalls:
    """Narrow syscall boundaries; tests inject here, never with sleeps."""

    def __init__(self) -> None:
        machine = platform.machine()
        if (
            sys.platform != "linux"
            or machine not in _SYSCALL_OPENAT2
            or ctypes.sizeof(ctypes.c_void_p) != 8
            or ctypes.sizeof(ctypes.c_long) != 8
            or sys.byteorder != "little"
        ):
            raise StorageFailure("Linux x86_64/aarch64 ABI required", code="unsupported_storage")
        for name in ("O_TMPFILE", "O_PATH", "O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC"):
            if not hasattr(os, name):
                raise StorageFailure(f"Required Linux flag {name} is unavailable", code="unsupported_storage")
        try:
            self._libc = ctypes.CDLL(None, use_errno=True)
            self._syscall = self._libc.syscall
            self._syscall.restype = ctypes.c_long
            self._linkat = self._libc.linkat
            self._linkat.restype = ctypes.c_int
            self._linkat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        except (AttributeError, OSError) as exc:
            raise StorageFailure(f"Required libc primitives unavailable: {exc}", code="unsupported_storage") from exc
        self._openat2_number = _SYSCALL_OPENAT2[machine]

    def open_root(self) -> int:
        # Fixed '/' bootstraps a dirfd. BENEATH cannot be used with an absolute
        # path; every subsequent anchor/parent/target open uses openat2.
        return self.openat2(
            _AT_FDCWD,
            "/",
            os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            resolve=_RESOLVE_NO_MAGICLINKS | _RESOLVE_NO_SYMLINKS,
        )

    def openat2(self, dirfd: int, name: str, flags: int, *, mode: int = 0, resolve: int) -> int:
        how = _OpenHow(flags, mode, resolve)
        result = self._syscall(
            ctypes.c_long(self._openat2_number), ctypes.c_int(dirfd), ctypes.c_char_p(os.fsencode(name)),
            ctypes.byref(how), ctypes.c_size_t(ctypes.sizeof(how)),
        )
        if result == -1:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), name)
        return int(result)

    def link_empty(self, source_fd: int, parent_fd: int, basename: str) -> None:
        if self._linkat(source_fd, b"", parent_fd, os.fsencode(basename), _AT_EMPTY_PATH) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), basename)

    @staticmethod
    def close(fd: int) -> None:
        os.close(fd)

    @staticmethod
    def fsync(fd: int) -> None:
        os.fsync(fd)

    @staticmethod
    def fstat(fd: int) -> os.stat_result:
        return os.fstat(fd)

    @staticmethod
    def read(fd: int, size: int, offset: int) -> bytes:
        return os.pread(fd, size, offset)

    @staticmethod
    def write(fd: int, payload: bytes) -> int:
        return os.write(fd, payload)

    @staticmethod
    def unlink(parent_fd: int, basename: str) -> None:
        os.unlink(basename, dir_fd=parent_fd)


# Public enough for deterministic syscall-boundary injection in the isolated
# backend tests; callers should use open_target rather than this boundary.
SyscallBoundary = _LinuxSyscalls


def _new_syscalls() -> _LinuxSyscalls:
    """Factory kept as a small injection boundary for deterministic tests."""
    return _LinuxSyscalls()


def _failure(
    message: str,
    *,
    code: str,
    installed: bool = False,
    cleanup_required: bool = False,
    durability_confirmed: bool = False,
    state: str | None = None,
    artifacts: tuple[str, ...] = (),
) -> StorageFailure:
    if state is None:
        state = "installed_failure" if installed else "pre_install_failed"
    return StorageFailure(
        message,
        code=code,
        installed=installed,
        cleanup_required=cleanup_required,
        durability_confirmed=durability_confirmed,
        state=state,
        artifacts=artifacts,
    )


def _exception_failure(
    exc: BaseException,
    *,
    code: str,
    installed: bool = False,
    cleanup_required: bool = False,
    durability_confirmed: bool = False,
    state: str | None = None,
    artifacts: tuple[str, ...] = (),
) -> StorageFailure:
    return _failure(
        str(exc) or exc.__class__.__name__,
        code=code,
        installed=installed,
        cleanup_required=cleanup_required,
        durability_confirmed=durability_confirmed,
        state=state,
        artifacts=artifacts,
    )


def _sqlite_image_available() -> bool:
    return callable(getattr(sqlite3.Connection, "serialize", None)) and callable(
        getattr(sqlite3.Connection, "deserialize", None)
    )


def _path_text(value: str | os.PathLike[str]) -> str:
    try:
        raw = os.fspath(value)
    except TypeError as exc:
        raise _failure("database must be a filesystem path", code="invalid_database_path") from exc
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise _failure("database must be a nonempty text path", code="invalid_database_path")
    return raw


def _reject_uri_and_traversal(raw: str) -> None:
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", raw):
        raise _failure("database must be a filesystem path, not a URI", code="invalid_database_path")
    if any(part == ".." for part in raw.split("/")):
        raise _failure("database path traversal is not permitted", code="unsafe_database_path")


def _absolute_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    # Path strips '.' components, which is harmless; '..' was rejected from
    # the original spelling above. Do not resolve: symlink identity belongs to
    # the kernel openat2 boundary, not to a pathname preflight.
    return path


def _parts(path: Path) -> tuple[str, ...]:
    if path.anchor != "/":
        raise _failure("database path must be absolute on Linux", code="invalid_database_path")
    return tuple(part for part in path.parts[1:] if part not in {"", "."})


def _is_descendant(path: Path, ancestor: Path) -> bool:
    try:
        path.relative_to(ancestor)
        return True
    except ValueError:
        return False


def _validated_paths(database: str | os.PathLike[str], workspace_root: Path) -> tuple[Path, Path, Path, tuple[str, ...], tuple[str, ...]]:
    raw = _path_text(database)
    _reject_uri_and_traversal(raw)
    path = _absolute_path(raw)
    temp_raw = _path_text(tempfile.gettempdir())
    workspace_raw = _path_text(workspace_root)
    _reject_uri_and_traversal(temp_raw)
    _reject_uri_and_traversal(workspace_raw)
    temp_root = _absolute_path(temp_raw)
    workspace = _absolute_path(workspace_raw)
    temp_parts = _parts(temp_root)
    path_parts = _parts(path)
    if not temp_parts or len(path_parts) <= len(temp_parts) or path_parts[: len(temp_parts)] != temp_parts:
        raise _failure("database must be below the operating-system temporary directory", code="unsafe_database_path")
    if _is_descendant(path, workspace):
        raise _failure("database may not be below the workspace", code="unsafe_database_path")
    parent = path.parent
    parent_parts = _parts(parent)
    if parent_parts[: len(temp_parts)] != temp_parts:
        raise _failure("database parent must be below the operating-system temporary directory", code="unsafe_database_path")
    return path, parent, temp_root, temp_parts, tuple(parent_parts[len(temp_parts) :])


def _identity_matches(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _target_identity_matches(left: os.stat_result, right: os.stat_result) -> bool:
    """Match a target inode and its change generation observable in Python.

    Device/inode alone can match after deletion followed by inode reuse.  The
    change-time check is not an authority boundary—the retained descriptor is
    that boundary—but it makes namespace substitution fail closed before a
    replacement can be mistaken for the approved target.
    """

    return _identity_matches(left, right) and left.st_ctime_ns == right.st_ctime_ns


def _regular_single_link(stat_result: os.stat_result, *, zero_ok: bool = False) -> bool:
    return (
        stat.S_ISREG(stat_result.st_mode)
        and stat_result.st_nlink == 1
        and (zero_ok or stat_result.st_size > 0)
    )


def _same_image_stat(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        _identity_matches(left, right)
        and left.st_mode == right.st_mode
        and left.st_nlink == right.st_nlink
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
    )


def _close_fd(boundary: _LinuxSyscalls, fd: int | None, *, label: str, installed: bool, durability: bool) -> StorageFailure | None:
    if fd is None:
        return None
    try:
        boundary.close(fd)
    except BaseException as exc:
        return _exception_failure(
            exc,
            code="descriptor_close_failed",
            installed=installed,
            cleanup_required=True,
            durability_confirmed=durability,
            state="installed_cleanup_failed" if installed else "pre_install_cleanup_failed",
            artifacts=(label,),
        )
    return None


def _close_many(
    boundary: _LinuxSyscalls,
    descriptors: list[tuple[str, int]],
    *,
    installed: bool = False,
    durability: bool = False,
) -> StorageFailure | None:
    failure: StorageFailure | None = None
    for label, fd in descriptors:
        current = _close_fd(boundary, fd, label=label, installed=installed, durability=durability)
        if current is not None:
            if failure is None:
                failure = current
            else:
                failure._add_cleanup(current)
    return failure


def _walk_from_root(
    boundary: _LinuxSyscalls,
    root_fd: int,
    components: tuple[str, ...],
    *,
    resolve: int,
    close_root_on_error: bool = True,
    final_flags: int | None = None,
) -> tuple[int, list[tuple[str, int]]]:
    current = root_fd
    opened: list[tuple[str, int]] = [("root", root_fd)]
    try:
        for component in components:
            if component in {"", ".", ".."} or "/" in component or "\x00" in component:
                raise _failure("invalid directory component", code="unsafe_database_path")
            flags = (
                final_flags
                if final_flags is not None and component == components[-1]
                else os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
            )
            next_fd = boundary.openat2(
                current,
                component,
                flags,
                resolve=resolve,
            )
            opened.append((f"directory:{component}", next_fd))
            current = next_fd
    except StorageFailure as primary:
        cleanup = _close_many(
            boundary,
            list(reversed(opened if close_root_on_error else opened[1:])),
        )
        if cleanup is not None:
            primary._add_cleanup(cleanup)
        raise
    except BaseException as exc:
        primary = _exception_failure(exc, code="directory_pin_failed")
        cleanup = _close_many(
            boundary,
            list(reversed(opened if close_root_on_error else opened[1:])),
        )
        if cleanup is not None:
            primary._add_cleanup(cleanup)
        raise primary from exc
    return current, opened


def _close_intermediate(
    boundary: _LinuxSyscalls,
    opened: list[tuple[str, int]],
    *,
    keep_fd: int,
) -> StorageFailure | None:
    return _close_many(
        boundary,
        [(label, fd) for label, fd in opened if fd != keep_fd],
    )


def _entry_metadata(
    boundary: _LinuxSyscalls,
    parent_fd: int,
    basename: str,
    *,
    installed: bool = False,
    durability: bool = False,
) -> os.stat_result:
    entry_fd: int | None = None
    primary: BaseException | None = None
    result: os.stat_result | None = None
    try:
        entry_fd = boundary.openat2(
            parent_fd,
            basename,
            os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW,
            resolve=_CHILD_RESOLVE,
        )
        result = boundary.fstat(entry_fd)
    except BaseException as exc:
        primary = exc
    finally:
        if entry_fd is not None:
            # Metadata probes never own the database bytes. A close failure is
            # surfaced by callers that require a proof, rather than retried.
            try:
                boundary.close(entry_fd)
            except BaseException as exc:
                cleanup = _exception_failure(
                    exc,
                    code="metadata_close_failed",
                    installed=installed,
                    cleanup_required=True,
                    durability_confirmed=durability,
                    state="installed_cleanup_failed" if installed else "pre_install_cleanup_failed",
                )
                if primary is None:
                    primary = cleanup
                elif isinstance(primary, StorageFailure):
                    primary._add_cleanup(cleanup)
                else:
                    wrapped = _exception_failure(primary, code="metadata_probe_failed")
                    wrapped._add_cleanup(cleanup)
                    primary = wrapped
    if primary is not None:
        if isinstance(primary, StorageFailure):
            raise primary
        raise primary
    assert result is not None
    return result


class _MemoryTarget:
    memory = True
    exists = False
    installed = False
    state = "memory"

    def __enter__(self) -> "_MemoryTarget":
        return self

    def __exit__(self, exc_type: Any, exc_value: BaseException | None, traceback: Any) -> bool:
        self.close()
        return False

    def read_image(self) -> bytes | None:
        return None

    def verify_entry(self) -> None:
        return None

    def install_image(self, image: bytes) -> None:
        raise _failure("memory targets are owned by the SQLite caller", code="memory_target")

    def close(self) -> None:
        return None


class _FileTarget:
    memory = False

    def __init__(
        self,
        *,
        boundary: _LinuxSyscalls,
        database: Path,
        parent: Path,
        temp_root: Path,
        temp_stat: os.stat_result,
        parent_fd: int,
        parent_stat: os.stat_result,
        target_fd: int | None,
        target_stat: os.stat_result | None,
        temp_parts: tuple[str, ...],
    ) -> None:
        self._boundary = boundary
        self._database = database
        self._parent = parent
        self._temp_root = temp_root
        self._temp_parts = temp_parts
        self._temp_stat = temp_stat
        self._parent_stat = parent_stat
        self._parent_fd: int | None = parent_fd
        self._target_fd: int | None = target_fd
        self._target_stat = target_stat
        self._stage_fd: int | None = None
        self._installed_stat: os.stat_result | None = None
        self._close_failures: StorageFailure | None = None
        self._closed = False
        self._installed = False
        self._durability_confirmed = False
        self._target_name = database.name
        self.exists = target_fd is not None
        self.installed = False
        self.state = "ready_existing" if self.exists else "ready_new"

    def __enter__(self) -> "_FileTarget":
        return self

    def __exit__(self, exc_type: Any, exc_value: BaseException | None, traceback: Any) -> bool:
        try:
            self.close()
        except StorageFailure as cleanup:
            if isinstance(exc_value, StorageFailure):
                exc_value._add_cleanup(cleanup)
                return False
            report = getattr(exc_value, "report", None)
            if isinstance(report, dict):
                _attach_failure_report(report, cleanup)
                return False
            if exc_value is not None:
                raise cleanup from exc_value
            raise
        return False

    def _require_open(self) -> int:
        if self._closed or self._parent_fd is None:
            raise _failure("storage target is closed", code="target_closed", installed=self._installed, state=self.state)
        return self._parent_fd

    def _namespace_matches(self) -> bool:
        try:
            current_temp = os.lstat(self._temp_root)
            current_parent = os.lstat(self._parent)
        except OSError:
            return False
        return _identity_matches(current_temp, self._temp_stat) and _identity_matches(current_parent, self._parent_stat)

    def _require_namespace(self, *, installed: bool = False, durability: bool = False) -> None:
        if not self._namespace_matches():
            raise _failure(
                "temporary-directory namespace changed during storage operation",
                code="namespace_changed",
                installed=installed,
                durability_confirmed=durability,
                state="installed_namespace_changed" if installed else "pre_install_failed",
            )

    def _metadata_matches_target(self) -> bool:
        if self._target_fd is None or self._target_stat is None or self._parent_fd is None:
            return False
        try:
            entry_stat = _entry_metadata(self._boundary, self._parent_fd, self._target_name)
        except StorageFailure:
            raise
        except OSError:
            return False
        return _same_image_stat(entry_stat, self._target_stat) and stat.S_ISREG(entry_stat.st_mode)

    def _require_target_absent(self) -> None:
        """Verify that an initially absent target has not appeared by name."""

        try:
            _entry_metadata(self._boundary, self._require_open(), self._target_name)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return
            raise _exception_failure(
                exc,
                code="target_observation_failed",
                installed=self._installed,
                durability_confirmed=self._durability_confirmed,
                state="installed_namespace_changed" if self._installed else "pre_install_failed",
            ) from exc
        raise _failure(
            "database target appeared during the storage operation",
            code="target_race",
            installed=self._installed,
            durability_confirmed=self._durability_confirmed,
            state="installed_namespace_changed" if self._installed else "pre_install_failed",
        )

    def verify_entry(self) -> None:
        self._require_open()
        self._require_namespace(installed=self._installed, durability=self._durability_confirmed)
        if self._target_fd is None or self._target_stat is None:
            if self._installed:
                try:
                    entry_stat = _entry_metadata(self._boundary, self._require_open(), self._target_name)
                except BaseException as exc:
                    if isinstance(exc, StorageFailure):
                        raise exc
                    raise _exception_failure(
                        exc,
                        code="installed_entry_missing",
                        installed=True,
                        durability_confirmed=self._durability_confirmed,
                        state="installed_namespace_changed",
                    ) from exc
                if (
                    self._installed_stat is None
                    or not _same_image_stat(entry_stat, self._installed_stat)
                    or not stat.S_ISREG(entry_stat.st_mode)
                    or entry_stat.st_nlink != 1
                ):
                    raise _failure(
                        "installed target is not a single-link regular file",
                        code="installed_entry_invalid",
                        installed=True,
                        durability_confirmed=self._durability_confirmed,
                        state="installed_namespace_changed",
                    )
            else:
                self._require_target_absent()
            return
        try:
            entry_stat = _entry_metadata(self._boundary, self._require_open(), self._target_name)
        except StorageFailure:
            raise
        except BaseException as exc:
            raise _exception_failure(
                exc,
                code="entry_missing",
                installed=False,
                state="pre_install_failed",
            ) from exc
        try:
            descriptor_stat = self._boundary.fstat(self._target_fd)
        except BaseException as exc:
            raise _exception_failure(exc, code="target_stat_failed") from exc
        if not _same_image_stat(descriptor_stat, self._target_stat):
            raise _failure("database descriptor changed after it was opened", code="entry_substituted")
        if not _same_image_stat(entry_stat, self._target_stat):
            raise _failure("database entry was deleted or substituted", code="entry_substituted")
        if not stat.S_ISREG(entry_stat.st_mode) or entry_stat.st_nlink != 1:
            raise _failure("database entry is no longer a single-link regular file", code="entry_substituted")

    def read_image(self) -> bytes | None:
        self.verify_entry()
        if self._target_fd is None or self._target_stat is None:
            return None
        self._require_open()
        try:
            before = self._boundary.fstat(self._target_fd)
        except StorageFailure:
            raise
        except BaseException as exc:
            raise _exception_failure(exc, code="image_stat_failed") from exc
        if not _regular_single_link(before) or before.st_size > MAX_IMAGE_BYTES:
            raise _failure("database image is empty, linked, nonregular, or too large", code="invalid_database_image")
        if self._target_stat is not None and not _same_image_stat(self._target_stat, before):
            raise _failure(
                "database image changed after the target was opened",
                code="inconsistent_image_read",
            )
        expected = before.st_size
        chunks: list[bytes] = []
        offset = 0
        while offset < expected:
            amount = min(_IO_CHUNK, expected - offset)
            try:
                chunk = self._boundary.read(self._target_fd, amount, offset)
            except StorageFailure:
                raise
            except BaseException as exc:
                raise _exception_failure(exc, code="image_read_failed") from exc
            if not isinstance(chunk, (bytes, bytearray)) or len(chunk) != amount:
                raise _failure("database image read was short or inconsistent", code="inconsistent_image_read")
            chunks.append(bytes(chunk))
            offset += len(chunk)
        try:
            after = self._boundary.fstat(self._target_fd)
        except StorageFailure:
            raise
        except BaseException as exc:
            raise _exception_failure(exc, code="image_stat_failed") from exc
        if not _same_image_stat(before, after):
            raise _failure("database image changed while it was being read", code="inconsistent_image_read")
        self.verify_entry()
        return b"".join(chunks)

    def _close_stage(self) -> StorageFailure | None:
        if self._stage_fd is None:
            return None
        fd = self._stage_fd
        self._stage_fd = None
        return _close_fd(
            self._boundary,
            fd,
            label="anonymous-stage",
            installed=self._installed,
            durability=self._durability_confirmed,
        )

    def install_image(self, image: bytes) -> None:
        self._require_open()
        if self._target_fd is not None or self._installed:
            raise _failure("existing Finder databases are read-only and cannot be installed", code="target_exists")
        if not isinstance(image, bytes):
            raise _failure("serialized image must be bytes", code="invalid_image")
        if not image or len(image) > MAX_IMAGE_BYTES:
            raise _failure("serialized image is empty or too large", code="invalid_image")
        self._require_namespace()
        stage: int | None = None
        primary: StorageFailure | None = None
        try:
            try:
                stage = self._boundary.openat2(
                    self._require_open(),
                    ".",
                    os.O_TMPFILE | os.O_RDWR | os.O_CLOEXEC,
                    mode=_ANONYMOUS_FILE_MODE,
                    resolve=_CHILD_RESOLVE,
                )
                self._stage_fd = stage
            except BaseException as exc:
                raise _exception_failure(exc, code="anonymous_stage_failed") from exc

            try:
                stage_stat = self._boundary.fstat(stage)
            except BaseException as exc:
                raise _exception_failure(exc, code="anonymous_stage_stat_failed") from exc
            if not stat.S_ISREG(stage_stat.st_mode) or stage_stat.st_nlink != 0:
                raise _failure("anonymous staging inode was not an unlinked regular file", code="anonymous_stage_invalid")

            offset = 0
            while offset < len(image):
                try:
                    written = self._boundary.write(stage, image[offset:])
                except BaseException as exc:
                    raise _exception_failure(exc, code="image_write_failed") from exc
                if not isinstance(written, int) or written != len(image) - offset:
                    raise _failure("serialized image write was short or invalid", code="short_image_write")
                offset += written
            try:
                self._boundary.fsync(stage)
            except BaseException as exc:
                raise _exception_failure(exc, code="image_fsync_failed") from exc

            self._require_namespace()
            try:
                self._boundary.link_empty(stage, self._require_open(), self._target_name)
            except BaseException as exc:
                code = "target_exists" if isinstance(exc, OSError) and exc.errno == errno.EEXIST else "target_install_failed"
                raise _exception_failure(exc, code=code) from exc

            # linkat succeeded: this is a committed, complete one-link image.
            self._installed = True
            self.installed = True
            self.exists = True
            try:
                stage_after = self._boundary.fstat(stage)
            except BaseException as exc:
                raise _exception_failure(
                    exc,
                    code="installed_entry_stat_failed",
                    installed=True,
                    state="installed_namespace_changed",
                ) from exc
            if not stat.S_ISREG(stage_after.st_mode) or stage_after.st_nlink != 1:
                raise _failure(
                    "installed target does not have exactly one link",
                    code="installed_entry_invalid",
                    installed=True,
                    state="installed_namespace_changed",
                )
            self._installed_stat = stage_after
            try:
                self._boundary.fsync(self._require_open())
            except BaseException as exc:
                raise _exception_failure(
                    exc,
                    code="parent_fsync_failed",
                    installed=True,
                    state="installed_durability_uncertain",
                ) from exc
            self._durability_confirmed = True
            self._require_namespace(installed=True, durability=True)
            self.verify_entry()
            self.state = "installed"
        except StorageFailure as exc:
            primary = exc
        finally:
            cleanup = self._close_stage()
            if cleanup is not None:
                if primary is None:
                    primary = cleanup
                else:
                    primary._add_cleanup(cleanup)
        if primary is not None:
            self.state = primary.state
            raise primary

    def close(self) -> None:
        if self._closed:
            if self._close_failures is not None:
                raise self._close_failures
            return
        self._closed = True
        descriptors: list[tuple[str, int]] = []
        if self._stage_fd is not None:
            descriptors.append(("anonymous-stage", self._stage_fd))
            self._stage_fd = None
        if self._target_fd is not None:
            descriptors.append(("existing-database", self._target_fd))
            self._target_fd = None
        if self._parent_fd is not None:
            descriptors.append(("pinned-parent", self._parent_fd))
            self._parent_fd = None
        failure = _close_many(
            self._boundary,
            descriptors,
            installed=self._installed,
            durability=self._durability_confirmed,
        )
        if failure is not None:
            self._close_failures = failure
            self.state = failure.state
            raise failure
        self.state = "closed" if not self._installed else "installed"


def _probe_capabilities(boundary: _LinuxSyscalls, parent_fd: int) -> None:
    """Exercise O_TMPFILE and linkat on the actual destination filesystem."""
    probe_name = f".finder-capability-{uuid.uuid4().hex}.probe"
    probe_fd: int | None = None
    linked = False
    linked_stat: os.stat_result | None = None
    primary: StorageFailure | None = None
    try:
        try:
            probe_fd = boundary.openat2(
                parent_fd,
                ".",
                os.O_TMPFILE | os.O_RDWR | os.O_CLOEXEC,
                mode=_ANONYMOUS_FILE_MODE,
                resolve=_CHILD_RESOLVE,
            )
            probe_stat = boundary.fstat(probe_fd)
            if not stat.S_ISREG(probe_stat.st_mode) or probe_stat.st_nlink != 0 or probe_stat.st_size != 0:
                raise _failure("filesystem did not provide an anonymous empty regular inode", code="capability_probe_failed")
        except StorageFailure as exc:
            primary = exc
        except BaseException as exc:
            primary = _exception_failure(exc, code="capability_probe_failed")

        if primary is None:
            try:
                assert probe_fd is not None
                boundary.link_empty(probe_fd, parent_fd, probe_name)
                linked = True
                linked_stat = boundary.fstat(probe_fd)
                if linked_stat.st_nlink != 1:
                    raise _failure("descriptor link probe did not create one directory entry", code="capability_probe_failed")
            except StorageFailure as exc:
                primary = exc
            except BaseException as exc:
                primary = _exception_failure(exc, code="capability_probe_link_failed")

        if linked:
            try:
                current_stat = _entry_metadata(boundary, parent_fd, probe_name)
                if linked_stat is None or not _same_image_stat(current_stat, linked_stat):
                    raise _failure(
                        "capability probe entry was substituted; refusing unsafe cleanup",
                        code="capability_probe_cleanup_failed",
                        cleanup_required=True,
                        artifacts=(probe_name,),
                    )
                boundary.unlink(parent_fd, probe_name)
                linked = False
            except StorageFailure as exc:
                primary = primary or exc
                if probe_name not in primary.artifacts:
                    primary.artifacts += (probe_name,)
                    primary.cleanup_required = True
            except BaseException as exc:
                primary = primary or _exception_failure(
                    exc,
                    code="capability_probe_cleanup_failed",
                    cleanup_required=True,
                    artifacts=(probe_name,),
                )
                if primary is not None and probe_name not in primary.artifacts:
                    primary.artifacts += (probe_name,)
                    primary.cleanup_required = True
    finally:
        if probe_fd is not None:
            cleanup = _close_fd(
                boundary,
                probe_fd,
                label=f"capability-probe:{probe_name}",
                installed=False,
                durability=False,
            )
            if cleanup is not None:
                cleanup.artifacts += (probe_name,)
                cleanup.cleanup_required = True
                if primary is None:
                    primary = cleanup
                else:
                    primary._add_cleanup(cleanup)
        if linked:
            # The unlink boundary failed or was not reached. The artifact is
            # deliberately not retried; callers receive its recoverable name.
            if primary is None:
                primary = _failure(
                    f"capability probe artifact remains: {probe_name}",
                    code="capability_probe_cleanup_failed",
                    cleanup_required=True,
                    artifacts=(probe_name,),
                )
            elif probe_name not in primary.artifacts:
                primary.artifacts += (probe_name,)
                primary.cleanup_required = True
    if primary is not None:
        raise primary


def _close_setup_descriptors(
    boundary: _LinuxSyscalls,
    descriptors: list[tuple[str, int]],
    *,
    keep: int | None = None,
) -> StorageFailure | None:
    unique: list[tuple[str, int]] = []
    seen: set[int] = set()
    for label, fd in descriptors:
        if fd == keep or fd in seen:
            continue
        seen.add(fd)
        unique.append((label, fd))
    return _close_many(boundary, list(reversed(unique)))


def _optional_lstat(path: Path, *, code: str) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _exception_failure(exc, code=code) from exc


def open_target(
    database: str | os.PathLike[str],
    *,
    workspace_root: Path,
    expected_exists: bool | None = None,
) -> _MemoryTarget | _FileTarget:
    """Prepare a descriptor-pinned Finder image target.

    The capability probe completes before an existing target descriptor is
    opened. ``:memory:`` intentionally bypasses all Linux filesystem work.
    """
    try:
        memory_candidate = os.fspath(database)
    except TypeError:
        memory_candidate = None
    if memory_candidate == ":memory:":
        return _MemoryTarget()

    path, parent, temp_root, temp_parts, parent_relative = _validated_paths(database, workspace_root)
    if not _sqlite_image_available():
        raise _failure("Python SQLite image serialization is unavailable", code="unsupported_storage")

    try:
        boundary = _new_syscalls()
    except StorageFailure:
        raise
    except BaseException as exc:
        raise _exception_failure(exc, code="unsupported_storage") from exc

    root_fd: int | None = None
    temp_fd: int | None = None
    parent_fd: int | None = None
    opened: list[tuple[str, int]] = []
    target_fd: int | None = None
    try:
        try:
            root_fd = boundary.open_root()
        except StorageFailure:
            raise
        except BaseException as exc:
            raise _exception_failure(exc, code="directory_pin_failed") from exc
        temp_fd, temp_opened = _walk_from_root(boundary, root_fd, temp_parts, resolve=_ANCHOR_RESOLVE)
        opened.extend(temp_opened)
        parent_fd, parent_opened = _walk_from_root(
            boundary,
            temp_fd,
            parent_relative,
            resolve=_CHILD_RESOLVE,
            close_root_on_error=False,
            final_flags=os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        if not parent_relative:
            try:
                parent_fd = boundary.openat2(
                    temp_fd,
                    ".",
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    resolve=_CHILD_RESOLVE,
                )
                parent_opened.append(("pinned-parent", parent_fd))
            except StorageFailure:
                raise
            except BaseException as exc:
                raise _exception_failure(exc, code="directory_pin_failed") from exc
        opened.extend(parent_opened)
        temp_stat = boundary.fstat(temp_fd)
        parent_stat = boundary.fstat(parent_fd)
        if not stat.S_ISDIR(temp_stat.st_mode) or not stat.S_ISDIR(parent_stat.st_mode):
            raise _failure("temporary-directory anchor or parent is not a directory", code="unsafe_database_path")
        if temp_stat.st_dev != parent_stat.st_dev:
            raise _failure("mount crossing beneath temporary root is not permitted", code="unsafe_database_path")

        # Observe the requested entry without opening it. This distinguishes a
        # pre-existing file deleted during the capability probe from a genuinely
        # absent destination; descriptor access remains the authority below.
        observed_target = _optional_lstat(path, code="target_observation_failed")
        if expected_exists is True and observed_target is None:
            raise _failure("expected existing database disappeared before final open", code="target_disappeared")
        if expected_exists is False and observed_target is not None:
            raise _failure("database target appeared before final open", code="target_race")

        # This is deliberately before opening the requested database entry.
        _probe_capabilities(boundary, parent_fd)
        current_temp = _optional_lstat(temp_root, code="namespace_observation_failed")
        current_parent = _optional_lstat(parent, code="namespace_observation_failed")
        if (
            current_temp is None
            or current_parent is None
            or not _identity_matches(current_temp, temp_stat)
            or not _identity_matches(current_parent, parent_stat)
        ):
            raise _failure("temporary-directory namespace changed before target open", code="namespace_changed")

        current_target = _optional_lstat(path, code="target_observation_failed")
        if observed_target is None and current_target is not None:
            raise _failure("database target appeared during capability preflight", code="target_race")
        if observed_target is not None and current_target is None:
            raise _failure("existing database disappeared before final open", code="target_disappeared")
        if (
            observed_target is not None
            and current_target is not None
            and not _target_identity_matches(observed_target, current_target)
        ):
            raise _failure("database target was substituted before final open", code="target_substituted")
        if observed_target is not None and not _regular_single_link(observed_target):
            raise _failure(
                "existing database must be a nonempty single-link regular file",
                code="unsafe_database_target",
            )

        target_name = path.name
        try:
            target_fd = boundary.openat2(
                parent_fd,
                target_name,
                # O_NONBLOCK prevents a concurrent replacement with a FIFO or
                # other blocking special file from hanging before fstat rejects
                # it. Regular database reads are unaffected by this flag.
                os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW,
                resolve=_CHILD_RESOLVE,
            )
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                if observed_target is not None:
                    raise _failure("existing database disappeared before final open", code="target_disappeared") from exc
                target_fd = None
            else:
                raise _exception_failure(exc, code="target_open_failed") from exc

        target_stat: os.stat_result | None = None
        if target_fd is not None:
            try:
                target_stat = boundary.fstat(target_fd)
            except BaseException as exc:
                raise _exception_failure(exc, code="target_stat_failed") from exc
            if not _regular_single_link(target_stat):
                raise _failure(
                    "existing database must be a nonempty single-link regular file",
                    code="unsafe_database_target",
                )
            if target_stat.st_size > MAX_IMAGE_BYTES:
                raise _failure("existing database image exceeds the size bound", code="invalid_database_image")
            if observed_target is None or not _same_image_stat(observed_target, target_stat):
                raise _failure("database target was substituted during final open", code="target_substituted")

        final_temp = _optional_lstat(temp_root, code="namespace_observation_failed")
        final_parent = _optional_lstat(parent, code="namespace_observation_failed")
        if (
            final_temp is None
            or final_parent is None
            or not _identity_matches(final_temp, temp_stat)
            or not _identity_matches(final_parent, parent_stat)
        ):
            raise _failure("temporary-directory namespace changed while opening target", code="namespace_changed")

        target = _FileTarget(
            boundary=boundary,
            database=path,
            parent=parent,
            temp_root=temp_root,
            temp_stat=temp_stat,
            parent_fd=parent_fd,
            parent_stat=parent_stat,
            target_fd=target_fd,
            target_stat=target_stat,
            temp_parts=temp_parts,
        )
        parent_fd = None
        close_failure = _close_setup_descriptors(boundary, opened, keep=target._parent_fd)
        opened = []
        if close_failure is not None:
            try:
                target.close()
            except StorageFailure as target_cleanup:
                close_failure._add_cleanup(target_cleanup)
            raise close_failure
        return target
    except StorageFailure as primary:
        descriptors: list[tuple[str, int]] = []
        if target_fd is not None:
            descriptors.append(("existing-database", target_fd))
            target_fd = None
        if parent_fd is not None:
            descriptors.append(("pinned-parent", parent_fd))
            parent_fd = None
        descriptors.extend(opened)
        cleanup = _close_setup_descriptors(boundary, descriptors)
        if cleanup is not None:
            primary._add_cleanup(cleanup)
        raise
    except BaseException as exc:
        primary = _exception_failure(exc, code="storage_setup_failed")
        descriptors = []
        if target_fd is not None:
            descriptors.append(("existing-database", target_fd))
            target_fd = None
        if parent_fd is not None:
            descriptors.append(("pinned-parent", parent_fd))
            parent_fd = None
        descriptors.extend(opened)
        cleanup = _close_setup_descriptors(boundary, descriptors)
        if cleanup is not None:
            primary._add_cleanup(cleanup)
        raise primary from exc
