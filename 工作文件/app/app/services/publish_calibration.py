"""发布实验的可靠校准闭环。

当前状态保存在 SQLite ``experiments`` 表中；每一步状态变化同时追加到
``experiment_events``，事件表由触发器禁止更新和删除。SQLite 使用 WAL、完整同步和
显式事务，避免旧 JSONL 全量重写带来的截断与并发丢失。

如果同目录存在旧 ``ledger.jsonl``，首次打开数据库时只读导入，原文件不会改名、
覆盖或删除。导入记录保留原始结构，因此旧记录仍可人工追溯；新记录使用 schema v2。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCORE_DIMENSIONS = [
    "问题强度",
    "受众匹配",
    "首屏钩子",
    "具体证据",
    "表达清晰",
    "可传播性",
    "执行成本",
]
SCORE_MIN = 1
SCORE_MAX = 5

# 与项目019的真实回填口径保持一致。除 retention 外均为非负整数计数。
METRIC_KEYS = [
    "impressions",
    "views",
    "likes",
    "comments",
    "shares",
    "followers",
    "retention",
]
METRIC_LABELS = {
    "impressions": "曝光量",
    "views": "播放量",
    "likes": "点赞数",
    "comments": "评论数",
    "shares": "分享数",
    "followers": "新增粉丝",
    "retention": "留存率",
}
METRIC_UNITS = {
    "impressions": "count",
    "views": "count",
    "likes": "count",
    "comments": "count",
    "shares": "count",
    "followers": "count",
    "retention": "percent",
}
DEFAULT_WINDOW_HOURS = 72
MINIMUM_CALIBRATION_SAMPLE_SIZE = 3
_SCHEMA_INITIALISE_LOCK = threading.Lock()

ExperimentStatus = Literal["predicted", "published", "measured", "reviewed"]


def _now() -> str:
    """返回带时区的 ISO 时间，避免不同机器解释无时区时间时产生歧义。"""
    return datetime.now(timezone.utc).astimezone().isoformat()


def _normalise_timestamp(value: str, field_name: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise ValueError(f"{field_name} 不能为空")
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是 ISO 日期或时间") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.isoformat()


def _valid_http_url(value: str, field_name: str) -> str:
    candidate = value.strip()
    parts = urlsplit(candidate)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"{field_name} 必须是 http/https 链接")
    return candidate


def _validate_metric_value(key: str, value: float, *, field_name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name}.{key} 必须是有限数字")
    if number < 0:
        raise ValueError(f"{field_name}.{key} 不能为负数")
    if key == "retention":
        if number > 100:
            raise ValueError(f"{field_name}.retention 必须在 0..100（百分比）")
    elif not number.is_integer():
        raise ValueError(f"{field_name}.{key} 必须是整数计数")
    return number


class DimensionScore(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    dimension: str
    score: int
    note: str = Field(default="", max_length=2000)

    @field_validator("dimension")
    @classmethod
    def _dimension_must_be_known(cls, value: str) -> str:
        if value not in SCORE_DIMENSIONS:
            raise ValueError(f"dimension 必须是 {SCORE_DIMENSIONS} 之一")
        return value

    @field_validator("score", mode="before")
    @classmethod
    def _score_not_boolean(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("score 不能是布尔值")
        return value

    @field_validator("score")
    @classmethod
    def _score_in_range(cls, value: int) -> int:
        if not (SCORE_MIN <= value <= SCORE_MAX):
            raise ValueError(f"score 必须在 {SCORE_MIN}..{SCORE_MAX}")
        return value


class PredictedMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: str
    low: float
    high: float

    @field_validator("key")
    @classmethod
    def _key_must_be_known(cls, value: str) -> str:
        if value not in METRIC_KEYS:
            raise ValueError(f"key 必须是 {METRIC_KEYS} 之一")
        return value

    @field_validator("low", "high", mode="before")
    @classmethod
    def _bound_not_boolean(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("预测边界不能是布尔值")
        return value

    @model_validator(mode="after")
    def _valid_interval(self) -> PredictedMetric:
        self.low = _validate_metric_value(self.key, self.low, field_name="predictions.low")
        self.high = _validate_metric_value(self.key, self.high, field_name="predictions.high")
        if self.high < self.low:
            raise ValueError("high 不能小于 low")
        return self


class PublishExperimentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=300)
    source_url: str | None = Field(default=None, max_length=2048)
    analysis_ref: str | None = Field(default=None, max_length=300)
    source_topic_id: str | None = Field(default=None, pattern=r"^dy_[A-Za-z0-9_]+$", max_length=64)
    content_summary: str | None = Field(default=None, max_length=20000)
    platform: str = Field(min_length=1, max_length=200)
    hypothesis: str | None = Field(default=None, max_length=5000)
    window_hours: int = Field(default=DEFAULT_WINDOW_HOURS, ge=1, le=24 * 365)
    # 评分与指标区间都是可选的发布前复盘基线。旧客户端提交完整列表时仍按原契约校验。
    scores: list[DimensionScore] = Field(
        default_factory=list,
        max_length=len(SCORE_DIMENSIONS),
    )
    predictions: list[PredictedMetric] = Field(
        default_factory=list,
        max_length=len(METRIC_KEYS),
    )

    @field_validator("source_url")
    @classmethod
    def _source_url_if_present(cls, value: str | None) -> str | None:
        return _valid_http_url(value, "source_url") if value else None

    @field_validator("window_hours", mode="before")
    @classmethod
    def _prediction_window_not_boolean(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("window_hours 不能是布尔值")
        return value

    @model_validator(mode="after")
    def _complete_unique_inputs(self) -> PublishExperimentCreate:
        dimensions = [item.dimension for item in self.scores]
        if len(set(dimensions)) != len(dimensions):
            raise ValueError("scores 不能包含重复维度")

        metric_keys = [item.key for item in self.predictions]
        if len(set(metric_keys)) != len(metric_keys):
            raise ValueError("predictions 不能包含重复指标")
        return self


class PublishRecordInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    publish_url: str = Field(min_length=1, max_length=2048)
    platform: str | None = Field(default=None, min_length=1, max_length=200)
    published_at: str | None = Field(default=None, max_length=64)
    # 兼容 DSH 版本的字段；落盘时统一为 published_at，并保留派生的 publish_date。
    publish_date: str | None = Field(default=None, max_length=64)

    @field_validator("publish_url")
    @classmethod
    def _valid_publish_url(cls, value: str) -> str:
        return _valid_http_url(value, "publish_url")

    @field_validator("published_at", "publish_date")
    @classmethod
    def _valid_publish_time(cls, value: str | None, info: Any) -> str | None:
        return _normalise_timestamp(value, info.field_name) if value else None

    @model_validator(mode="after")
    def _one_consistent_publish_time(self) -> PublishRecordInput:
        if self.published_at is None and self.publish_date is None:
            raise ValueError("published_at 不能为空（兼容字段 publish_date 也可）")
        if (
            self.published_at is not None
            and self.publish_date is not None
            and self.published_at != self.publish_date
        ):
            raise ValueError("published_at 与 publish_date 不能冲突")
        return self


class PublishBackfillInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    metrics: dict[str, float] = Field(min_length=1, max_length=len(METRIC_KEYS))
    window_hours: int = Field(default=DEFAULT_WINDOW_HOURS, ge=1, le=24 * 365)
    observed_at: str = Field(default_factory=_now, max_length=64)
    data_source: str = Field(default="人工回填", min_length=1, max_length=300)
    note: str | None = Field(default=None, max_length=5000)

    @field_validator("window_hours", mode="before")
    @classmethod
    def _window_not_boolean(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("window_hours 不能是布尔值")
        return value

    @field_validator("observed_at")
    @classmethod
    def _valid_observed_at(cls, value: str) -> str:
        return _normalise_timestamp(value, "observed_at")

    @field_validator("metrics", mode="before")
    @classmethod
    def _raw_metrics_not_boolean(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for key, metric_value in value.items():
                if isinstance(metric_value, bool):
                    raise ValueError(f"metrics.{key} 不能是布尔值")
        return value

    @field_validator("metrics")
    @classmethod
    def _valid_metrics(cls, value: dict[str, float]) -> dict[str, float]:
        normalised: dict[str, float] = {}
        for key, metric_value in value.items():
            if key not in METRIC_KEYS:
                raise ValueError(f"metrics 包含未知指标: {key}")
            normalised[key] = _validate_metric_value(
                key,
                metric_value,
                field_name="metrics",
            )
        return normalised


class PublishReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    note: str | None = Field(default=None, max_length=5000)


class PublishCalibrationError(Exception):
    """发布校准领域异常的基类。"""


class PublishExperimentNotFoundError(PublishCalibrationError):
    def __init__(self, experiment_id: str) -> None:
        self.experiment_id = experiment_id
        super().__init__(f"发布实验不存在: {experiment_id}")


class PublishCalibrationConflictError(PublishCalibrationError):
    """状态机冲突；HTTP 路由应映射为 409。"""

    def __init__(
        self,
        experiment_id: str,
        *,
        operation: str,
        current_status: str,
        expected_status: str,
    ) -> None:
        self.experiment_id = experiment_id
        self.operation = operation
        self.current_status = current_status
        self.expected_status = expected_status
        super().__init__(
            f"状态冲突: {operation} 要求实验 {experiment_id} 处于 {expected_status}，"
            f"当前为 {current_status}"
        )


class PublishCalibrationValidationError(PublishCalibrationError):
    """需要当前实验上下文才能判断的领域校验错误；路由宜映射为 422。"""


class PublishCalibrationStorageError(PublishCalibrationError):
    """旧账本无法安全迁移或存储内容损坏。"""


def _prediction_payload(prediction: PredictedMetric) -> dict[str, Any]:
    item = prediction.model_dump()
    item["unit"] = METRIC_UNITS[prediction.key]
    return item


def _content_snapshot_sha256(payload: PublishExperimentCreate) -> str:
    snapshot = {
        "title": payload.title,
        "analysis_ref": payload.analysis_ref,
        "source_topic_id": payload.source_topic_id,
        "content_summary": payload.content_summary,
    }
    serialised = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()


def _generate_suggestions(deviations: list[dict[str, Any]]) -> list[dict[str, str]]:
    """只提出待验证假设和单变量实验，不把相关性写成因果结论。"""
    if not deviations:
        return []

    suggestions: list[dict[str, str]] = []
    for deviation in deviations:
        if deviation["inside_interval"]:
            continue
        label = str(deviation["label"])
        low = deviation["predicted_low"]
        high = deviation["predicted_high"]
        actual = deviation["actual"]
        if actual < low:
            suggestions.append(
                {
                    "target": label,
                    "direction": "下一轮只校准该指标的预测区间",
                    "rationale": (
                        f"{label}实测值 {actual} 低于预测下限 {low}，当前证据只支持“预测偏乐观”；"
                        "不能据此断定钩子、选题或发布时间是原因。下一轮保持其他条件不变，"
                        "只下调该指标预测区间后再观察。"
                    ),
                }
            )
        else:
            suggestions.append(
                {
                    "target": label,
                    "direction": "下一轮只校准该指标的预测区间",
                    "rationale": (
                        f"{label}实测值 {actual} 高于预测上限 {high}，当前证据只支持“预测偏保守”；"
                        "不能据此证明某个内容因素有效。下一轮保持其他条件不变，"
                        "只上调该指标预测区间后再观察。"
                    ),
                }
            )

    if not suggestions:
        suggestions.append(
            {
                "target": "全部已回填指标",
                "direction": "保持区间，继续收集同口径样本",
                "rationale": (
                    "本轮实测均落在预测区间内；单个样本只能说明本次命中，"
                    "不能证明内容因素与结果存在因果关系。"
                ),
            }
        )
    return suggestions


def _build_learning_note(
    record: dict[str, Any],
    summary: dict[str, Any],
) -> str:
    deviations = record.get("deviations", [])
    if not deviations:
        return (
            "本轮未登记可比较的发布前复盘基线；真实指标已保存，但不计入命中率或偏差统计。"
            "这里只保存发布复盘，不生成经验候选，也不作流量承诺。"
        )
    outside = [item for item in deviations if not item["inside_interval"]]
    if outside:
        detail = "、".join(str(item["label"]) for item in outside)
        finding = f"本轮有 {len(outside)} 项预测区间需校准（{detail}）"
    else:
        finding = "本轮已比较指标均命中预测区间"
    evidence = (
        "跨实验样本仍不足"
        if summary["evidence_insufficient"]
        else "跨实验样本已达到最低观察数量"
    )
    return (
        f"{finding}；{evidence}（有效复盘 {summary['sample_size']} 次）。"
        "这里只生成待人工确认的经验候选，不自动晋升项目017，也不作流量承诺。"
    )


class PublishCalibrationStore:
    """SQLite/WAL 发布实验存储，当前状态与追加式事件历史分离。"""

    def __init__(self) -> None:
        self._root: Path | None = None
        self._initialised = False

    @property
    def root(self) -> Path:
        if self._root is None:
            configured = os.environ.get("PROJECT024_PUBLISH_CALIBRATION_ROOT", "").strip()
            if configured:
                self._root = Path(configured).expanduser().resolve()
            else:
                self._root = Path(__file__).resolve().parents[2] / "var" / "publish_calibration"
            self._root.mkdir(parents=True, exist_ok=True)
        return self._root

    @property
    def database_path(self) -> Path:
        return self.root / "calibration.sqlite3"

    @property
    def ledger_path(self) -> Path:
        """旧版账本路径，仅用于只读迁移与兼容诊断。"""
        return self.root / "ledger.jsonl"

    def _open_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=30.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _ensure_initialised(self) -> None:
        if self._initialised:
            return
        # 同一进程内不同 Store 也必须共用初始化锁；每实例一把锁无法保护首次 WAL 切换。
        with _SCHEMA_INITIALISE_LOCK:
            if self._initialised:
                return
            connection = self._open_connection()
            try:
                self._ensure_wal(connection)
                connection.execute("PRAGMA synchronous = FULL")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS experiments (
                        id TEXT PRIMARY KEY,
                        status TEXT NOT NULL CHECK (
                            status IN ('predicted', 'published', 'measured', 'reviewed')
                        ),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        record_json TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS experiment_events (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id TEXT NOT NULL UNIQUE,
                        experiment_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        occurred_at TEXT NOT NULL,
                        record_version INTEGER NOT NULL,
                        record_json TEXT NOT NULL,
                        FOREIGN KEY (experiment_id) REFERENCES experiments(id)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS calibration_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS experiment_events_no_update
                    BEFORE UPDATE ON experiment_events
                    BEGIN
                        SELECT RAISE(ABORT, 'experiment_events is append-only');
                    END
                    """
                )
                connection.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS experiment_events_no_delete
                    BEFORE DELETE ON experiment_events
                    BEGIN
                        SELECT RAISE(ABORT, 'experiment_events is append-only');
                    END
                    """
                )
                self._migrate_legacy_jsonl(connection)
            finally:
                connection.close()
            self._initialised = True

    @staticmethod
    def _ensure_wal(connection: sqlite3.Connection) -> None:
        """可靠启用 WAL；跨进程首次初始化时对瞬时锁做有界短重试。"""
        for attempt in range(10):
            try:
                current = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
                if current == "wal":
                    return
                enabled = str(
                    connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
                ).lower()
                if enabled == "wal":
                    return
                raise PublishCalibrationStorageError(
                    f"无法启用 SQLite WAL，当前 journal_mode={enabled}"
                )
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 9:
                    raise
                time.sleep(0.02 * (attempt + 1))

    def _migrate_legacy_jsonl(self, connection: sqlite3.Connection) -> None:
        marker_key = "legacy_jsonl_migration_v1"
        marker = connection.execute(
            "SELECT value FROM calibration_meta WHERE key = ?",
            (marker_key,),
        ).fetchone()
        if marker is not None or not self.ledger_path.is_file():
            return

        original = self.ledger_path.read_bytes()
        file_sha256 = hashlib.sha256(original).hexdigest()
        parsed: list[tuple[int, dict[str, Any], str]] = []
        for line_number, raw_line in enumerate(original.splitlines(), start=1):
            if not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PublishCalibrationStorageError(
                    f"旧 ledger.jsonl 第 {line_number} 行无法安全解析，未执行迁移"
                ) from exc
            if not isinstance(record, dict) or not str(record.get("id", "")).strip():
                raise PublishCalibrationStorageError(
                    f"旧 ledger.jsonl 第 {line_number} 行缺少有效实验 id，未执行迁移"
                )
            status = str(record.get("status", "predicted"))
            if status not in {"predicted", "published", "measured", "reviewed"}:
                raise PublishCalibrationStorageError(
                    f"旧 ledger.jsonl 第 {line_number} 行包含未知状态 {status!r}，未执行迁移"
                )
            parsed.append((line_number, record, status))

        connection.execute("BEGIN IMMEDIATE")
        try:
            # 另一个进程/Store 可能在本连接读取 marker 后先完成迁移；取得写锁后
            # 必须再次检查，避免重复导入或争抢 calibration_meta 主键。
            marker = connection.execute(
                "SELECT value FROM calibration_meta WHERE key = ?",
                (marker_key,),
            ).fetchone()
            if marker is not None:
                connection.execute("COMMIT")
                return

            latest: dict[str, tuple[dict[str, Any], str, int]] = {}
            for line_number, record, status in parsed:
                experiment_id = str(record["id"])
                latest[experiment_id] = (record, status, line_number)

            for experiment_id, (record, status, line_number) in latest.items():
                created_at = str(record.get("created_at") or _now())
                updated_at = str(record.get("updated_at") or created_at)
                version = sum(1 for _, item, _ in parsed if str(item["id"]) == experiment_id)
                connection.execute(
                    """
                    INSERT INTO experiments (
                        id, status, created_at, updated_at, version, record_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO NOTHING
                    """,
                    (
                        experiment_id,
                        status,
                        created_at,
                        updated_at,
                        version,
                        self._dump_record(record),
                    ),
                )

            versions: dict[str, int] = {}
            for line_number, record, status in parsed:
                experiment_id = str(record["id"])
                versions[experiment_id] = versions.get(experiment_id, 0) + 1
                event_seed = f"{file_sha256}:{line_number}:{experiment_id}"
                event_id = "legacy_" + hashlib.sha256(event_seed.encode("utf-8")).hexdigest()[:32]
                event_type = "legacy_reviewed" if status == "reviewed" else "legacy_import"
                occurred_at = str(record.get("updated_at") or record.get("created_at") or _now())
                connection.execute(
                    """
                    INSERT OR IGNORE INTO experiment_events (
                        event_id, experiment_id, event_type, occurred_at,
                        record_version, record_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        experiment_id,
                        event_type,
                        occurred_at,
                        versions[experiment_id],
                        self._dump_record(record),
                    ),
                )

            marker_value = self._dump_record(
                {
                    "source": str(self.ledger_path),
                    "sha256": file_sha256,
                    "records": len(parsed),
                    "migrated_at": _now(),
                }
            )
            connection.execute(
                "INSERT INTO calibration_meta (key, value) VALUES (?, ?)",
                (marker_key, marker_value),
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _dump_record(record: dict[str, Any]) -> str:
        return json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        self._ensure_initialised()
        connection = self._open_connection()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _write_connection(self) -> Iterator[sqlite3.Connection]:
        self._ensure_initialised()
        connection = self._open_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _next_id(self) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"pub_{stamp}_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _row_record(row: sqlite3.Row) -> dict[str, Any]:
        try:
            record = json.loads(str(row["record_json"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise PublishCalibrationStorageError(
                f"实验 {row['id']} 的当前记录已损坏"
            ) from exc
        if not isinstance(record, dict):
            raise PublishCalibrationStorageError(f"实验 {row['id']} 的当前记录不是 JSON 对象")
        return record

    def _get_in_connection(
        self,
        connection: sqlite3.Connection,
        experiment_id: str,
    ) -> tuple[dict[str, Any], int]:
        row = connection.execute(
            "SELECT id, version, record_json FROM experiments WHERE id = ?",
            (experiment_id,),
        ).fetchone()
        if row is None:
            raise PublishExperimentNotFoundError(experiment_id)
        return self._row_record(row), int(row["version"])

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        *,
        experiment_id: str,
        event_type: str,
        occurred_at: str,
        record_version: int,
        record: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO experiment_events (
                event_id, experiment_id, event_type, occurred_at,
                record_version, record_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"evt_{uuid.uuid4().hex}",
                experiment_id,
                event_type,
                occurred_at,
                record_version,
                self._dump_record(record),
            ),
        )

    def _replace_current(
        self,
        connection: sqlite3.Connection,
        *,
        record: dict[str, Any],
        previous_version: int,
        expected_status: str,
        event_type: str,
    ) -> int:
        new_version = previous_version + 1
        cursor = connection.execute(
            """
            UPDATE experiments
            SET status = ?, updated_at = ?, version = ?, record_json = ?
            WHERE id = ? AND status = ? AND version = ?
            """,
            (
                record["status"],
                record["updated_at"],
                new_version,
                self._dump_record(record),
                record["id"],
                expected_status,
                previous_version,
            ),
        )
        if cursor.rowcount != 1:
            current, _ = self._get_in_connection(connection, str(record["id"]))
            raise PublishCalibrationConflictError(
                str(record["id"]),
                operation=event_type,
                current_status=str(current.get("status")),
                expected_status=expected_status,
            )
        self._insert_event(
            connection,
            experiment_id=str(record["id"]),
            event_type=event_type,
            occurred_at=str(record["updated_at"]),
            record_version=new_version,
            record=record,
        )
        return new_version

    @staticmethod
    def _assert_status(
        record: dict[str, Any],
        *,
        expected: str,
        operation: str,
    ) -> None:
        current = str(record.get("status"))
        if current != expected:
            raise PublishCalibrationConflictError(
                str(record.get("id")),
                operation=operation,
                current_status=current,
                expected_status=expected,
            )

    def create(self, payload: PublishExperimentCreate) -> dict[str, Any]:
        now = _now()
        record: dict[str, Any] = {
            "schema_version": 2,
            "id": self._next_id(),
            "status": "predicted",
            "created_at": now,
            "updated_at": now,
            "title": payload.title,
            "source_url": payload.source_url,
            "analysis_ref": payload.analysis_ref,
            "source_topic_id": payload.source_topic_id,
            "content_summary": payload.content_summary,
            "content_snapshot_sha256": _content_snapshot_sha256(payload),
            "platform": payload.platform,
            "hypothesis": payload.hypothesis,
            "scores": [score.model_dump() for score in payload.scores],
            "predictions": [_prediction_payload(item) for item in payload.predictions],
            "prediction_window_hours": payload.window_hours,
            "publish_url": None,
            "published_at": None,
            "publish_date": None,
            "actual_metrics": {},
            "metric_units": {},
            "window_hours": None,
            "observed_at": None,
            "data_source": None,
            "backfill_note": None,
            "deviations": [],
            "next_suggestions": [],
            "learning_candidate": False,
            "learning_note": None,
            "calibration_summary": None,
        }
        with self._write_connection() as connection:
            connection.execute(
                """
                INSERT INTO experiments (
                    id, status, created_at, updated_at, version, record_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["status"],
                    now,
                    now,
                    1,
                    self._dump_record(record),
                ),
            )
            self._insert_event(
                connection,
                experiment_id=str(record["id"]),
                event_type="created",
                occurred_at=now,
                record_version=1,
                record=record,
            )
        return record

    def publish(self, experiment_id: str, payload: PublishRecordInput) -> dict[str, Any]:
        with self._write_connection() as connection:
            record, version = self._get_in_connection(connection, experiment_id)
            self._assert_status(record, expected="predicted", operation="publish")
            published_at = payload.published_at or payload.publish_date
            assert published_at is not None  # guaranteed by PublishRecordInput validation
            now = _now()
            record["status"] = "published"
            record["platform"] = payload.platform or record["platform"]
            record["publish_url"] = payload.publish_url
            record["published_at"] = published_at
            record["publish_date"] = published_at[:10]
            record["updated_at"] = now
            self._replace_current(
                connection,
                record=record,
                previous_version=version,
                expected_status="predicted",
                event_type="published",
            )
        return record

    def backfill(self, experiment_id: str, payload: PublishBackfillInput) -> dict[str, Any]:
        with self._write_connection() as connection:
            record, version = self._get_in_connection(connection, experiment_id)
            self._assert_status(record, expected="published", operation="backfill")

            # 登记时即锁定观察窗，避免发布后挑选更有利的时间口径。旧迁移记录没有
            # 此字段时按项目既有默认 T+72h 解释，但不修改旧 JSONL 源文件。
            prediction_window_raw = record.get(
                "prediction_window_hours",
                DEFAULT_WINDOW_HOURS,
            )
            try:
                if isinstance(prediction_window_raw, bool):
                    raise ValueError
                prediction_window_number = float(prediction_window_raw)
                if (
                    not math.isfinite(prediction_window_number)
                    or not prediction_window_number.is_integer()
                    or not 1 <= prediction_window_number <= 24 * 365
                ):
                    raise ValueError
                prediction_window = int(prediction_window_number)
            except (TypeError, ValueError) as exc:
                raise PublishCalibrationStorageError(
                    f"实验 {experiment_id} 的预测观察窗已损坏"
                ) from exc
            if prediction_window != payload.window_hours:
                raise PublishCalibrationValidationError(
                    f"回填观察窗 {payload.window_hours} 小时与登记时观察窗 "
                    f"{prediction_window} 小时不一致，不能比较"
                )

            published_at_raw = record.get("published_at") or record.get("publish_date")
            if not published_at_raw:
                raise PublishCalibrationValidationError(
                    "发布记录缺少 published_at，无法验证观察时间顺序"
                )
            try:
                published_at = datetime.fromisoformat(
                    _normalise_timestamp(str(published_at_raw), "published_at")
                )
                observed_at = datetime.fromisoformat(payload.observed_at)
            except ValueError as exc:
                raise PublishCalibrationValidationError(
                    "发布或观察时间无效，无法验证时间顺序"
                ) from exc
            if observed_at < published_at:
                raise PublishCalibrationValidationError(
                    "observed_at 不能早于 published_at"
                )

            now = _now()
            record["actual_metrics"] = dict(payload.metrics)
            record["prediction_window_hours"] = prediction_window
            record["metric_units"] = {
                key: METRIC_UNITS[key] for key in payload.metrics
            }
            record["window_hours"] = payload.window_hours
            record["observed_at"] = payload.observed_at
            record["data_source"] = payload.data_source
            record["backfill_note"] = payload.note
            record["status"] = "measured"
            record["updated_at"] = now
            self._replace_current(
                connection,
                record=record,
                previous_version=version,
                expected_status="published",
                event_type="measured",
            )
        return record

    def review(self, experiment_id: str, note: str | None = None) -> dict[str, Any]:
        with self._write_connection() as connection:
            record, version = self._get_in_connection(connection, experiment_id)
            self._assert_status(record, expected="measured", operation="review")

            predictions = {
                str(item["key"]): item for item in record.get("predictions", [])
            }
            actual_metrics = record.get("actual_metrics", {})
            deviations: list[dict[str, Any]] = []
            for key, actual_raw in actual_metrics.items():
                prediction = predictions.get(str(key))
                if prediction is None:
                    continue
                actual = float(actual_raw)
                low = float(prediction["low"])
                high = float(prediction["high"])
                midpoint = (low + high) / 2
                inside = low <= actual <= high
                error = actual - midpoint
                relative_error = abs(error) / midpoint if midpoint > 0 else None
                label = METRIC_LABELS[str(key)]
                if inside:
                    bias = "within_interval"
                    note_text = f"{label}落在预测区间内"
                elif actual < low:
                    bias = "prediction_optimistic"
                    note_text = f"{label}低于预测区间下限，预测偏乐观"
                else:
                    bias = "prediction_conservative"
                    note_text = f"{label}高于预测区间上限，预测偏保守"
                deviations.append(
                    {
                        "key": key,
                        "label": label,
                        "unit": METRIC_UNITS[str(key)],
                        "predicted_low": low,
                        "predicted_high": high,
                        "actual": actual,
                        "inside_interval": inside,
                        "bias": bias,
                        "error": round(error, 4),
                        "magnitude": round(relative_error, 4) if relative_error is not None else None,
                        "note": note_text,
                    }
                )

            now = _now()
            record["deviations"] = deviations
            record["next_suggestions"] = _generate_suggestions(deviations)
            record["learning_candidate"] = bool(deviations)
            record["status"] = "reviewed"
            record["updated_at"] = now
            summary = self._calibration_summary_from_events(
                connection,
                extra_record=record,
            )
            record["calibration_summary"] = summary
            record["learning_note"] = note or _build_learning_note(record, summary)
            self._replace_current(
                connection,
                record=record,
                previous_version=version,
                expected_status="measured",
                event_type="reviewed",
            )
        return record

    def get(self, experiment_id: str) -> dict[str, Any]:
        with self._read_connection() as connection:
            record, _ = self._get_in_connection(connection, experiment_id)
            return record

    def list_all(self) -> list[dict[str, Any]]:
        with self._read_connection() as connection:
            rows = connection.execute(
                "SELECT id, record_json FROM experiments ORDER BY created_at DESC, id DESC"
            ).fetchall()
            return [self._row_record(row) for row in rows]

    def list_events(self, experiment_id: str | None = None) -> list[dict[str, Any]]:
        """返回不可变事件历史，主要供审计、迁移验证与诊断使用。"""
        with self._read_connection() as connection:
            if experiment_id is None:
                rows = connection.execute(
                    """
                    SELECT sequence, event_id, experiment_id, event_type,
                           occurred_at, record_version, record_json
                    FROM experiment_events ORDER BY sequence
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT sequence, event_id, experiment_id, event_type,
                           occurred_at, record_version, record_json
                    FROM experiment_events
                    WHERE experiment_id = ? ORDER BY sequence
                    """,
                    (experiment_id,),
                ).fetchall()
            return [
                {
                    "sequence": int(row["sequence"]),
                    "event_id": str(row["event_id"]),
                    "experiment_id": str(row["experiment_id"]),
                    "event_type": str(row["event_type"]),
                    "occurred_at": str(row["occurred_at"]),
                    "record_version": int(row["record_version"]),
                    "record": json.loads(str(row["record_json"])),
                }
                for row in rows
            ]

    def _calibration_summary_from_events(
        self,
        connection: sqlite3.Connection,
        *,
        extra_record: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rows = connection.execute(
            """
            SELECT experiment_id, record_json
            FROM experiment_events
            WHERE event_type IN ('reviewed', 'legacy_reviewed')
            ORDER BY sequence
            """
        ).fetchall()
        latest_by_experiment: dict[str, dict[str, Any]] = {}
        for row in rows:
            try:
                candidate = json.loads(str(row["record_json"]))
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                latest_by_experiment[str(row["experiment_id"])] = candidate
        if extra_record is not None:
            latest_by_experiment[str(extra_record["id"])] = extra_record

        comparable_records = [
            record
            for record in latest_by_experiment.values()
            if isinstance(record.get("deviations"), list) and record["deviations"]
        ]
        observations = [
            deviation
            for record in comparable_records
            for deviation in record["deviations"]
            if isinstance(deviation, dict) and "inside_interval" in deviation
        ]
        hits = sum(1 for item in observations if bool(item["inside_interval"]))
        hit_rate = round(hits / len(observations), 4) if observations else None
        sample_size = len(comparable_records)
        evidence_insufficient = (
            sample_size < MINIMUM_CALIBRATION_SAMPLE_SIZE or not observations
        )
        return {
            "sample_size": sample_size,
            "metric_observations": len(observations),
            "interval_hits": hits,
            "hit_rate": hit_rate,
            "minimum_sample_size": MINIMUM_CALIBRATION_SAMPLE_SIZE,
            "evidence_insufficient": evidence_insufficient,
            "message": (
                f"有基线的有效复盘仅 {sample_size} 次，证据不足，不能据此概括稳定规律。"
                if evidence_insufficient
                else f"已累计 {sample_size} 次有基线的有效复盘；命中率仅用于校准记录，不代表因果关系。"
            ),
        }

    def calibration_summary(self) -> dict[str, Any]:
        """基于追加式 reviewed 事件汇总有发布前基线的指标命中情况。"""
        with self._read_connection() as connection:
            return self._calibration_summary_from_events(connection)
