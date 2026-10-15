# First Customer Finder: Linux safety design

## Decision recorded before implementation

The saved-input v1 schema is fail-closed unless it contains the validated
`novelty_evidence` contract. Absence of a detected match is not proof of
novelty, even for a single record and an empty snapshot. Every unresolved
identity is `AMBIGUOUS_DUPLICATE` unless a complete, authoritative snapshot
binding is validated. Exact, probable, conflicting and malformed
classifications remain available. Empty input must not create a database.
The contract is a consistency binding, not source authenticity, operator
authentication, or authorization.

The file backend is Linux-specific and capability checked in
`media_house/finder/linux_storage.py`; `media_house/finder/core.py` delegates
file targets to it. There is no path-based SQLite fallback, named staging
fallback, overwrite rename, or link-then-unlink installation fallback.
Existing Finder files are read-only snapshots; the backend never replaces or
modifies an existing database.

## Primitives and object boundaries

1. Use `ctypes` from the standard library to call Linux `openat2` and `linkat`.
   Support only architectures whose syscall ABI is explicitly implemented.
   Missing symbols, unsupported kernel flags, filesystem support or permission
   cause rejection, not a less-safe implementation.
2. Open/pin the OS temporary-directory anchor and the requested parent with
   `openat2`. Use `RESOLVE_BENEATH`, `RESOLVE_NO_SYMLINKS`, and
   `RESOLVE_NO_MAGICLINKS`; also prohibit mount crossing beneath the temporary
   anchor with `RESOLVE_NO_XDEV`. Reject workspace paths, URI inputs, parent
   traversal, nonregular targets and existing hardlinked/zero-byte files.
3. Open an existing target relative to the pinned directory descriptor with
   `O_NOFOLLOW`, `O_NONBLOCK`, and read-only semantics, never `O_CREAT`. Keep
   its file descriptor open. Read bounded image bytes from that descriptor,
   not a reopened path. Entry/inode/time checks detect deletion/substitution
   and return rejection; these checks are not the access-control mechanism.
   Descriptor pinning is.
4. SQLite connects only to `:memory:`. Use Python 3.12's SQLite image API to
   deserialize a bounded existing image and serialize a committed new image.
   SQLite never opens a filesystem database path or journal. Unsupported image
   APIs reject file operations before the target is read. Unreadable,
   inconsistent or unsupported images fail closed. This is not a live database
   synchronization protocol or a WAL/journal recovery implementation.
5. For a new image, use `openat2`/directory-relative `O_TMPFILE | O_RDWR` to create
   an anonymous inode on the target filesystem. Write image bytes through the
   descriptor and `fsync` it. There is no staging filename to substitute.
6. Install using `linkat(anonymous_fd, "", parent_fd, basename, AT_EMPTY_PATH)`.
   This atomically gives the complete anonymous inode its **first and only**
   filename. Any existing destination, including a dangling symlink, causes
   rejection. Unlike linking a named staging file, successful installation has
   link count one and requires no pathname unlink to make the target usable.
7. `fsync` the parent directory after linking. Close owned descriptors without
   retrying an uncertain `close` on a possibly reused descriptor. There is no
   post-install target deletion or replacement to attempt unsafe rollback.

Capability preflight exercises anonymous-file creation and descriptor linking
on the actual filesystem before opening any database target or SQLite
connection. A unique, empty non-database probe entry is removed relative to the
pinned parent; cleanup is identity-checked, is never retried after an
uncertain failure, and identifies any recoverable artifact. It must never be
mistaken for successful database work.

## Four safety requirements and failure receipts

| Requirement | Guarantee |
|---|---|
| Fail-closed identity | No unresolved v1 identity reaches `NEW`, regardless of snapshot size. Only a validated complete-snapshot binding can enable the installation path. |
| Path substitution | Existing bytes come from an approved descriptor; anonymous staging has no pathname; installation uses a source descriptor and pinned destination directory. Symlink replacements cannot redirect these operations. Target identity checks include device, inode and change-time metadata. |
| Finalization/cleanup | Before `linkat` succeeds, no target is installed. After it succeeds, the target is a complete single-link image. Later durability/cleanup errors report committed installation plus the error, never ordinary success or fictitious rollback. |
| Concurrent deletion | Existing files are opened read-only without creation and never reopened by SQLite. Deletion causes rejection and cannot create a replacement file. |

Parent/target namespace changes detected during the operation must be reported
as errors, not ordinary successful installation. Descriptor-relative access
continues to refer to the originally authorized directory/inode, never its
pathname replacement. No implementation can prevent another privileged actor
from subsequently moving, modifying or deleting an already authorized object;
the receipt describes this operation's commit state, not immutable future
filesystem contents.

On a post-install `fsync` or descriptor cleanup failure, retain the valid target
and return an explicit incomplete/durability-uncertain state with
`database_write_performed=true` and accurate persisted counts. On a pre-install
failure, report no target installation and zero imported records. Report any
capability-probe cleanup artifact separately. Never suppress cleanup errors,
unlink a substituted target, or automatically retry an uncertain operation.

## Verification plan

Use deterministic syscall-boundary injection, not timing-dependent sleeps, for
target creation, target symlink substitution, parent substitution, expected
target deletion, unavailable capabilities, link failure, short/failed writes,
pre/post-install `fsync` failure and cleanup/close failure. Assert sentinel
bytes, target absence or complete single-link state, accurate error receipts,
and usability on a later run.

Seed duplicate-test snapshots independently with fabricated records; do not
bypass the public identity gate to claim a successful v1 import. Exercise SQL
insert-only defaults and rollback separately in disposable in-memory databases.
Backend installation tests operate only on fabricated SQLite image bytes.

Before any suites run, inspect fixtures/connection targets and enforce offline
network and database-target guards. Run the dedicated Finder tests and the full
standard-library offline suite with bytecode writes disabled. No real workspace
database, live source, provider, outreach, installation or deployment is used.

This design is a local synthetic-data boundary, not authentication, tenant
isolation, source verification, a production storage protocol or production
readiness. Unsupported platforms/filesystems remain rejected.
