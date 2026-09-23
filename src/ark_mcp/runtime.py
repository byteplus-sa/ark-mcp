"""Process-lifetime services shared by tools and resources.

The FastMCP lifespan owns this container. Keeping artifact persistence,
concurrency controls, ownership, and budget state here prevents request-scoped
limiters and mutable server-module globals from leaking across test servers.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from cachetools import TTLCache
from fastmcp.server.dependencies import get_access_token

from ark_mcp.artifacts.filesystem_store import FilesystemArtifactStore
from ark_mcp.artifacts.object_storage_store import ObjectStorageArtifactStore
from ark_mcp.config.env import Settings
from ark_mcp.domain.artifacts import ArtifactRef
from ark_mcp.domain.errors import ProviderError
from ark_mcp.observability.logger import info as log_info
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.observability.metrics import BUDGET_REJECTIONS
from ark_mcp.security.auth_context import AuthContext, PrincipalContext
from ark_mcp.security.safe_downloader import SafeDownloader

if TYPE_CHECKING:
    from fastmcp import Context, FastMCP

    from ark_mcp.artifacts.store import ArtifactStore

ProviderKey = Literal["modelark", "seed-speech", "vod-mediakit", "tos", "s3"]


def _open_sqlite_connection(database_path: Path) -> sqlite3.Connection:
    """Open a WAL-mode SQLite connection safe for thread-offloaded access.

    ``check_same_thread=False`` is required because database access is
    dispatched to worker threads via :func:`_offload`. Each store owns an
    ``asyncio.Lock`` that serializes access to its connection object, so at
    most one thread touches a given connection at a time.
    """
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, check_same_thread=False)
    connection.execute("PRAGMA journal_mode=WAL").fetchone()
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


async def _offload[T](func: Callable[..., T], *args: Any) -> T:
    """Run a synchronous callable in a worker thread, off the event loop."""
    return await asyncio.to_thread(func, *args)


async def _run_locked[T](lock: asyncio.Lock, func: Callable[[], T]) -> T:
    """Run a synchronous closure off the loop while holding ``lock``.

    The lock serializes access to a store's ``sqlite3.Connection``, and
    ``asyncio.to_thread`` moves the synchronous database call off the event
    loop so a slow commit cannot block unrelated MCP requests.
    """
    async with lock:
        return await _offload(func)


class ProviderLimiters:
    """Shared provider and bounded per-principal concurrency controls."""

    def __init__(
        self,
        *,
        provider_limit: int = 5,
        principal_limit: int = 3,
        principal_cache_size: int = 10_000,
        principal_ttl_seconds: int = 86_400,
    ) -> None:
        if provider_limit <= 0 or principal_limit <= 0:
            raise ValueError("Concurrency limits must be positive.")
        self._provider = {
            "modelark": asyncio.Semaphore(provider_limit),
            "seed-speech": asyncio.Semaphore(provider_limit),
            "vod-mediakit": asyncio.Semaphore(provider_limit),
            "tos": asyncio.Semaphore(provider_limit),
            "s3": asyncio.Semaphore(provider_limit),
        }
        self._principal_limit = principal_limit
        self._principals: TTLCache[str, asyncio.Semaphore] = TTLCache(
            maxsize=principal_cache_size,
            ttl=principal_ttl_seconds,
        )
        self._cache_lock = asyncio.Lock()

    def provider(self, provider: ProviderKey) -> asyncio.Semaphore:
        return self._provider[provider]

    async def principal(self, owner: AuthContext) -> asyncio.Semaphore:
        key = f"{owner.tenant_id}\0{owner.principal_id}"
        async with self._cache_lock:
            limiter = self._principals.get(key)
            if limiter is None:
                limiter = asyncio.Semaphore(self._principal_limit)
                self._principals[key] = limiter
            return limiter

    @asynccontextmanager
    async def acquire(
        self,
        provider: ProviderKey,
        owner: AuthContext,
    ) -> AsyncIterator[None]:
        if owner.is_local:
            # A local stdio principal is a single trusted caller; the
            # per-principal semaphore would otherwise cap ALL concurrent calls
            # across every provider at ``principal_limit``, below the
            # per-provider limit. Bound local concurrency by provider only.
            async with self.provider(provider):
                yield
        else:
            principal_limiter = await self.principal(owner)
            async with self.provider(provider), principal_limiter:
                yield


class TaskOwnershipStore(Protocol):
    async def claim(self, provider: str, task_id: str, owner: AuthContext) -> bool: ...

    async def record(self, provider: str, task_id: str, owner: AuthContext) -> None: ...

    async def require_owner(self, provider: str, task_id: str, owner: AuthContext) -> None: ...

    async def list_task_ids(self, provider: str, owner: AuthContext) -> set[str]: ...

    async def prune(self, max_age_days: int) -> int: ...

    async def ping(self) -> None: ...

    async def close(self) -> None: ...


class ObjectKeyOwnershipStore(Protocol):
    """Ownership ledger for object-storage keys minted by ``media_upload``."""

    async def record(self, object_key: str, owner: AuthContext) -> None: ...

    async def require_owner(self, object_key: str, owner: AuthContext) -> None: ...

    async def prune(self, max_age_days: int) -> int: ...

    async def ping(self) -> None: ...

    async def close(self) -> None: ...


class TaskArtifactCache(Protocol):
    async def get(self, provider: str, task_id: str) -> dict[str, ArtifactRef | None] | None: ...

    async def set(
        self,
        provider: str,
        task_id: str,
        artifacts: dict[str, ArtifactRef | None],
    ) -> None: ...

    async def pop(self, provider: str, task_id: str) -> dict[str, ArtifactRef | None] | None: ...

    async def prune_expired(self) -> int: ...

    async def clear(self) -> None: ...

    async def close(self) -> None: ...


@dataclass(slots=True)
class _TaskArtifactLockEntry:
    lock: asyncio.Lock
    users: int = 0
    artifacts: dict[str, ArtifactRef | None] | None = None


class TaskArtifactPersistenceLocks:
    """Bounded-lifetime task locks for artifact persistence single-flight."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], _TaskArtifactLockEntry] = {}
        self._registry_lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self, provider: str, task_id: str) -> AsyncIterator[_TaskArtifactLockEntry]:
        """Serialize persistence for one provider task while leaving other tasks concurrent."""
        key = (provider, task_id)
        async with self._registry_lock:
            entry = self._entries.get(key)
            if entry is None:
                entry = _TaskArtifactLockEntry(lock=asyncio.Lock())
                self._entries[key] = entry
            entry.users += 1

        try:
            async with entry.lock:
                yield entry
        finally:
            async with self._registry_lock:
                entry.users -= 1
                if entry.users == 0:
                    self._entries.pop(key, None)


class SQLiteTaskOwnershipStore:
    """Small single-instance ownership database for provider task IDs."""

    def __init__(self, database_path: Path) -> None:
        self._connection = _open_sqlite_connection(database_path)
        self._ensure_schema()
        self._connection.commit()
        self._lock = asyncio.Lock()

    def _ensure_schema(self) -> None:
        """Create or migrate the ownership table to provider-scoped keys."""
        with self._connection:
            columns = {
                str(row[1])
                for row in self._connection.execute("PRAGMA table_info(task_ownership)").fetchall()
            }
            legacy_exists = self._table_exists("task_ownership_legacy")
            if columns and "provider" not in columns:
                if legacy_exists:
                    self._connection.execute(
                        """
                        INSERT OR REPLACE INTO task_ownership_legacy
                        SELECT task_id, principal_id, tenant_id, created_at FROM task_ownership
                        """
                    )
                    self._connection.execute("DROP TABLE task_ownership")
                else:
                    self._connection.execute(
                        "ALTER TABLE task_ownership RENAME TO task_ownership_legacy"
                    )
                legacy_exists = True
            self._create_table()
            if legacy_exists:
                self._connection.execute(
                    """
                    INSERT OR REPLACE INTO task_ownership(
                        provider, task_id, principal_id, tenant_id, created_at
                    )
                    SELECT 'modelark', task_id, principal_id, tenant_id, created_at
                    FROM task_ownership_legacy
                    """
                )
                self._connection.execute("DROP TABLE task_ownership_legacy")

    def _table_exists(self, table: str) -> bool:
        return (
            self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()
            is not None
        )

    def _create_table(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS task_ownership (
                provider TEXT NOT NULL,
                task_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(provider, task_id)
            )
            """
        )

    async def record(self, provider: str, task_id: str, owner: AuthContext) -> None:
        created_at = datetime.now(UTC).isoformat()

        def _run() -> None:
            self._connection.execute(
                """
                INSERT INTO task_ownership(
                    provider, task_id, principal_id, tenant_id, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider, task_id) DO UPDATE SET
                    principal_id = excluded.principal_id,
                    tenant_id = excluded.tenant_id
                """,
                (
                    provider,
                    task_id,
                    owner.principal_id,
                    owner.tenant_id,
                    created_at,
                ),
            )
            self._connection.commit()

        await _run_locked(self._lock, _run)

    async def claim(self, provider: str, task_id: str, owner: AuthContext) -> bool:
        created_at = datetime.now(UTC).isoformat()

        def _run() -> bool:
            cursor = self._connection.execute(
                """
                INSERT INTO task_ownership(provider, task_id, principal_id, tenant_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider, task_id) DO NOTHING
                """,
                (provider, task_id, owner.principal_id, owner.tenant_id, created_at),
            )
            self._connection.commit()
            return cursor.rowcount == 1

        return await _run_locked(self._lock, _run)

    async def require_owner(self, provider: str, task_id: str, owner: AuthContext) -> None:
        def _run() -> tuple[str, str] | None:
            return cast(
                "tuple[str, str] | None",
                self._connection.execute(
                    """
                    SELECT principal_id, tenant_id FROM task_ownership
                    WHERE provider = ? AND task_id = ?
                    """,
                    (provider, task_id),
                ).fetchone(),
            )

        row = await _run_locked(self._lock, _run)
        if row is None:
            if owner.is_local:
                return
            raise PermissionError("Task is not owned by the current principal.")
        if row != (owner.principal_id, owner.tenant_id):
            raise PermissionError("Task is not owned by the current principal.")

    async def list_task_ids(self, provider: str, owner: AuthContext) -> set[str]:
        def _run() -> list[tuple[str]]:
            return self._connection.execute(
                """
                SELECT task_id FROM task_ownership
                WHERE provider = ? AND principal_id = ? AND tenant_id = ?
                """,
                (provider, owner.principal_id, owner.tenant_id),
            ).fetchall()

        rows = await _run_locked(self._lock, _run)
        return {str(row[0]) for row in rows}

    async def prune(self, max_age_days: int) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=max_age_days)).isoformat()

        def _run() -> int:
            cursor = self._connection.execute(
                "DELETE FROM task_ownership WHERE created_at < ?",
                (cutoff,),
            )
            self._connection.commit()
            return cursor.rowcount if cursor.rowcount is not None else 0

        return await _run_locked(self._lock, _run)

    async def ping(self) -> None:
        await _run_locked(self._lock, lambda: self._connection.execute("SELECT 1").fetchone())

    async def close(self) -> None:
        await _run_locked(self._lock, self._connection.close)


class SQLiteObjectKeyOwnershipStore:
    """SQLite ownership ledger for object-storage keys (``media_upload``)."""

    def __init__(self, database_path: Path) -> None:
        self._connection = _open_sqlite_connection(database_path)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS object_key_ownership (
                object_key TEXT NOT NULL PRIMARY KEY,
                principal_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._connection.commit()
        self._lock = asyncio.Lock()

    async def record(self, object_key: str, owner: AuthContext) -> None:
        created_at = datetime.now(UTC).isoformat()

        def _run() -> None:
            self._connection.execute(
                """
                INSERT INTO object_key_ownership(
                    object_key, principal_id, tenant_id, created_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(object_key) DO UPDATE SET
                    principal_id = excluded.principal_id,
                    tenant_id = excluded.tenant_id,
                    created_at = excluded.created_at
                """,
                (
                    object_key,
                    owner.principal_id,
                    owner.tenant_id,
                    created_at,
                ),
            )
            self._connection.commit()

        await _run_locked(self._lock, _run)

    async def require_owner(self, object_key: str, owner: AuthContext) -> None:
        def _run() -> None:
            row = self._connection.execute(
                """
                SELECT principal_id, tenant_id FROM object_key_ownership
                WHERE object_key = ?
                """,
                (object_key,),
            ).fetchone()
            if row is None:
                if owner.is_local:
                    return
                raise PermissionError("Object key is not owned by the current principal.")
            if row != (owner.principal_id, owner.tenant_id):
                raise PermissionError("Object key is not owned by the current principal.")
            self._connection.execute(
                "UPDATE object_key_ownership SET created_at = ? WHERE object_key = ?",
                (datetime.now(UTC).isoformat(), object_key),
            )
            self._connection.commit()

        await _run_locked(self._lock, _run)

    async def prune(self, max_age_days: int) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=max_age_days)).isoformat()

        def _run() -> int:
            cursor = self._connection.execute(
                "DELETE FROM object_key_ownership WHERE created_at < ?",
                (cutoff,),
            )
            self._connection.commit()
            return cursor.rowcount if cursor.rowcount is not None else 0

        return await _run_locked(self._lock, _run)

    async def ping(self) -> None:
        await _run_locked(self._lock, lambda: self._connection.execute("SELECT 1").fetchone())

    async def close(self) -> None:
        await _run_locked(self._lock, self._connection.close)


class SQLiteTaskArtifactCache:
    """SQLite-backed cache mapping provider task IDs to persisted artifact refs."""

    def __init__(
        self,
        database_path: Path,
        *,
        ttl_seconds: int = 86_400,
        max_size: int = 10_000,
    ) -> None:
        self._connection = _open_sqlite_connection(database_path)
        self._ensure_schema()
        self._connection.commit()
        self._lock = asyncio.Lock()
        self._ttl_seconds = ttl_seconds
        self._max_size = max_size

    def _ensure_schema(self) -> None:
        """Create or migrate the artifact cache to provider-scoped keys."""
        with self._connection:
            columns = {
                str(row[1])
                for row in self._connection.execute("PRAGMA table_info(task_artifacts)").fetchall()
            }
            legacy_exists = self._table_exists("task_artifacts_legacy")
            if columns and "provider" not in columns:
                if legacy_exists:
                    self._connection.execute(
                        """
                        INSERT OR REPLACE INTO task_artifacts_legacy
                        SELECT task_id, artifacts_json, created_at FROM task_artifacts
                        """
                    )
                    self._connection.execute("DROP TABLE task_artifacts")
                else:
                    self._connection.execute(
                        "ALTER TABLE task_artifacts RENAME TO task_artifacts_legacy"
                    )
                legacy_exists = True
            self._create_table()
            if legacy_exists:
                self._connection.execute(
                    """
                    INSERT OR REPLACE INTO task_artifacts(
                        provider, task_id, artifacts_json, created_at
                    )
                    SELECT 'modelark', task_id, artifacts_json, created_at
                    FROM task_artifacts_legacy
                    """
                )
                self._connection.execute("DROP TABLE task_artifacts_legacy")

    def _table_exists(self, table: str) -> bool:
        return (
            self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()
            is not None
        )

    def _create_table(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS task_artifacts (
                provider TEXT NOT NULL,
                task_id TEXT NOT NULL,
                artifacts_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(provider, task_id)
            )
            """
        )

    async def get(self, provider: str, task_id: str) -> dict[str, ArtifactRef | None] | None:
        def _run() -> tuple[str, str] | None:
            return cast(
                "tuple[str, str] | None",
                self._connection.execute(
                    """
                    SELECT artifacts_json, created_at FROM task_artifacts
                    WHERE provider = ? AND task_id = ?
                    """,
                    (provider, task_id),
                ).fetchone(),
            )

        row = await _run_locked(self._lock, _run)
        if row is None:
            return None
        artifacts_json, created_at_str = row
        created_at = datetime.fromisoformat(created_at_str)
        if (datetime.now(UTC) - created_at).total_seconds() > self._ttl_seconds:
            return None
        return self._deserialize(artifacts_json)

    async def set(
        self,
        provider: str,
        task_id: str,
        artifacts: dict[str, ArtifactRef | None],
    ) -> None:
        artifacts_json = self._serialize(artifacts)
        now = datetime.now(UTC).isoformat()

        def _run() -> None:
            self._connection.execute(
                """
                INSERT INTO task_artifacts(provider, task_id, artifacts_json, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(provider, task_id) DO UPDATE SET
                    artifacts_json = excluded.artifacts_json,
                    created_at = excluded.created_at
                """,
                (provider, task_id, artifacts_json, now),
            )
            self._connection.commit()
            count_row = self._connection.execute("SELECT COUNT(*) FROM task_artifacts").fetchone()
            if count_row is not None and count_row[0] > self._max_size:
                self._connection.execute(
                    """
                    DELETE FROM task_artifacts
                    WHERE (provider, task_id) NOT IN (
                        SELECT provider, task_id FROM task_artifacts
                        ORDER BY created_at DESC LIMIT ?
                    )
                    """,
                    (self._max_size,),
                )
                self._connection.commit()

        await _run_locked(self._lock, _run)

    async def pop(self, provider: str, task_id: str) -> dict[str, ArtifactRef | None] | None:
        def _run() -> tuple[str, str] | None:
            row = cast(
                "tuple[str, str] | None",
                self._connection.execute(
                    """
                    SELECT artifacts_json, created_at FROM task_artifacts
                    WHERE provider = ? AND task_id = ?
                    """,
                    (provider, task_id),
                ).fetchone(),
            )
            if row is not None:
                self._connection.execute(
                    "DELETE FROM task_artifacts WHERE provider = ? AND task_id = ?",
                    (provider, task_id),
                )
                self._connection.commit()
            return row

        row = await _run_locked(self._lock, _run)
        if row is None:
            return None
        created_at = datetime.fromisoformat(row[1])
        if (datetime.now(UTC) - created_at).total_seconds() > self._ttl_seconds:
            return None
        return self._deserialize(row[0])

    async def clear(self) -> None:
        def _run() -> None:
            self._connection.execute("DELETE FROM task_artifacts")
            self._connection.commit()

        await _run_locked(self._lock, _run)

    async def prune_expired(self) -> int:
        cutoff = (datetime.now(UTC) - timedelta(seconds=self._ttl_seconds)).isoformat()

        def _run() -> int:
            cursor = self._connection.execute(
                "DELETE FROM task_artifacts WHERE created_at < ?",
                (cutoff,),
            )
            self._connection.commit()
            return cursor.rowcount if cursor.rowcount is not None else 0

        return await _run_locked(self._lock, _run)

    async def close(self) -> None:
        await _run_locked(self._lock, self._connection.close)

    @staticmethod
    def _serialize(artifacts: dict[str, ArtifactRef | None]) -> str:
        return json.dumps(
            {k: v.model_dump(mode="json") if v is not None else None for k, v in artifacts.items()}
        )

    @staticmethod
    def _deserialize(raw: str) -> dict[str, ArtifactRef | None]:
        data = json.loads(raw)
        return {
            k: ArtifactRef.model_validate(v) if v is not None else None for k, v in data.items()
        }


@dataclass(frozen=True, slots=True)
class CostEstimate:
    product: str
    amount_usd: float


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    reservation_id: int
    owner: AuthContext
    estimate: CostEstimate
    usage_date: date


class BudgetExceededError(ValueError):
    """Raised before dispatch when a principal's daily budget is exhausted."""


class BudgetLedger:
    """SQLite-backed daily budget reservations for one server instance."""

    def __init__(self, database_path: Path, *, daily_limit_usd: float | None = None) -> None:
        self._connection = _open_sqlite_connection(database_path)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS budget_reservations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usage_date TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                product TEXT NOT NULL,
                amount_usd REAL NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('reserved', 'committed', 'released'))
            )
            """
        )
        self._connection.commit()
        self._daily_limit_usd = daily_limit_usd
        self._lock = asyncio.Lock()

    async def reserve(
        self,
        owner: AuthContext,
        estimate: CostEstimate,
    ) -> BudgetReservation:
        usage_date = datetime.now(UTC).date()

        def _run() -> int:
            row = self._connection.execute(
                """
                SELECT COALESCE(SUM(amount_usd), 0) FROM budget_reservations
                WHERE usage_date = ? AND principal_id = ? AND tenant_id = ?
                  AND status IN ('reserved', 'committed')
                """,
                (usage_date.isoformat(), owner.principal_id, owner.tenant_id),
            ).fetchone()
            current = float(row[0]) if row is not None else 0.0
            if (
                self._daily_limit_usd is not None
                and current + estimate.amount_usd > self._daily_limit_usd
            ):
                BUDGET_REJECTIONS.labels(product=estimate.product).inc()
                log_warning(
                    "budget_rejected",
                    product=estimate.product,
                    amount_usd=round(estimate.amount_usd, 6),
                    current_usd=round(current, 6),
                    daily_limit_usd=self._daily_limit_usd,
                    principal_id=owner.principal_id,
                )
                raise BudgetExceededError(
                    f"Daily budget of ${self._daily_limit_usd:.2f} would be exceeded."
                )
            cursor = self._connection.execute(
                """
                INSERT INTO budget_reservations(
                    usage_date, principal_id, tenant_id, product, amount_usd, status
                ) VALUES (?, ?, ?, ?, ?, 'reserved')
                """,
                (
                    usage_date.isoformat(),
                    owner.principal_id,
                    owner.tenant_id,
                    estimate.product,
                    estimate.amount_usd,
                ),
            )
            self._connection.commit()
            if cursor.lastrowid is None:
                raise RuntimeError("Budget reservation did not return an ID.")
            return cursor.lastrowid

        reservation_id = await _run_locked(self._lock, _run)
        return BudgetReservation(reservation_id, owner, estimate, usage_date)

    async def commit(self, reservation: BudgetReservation) -> None:
        await self._set_status(reservation, "committed")

    async def release(self, reservation: BudgetReservation) -> None:
        await self._set_status(reservation, "released")

    async def _set_status(
        self,
        reservation: BudgetReservation,
        status: Literal["committed", "released"],
    ) -> None:
        def _run() -> None:
            self._connection.execute(
                "UPDATE budget_reservations SET status = ? WHERE id = ?",
                (status, reservation.reservation_id),
            )
            self._connection.commit()

        await _run_locked(self._lock, _run)

    async def prune(self, max_age_days: int) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=max_age_days)).date().isoformat()

        def _run() -> int:
            cursor = self._connection.execute(
                "DELETE FROM budget_reservations WHERE usage_date < ?",
                (cutoff,),
            )
            self._connection.commit()
            return cursor.rowcount if cursor.rowcount is not None else 0

        return await _run_locked(self._lock, _run)

    async def close(self) -> None:
        await _run_locked(self._lock, self._connection.close)


@dataclass(slots=True)
class RuntimeServices:
    settings: Settings
    artifact_store: ArtifactStore
    safe_downloader: SafeDownloader
    ownership_store: TaskOwnershipStore
    object_key_ownership_store: ObjectKeyOwnershipStore
    budget_ledger: BudgetLedger
    provider_limiters: ProviderLimiters
    task_artifact_cache: TaskArtifactCache
    task_artifact_locks: TaskArtifactPersistenceLocks


@dataclass(slots=True)
class RuntimeState:
    runtime: RuntimeServices | None = None


def build_state_stores(
    settings: Settings,
    database_path: Path,
) -> tuple[TaskOwnershipStore, ObjectKeyOwnershipStore, BudgetLedger, TaskArtifactCache]:
    """Build the state stores for the configured ``STATE_BACKEND``."""
    if settings.state_backend != "sqlite":
        raise ValueError(f"Unsupported STATE_BACKEND: {settings.state_backend}")
    log_warning(
        "single_instance_state_backend",
        backend=settings.state_backend,
        note="Only one replica may run with the SQLite state backend.",
    )
    return (
        SQLiteTaskOwnershipStore(database_path),
        SQLiteObjectKeyOwnershipStore(database_path),
        BudgetLedger(database_path, daily_limit_usd=settings.daily_budget_usd or None),
        SQLiteTaskArtifactCache(
            database_path,
            ttl_seconds=settings.persistence_cache_ttl_seconds,
            max_size=settings.persistence_cache_max_size,
        ),
    )


async def create_runtime_services(settings: Settings) -> RuntimeServices:
    artifact_dir = Path(settings.artifact_dir).expanduser().resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    downloader = SafeDownloader(
        timeout=settings.artifact_download_timeout_seconds,
        connect_timeout=settings.connect_timeout_ms / 1000,
        max_attempts=settings.artifact_download_max_attempts,
    )
    if settings.artifact_backend == "object_storage":
        artifact_store: ArtifactStore = ObjectStorageArtifactStore(
            settings=settings,
            downloader=downloader,
        )
    else:
        artifact_store = FilesystemArtifactStore(
            artifact_dir=str(artifact_dir),
            ttl_seconds=settings.artifact_ttl_seconds,
            downloader=downloader,
        )
    database_path = artifact_dir / "runtime.sqlite3"
    ownership_store, object_key_ownership_store, budget_ledger, task_artifact_cache = (
        build_state_stores(settings, database_path)
    )
    return RuntimeServices(
        settings=settings,
        artifact_store=artifact_store,
        safe_downloader=downloader,
        ownership_store=ownership_store,
        object_key_ownership_store=object_key_ownership_store,
        budget_ledger=budget_ledger,
        provider_limiters=ProviderLimiters(
            provider_limit=settings.provider_max_concurrency,
            principal_limit=settings.principal_max_concurrency,
        ),
        task_artifact_cache=task_artifact_cache,
        task_artifact_locks=TaskArtifactPersistenceLocks(),
    )


async def close_runtime_services(runtime: RuntimeServices) -> None:
    await runtime.artifact_store.close()
    await runtime.ownership_store.close()
    await runtime.object_key_ownership_store.close()
    await runtime.budget_ledger.close()
    await runtime.task_artifact_cache.close()


RuntimeFactory = Callable[[Settings], Awaitable[RuntimeServices]]


def get_runtime(ctx: Context) -> RuntimeServices:
    runtime = ctx.lifespan_context.get("runtime")
    if not isinstance(runtime, RuntimeServices):
        raise RuntimeError("Runtime services are unavailable outside the server lifespan.")
    return runtime


def get_principal(ctx: Context) -> PrincipalContext:
    """Derive application ownership from local mode or the verified token."""
    settings = get_runtime(ctx).settings
    if settings.mcp_auth_mode.value == "local":
        return PrincipalContext()

    token = get_access_token()
    if token is None:
        raise PermissionError("An authenticated access token is required.")
    claims = token.claims or {}
    principal_id = claims.get("sub") or token.subject or token.client_id
    tenant_id = claims.get(settings.mcp_tenant_claim)
    if not isinstance(principal_id, str) or not principal_id:
        raise PermissionError("The access token is missing a principal identity.")
    if not isinstance(tenant_id, str) or not tenant_id:
        raise PermissionError(
            f"The access token is missing tenant claim '{settings.mcp_tenant_claim}'."
        )
    return PrincipalContext(
        principal_id=principal_id,
        tenant_id=tenant_id,
        scopes=frozenset(token.scopes),
        transport="http",
    )


@asynccontextmanager
async def billed_provider_slot(
    ctx: Context,
    *,
    provider: ProviderKey,
    product: str,
    estimated_cost_usd: float,
) -> AsyncIterator[None]:
    """Reserve budget and acquire shared limits around one billable call.

    The reservation is committed when the call succeeds, when the provider
    error is ambiguous, or when it is a ``TIMEOUT`` (a timed-out completion may
    still have consumed billable tokens). It is released for other errors.
    On cancellation it is committed if the provider call had already started
    (the outcome is unknown) and released if the call was still waiting for a
    limiter slot, so a cancelled call never leaves a dangling reservation.
    """
    runtime = get_runtime(ctx)
    owner = get_principal(ctx)
    reservation = await runtime.budget_ledger.reserve(
        owner,
        CostEstimate(product=product, amount_usd=estimated_cost_usd),
    )
    dispatched = False
    try:
        async with runtime.provider_limiters.acquire(provider, owner):
            dispatched = True
            yield
    except ProviderError as exc:
        if exc.ambiguous_completion or exc.code == "TIMEOUT":
            await runtime.budget_ledger.commit(reservation)
        else:
            await runtime.budget_ledger.release(reservation)
        raise
    except asyncio.CancelledError:
        with suppress(Exception):
            if dispatched:
                await asyncio.shield(runtime.budget_ledger.commit(reservation))
            else:
                await asyncio.shield(runtime.budget_ledger.release(reservation))
        raise
    except Exception:
        await runtime.budget_ledger.release(reservation)
        raise
    else:
        await runtime.budget_ledger.commit(reservation)


async def _state_sweeper(runtime: RuntimeServices, settings: Settings) -> None:
    """Periodically sweep expired artifacts and prune aged state rows."""
    while True:
        await asyncio.sleep(settings.artifact_sweep_interval_seconds)
        try:
            deleted = await runtime.artifact_store.delete_expired(datetime.now(UTC))
            log_info("state_sweep_artifacts", deleted=deleted)
        except Exception as exc:
            log_warning("state_sweep_artifact_error", error=str(exc))
        try:
            pruned = await runtime.ownership_store.prune(settings.state_prune_max_age_days)
            log_info("state_sweep_ownership", pruned=pruned)
        except Exception as exc:
            log_warning("state_sweep_ownership_error", error=str(exc))
        try:
            pruned = await runtime.object_key_ownership_store.prune(
                settings.state_prune_max_age_days
            )
            log_info("state_sweep_object_keys", pruned=pruned)
        except Exception as exc:
            log_warning("state_sweep_object_keys_error", error=str(exc))
        try:
            pruned = await runtime.budget_ledger.prune(settings.state_prune_max_age_days)
            log_info("state_sweep_budget", pruned=pruned)
        except Exception as exc:
            log_warning("state_sweep_budget_error", error=str(exc))
        try:
            pruned = await runtime.task_artifact_cache.prune_expired()
            log_info("state_sweep_cache", pruned=pruned)
        except Exception as exc:
            log_warning("state_sweep_cache_error", error=str(exc))


def build_lifespan(
    settings: Settings,
    runtime_factory: RuntimeFactory = create_runtime_services,
    state: RuntimeState | None = None,
) -> Callable[
    [FastMCP[dict[str, object]]],
    AbstractAsyncContextManager[dict[str, object]],
]:
    @asynccontextmanager
    async def server_lifespan(
        _server: FastMCP[dict[str, object]],
    ) -> AsyncIterator[dict[str, object]]:
        runtime = await runtime_factory(settings)
        if state is not None:
            state.runtime = runtime
        sweeper = asyncio.create_task(_state_sweeper(runtime, settings))
        try:
            yield {"runtime": runtime}
        finally:
            sweeper.cancel()
            with suppress(asyncio.CancelledError):
                await sweeper
            if state is not None:
                state.runtime = None
            await close_runtime_services(runtime)

    return server_lifespan
