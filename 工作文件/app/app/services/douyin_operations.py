from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DouyinTopicStatus = Literal["idea", "draft", "ready"]
DOUYIN_TOPIC_STATUSES = ("idea", "draft", "ready")
_SCHEMA_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_douyin_url(value: str) -> str:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("source_url 必须是完整的 HTTP(S) 链接")
    if host != "douyin.com" and not host.endswith(".douyin.com"):
        raise ValueError("抖音选题只接受 douyin.com 来源链接")
    return value


class DouyinTopicCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=300)
    source_url: str = Field(min_length=1, max_length=2048)
    analysis_ref: str | None = Field(default=None, max_length=300)
    content_summary: str | None = Field(default=None, max_length=20000)
    hypothesis: str | None = Field(default=None, max_length=5000)
    status: DouyinTopicStatus = "draft"

    @field_validator("source_url")
    @classmethod
    def _source_must_be_douyin(cls, value: str) -> str:
        return _validate_douyin_url(value)


class DouyinTopicUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: DouyinTopicStatus | None = None
    content_summary: str | None = Field(default=None, max_length=20000)
    hypothesis: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _at_least_one_change(self) -> DouyinTopicUpdate:
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要更新的选题字段")
        return self


class DouyinTopicNotFoundError(Exception):
    def __init__(self, topic_id: str) -> None:
        super().__init__(f"未找到抖音选题：{topic_id}")


class DouyinTopicStorageError(Exception):
    """抖音选题存储不可用或数据损坏。"""


def _snapshot(payload: DouyinTopicCreate) -> dict[str, Any]:
    return {
        "title": payload.title,
        "source_url": payload.source_url,
        "analysis_ref": payload.analysis_ref,
        "content_summary": payload.content_summary,
        "hypothesis": payload.hypothesis,
    }


def _snapshot_sha256(payload: DouyinTopicCreate) -> str:
    serialised = json.dumps(
        _snapshot(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()


class DouyinTopicStore:
    """SQLite/WAL 抖音选题存储；完全相同的分析快照只保留一条。"""

    def __init__(self) -> None:
        self._root: Path | None = None
        self._initialised = False

    @property
    def root(self) -> Path:
        if self._root is None:
            configured = os.environ.get("PROJECT024_DOUYIN_OPERATIONS_ROOT", "").strip()
            self._root = (
                Path(configured).expanduser().resolve()
                if configured
                else Path(__file__).resolve().parents[2] / "var" / "douyin_operations"
            )
            self._root.mkdir(parents=True, exist_ok=True)
        return self._root

    @property
    def database_path(self) -> Path:
        return self.root / "douyin_operations.sqlite3"

    def _open_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=30.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _ensure_initialised(self) -> None:
        if self._initialised:
            return
        with _SCHEMA_LOCK:
            if self._initialised:
                return
            with closing(self._open_connection()) as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = FULL")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS douyin_topics (
                        id TEXT PRIMARY KEY,
                        fingerprint TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL CHECK (status IN ('idea', 'draft', 'ready')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        record_json TEXT NOT NULL
                    )
                    """
                )
            self._initialised = True

    @staticmethod
    def _load_record(row: sqlite3.Row) -> dict[str, Any]:
        try:
            record = json.loads(str(row["record_json"]))
        except (TypeError, ValueError) as exc:
            raise DouyinTopicStorageError("抖音选题数据无法解析") from exc
        if not isinstance(record, dict):
            raise DouyinTopicStorageError("抖音选题数据结构无效")
        return record

    @staticmethod
    def _dump_record(record: dict[str, Any]) -> str:
        return json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @staticmethod
    def _next_id() -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"dy_{stamp}_{secrets.token_hex(4)}"

    def create(self, payload: DouyinTopicCreate) -> dict[str, Any]:
        self._ensure_initialised()
        fingerprint = _snapshot_sha256(payload)
        now = _now()
        record: dict[str, Any] = {
            "schema_version": 1,
            "id": self._next_id(),
            "platform": "douyin",
            "status": payload.status,
            "created_at": now,
            "updated_at": now,
            "version": 1,
            **_snapshot(payload),
            "content_snapshot_sha256": fingerprint,
        }
        with closing(self._open_connection()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO douyin_topics (
                        id, fingerprint, status, created_at, updated_at, version, record_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["id"],
                        fingerprint,
                        record["status"],
                        now,
                        now,
                        1,
                        self._dump_record(record),
                    ),
                )
                connection.execute("COMMIT")
                record["deduplicated"] = False
                return record
            except sqlite3.IntegrityError:
                connection.execute("ROLLBACK")
                row = connection.execute(
                    "SELECT record_json FROM douyin_topics WHERE fingerprint = ?",
                    (fingerprint,),
                ).fetchone()
                if row is None:
                    raise DouyinTopicStorageError("抖音选题写入冲突")
                existing = self._load_record(row)
                existing["deduplicated"] = True
                return existing
            except sqlite3.Error as exc:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise DouyinTopicStorageError("抖音选题暂时无法保存") from exc

    def list_all(self, *, status: DouyinTopicStatus | None = None) -> list[dict[str, Any]]:
        self._ensure_initialised()
        query = "SELECT record_json FROM douyin_topics"
        params: tuple[Any, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY created_at DESC, id DESC"
        try:
            with closing(self._open_connection()) as connection:
                rows = connection.execute(query, params).fetchall()
        except sqlite3.Error as exc:
            raise DouyinTopicStorageError("抖音选题暂时无法读取") from exc
        return [self._load_record(row) for row in rows]

    def get(self, topic_id: str) -> dict[str, Any]:
        self._ensure_initialised()
        with closing(self._open_connection()) as connection:
            row = connection.execute(
                "SELECT record_json FROM douyin_topics WHERE id = ?",
                (topic_id,),
            ).fetchone()
        if row is None:
            raise DouyinTopicNotFoundError(topic_id)
        return self._load_record(row)

    def update(self, topic_id: str, payload: DouyinTopicUpdate) -> dict[str, Any]:
        self._ensure_initialised()
        with closing(self._open_connection()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT version, record_json FROM douyin_topics WHERE id = ?",
                    (topic_id,),
                ).fetchone()
                if row is None:
                    connection.execute("ROLLBACK")
                    raise DouyinTopicNotFoundError(topic_id)
                record = self._load_record(row)
                version = int(row["version"]) + 1
                now = _now()
                record.update(payload.model_dump(exclude_unset=True))
                record.update({"updated_at": now, "version": version})
                connection.execute(
                    """
                    UPDATE douyin_topics
                    SET status = ?, updated_at = ?, version = ?, record_json = ?
                    WHERE id = ?
                    """,
                    (record["status"], now, version, self._dump_record(record), topic_id),
                )
                connection.execute("COMMIT")
                return record
            except DouyinTopicNotFoundError:
                raise
            except sqlite3.Error as exc:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise DouyinTopicStorageError("抖音选题暂时无法更新") from exc
