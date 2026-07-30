from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from .models import Claim, DealEvent, OfficialApp, Transition, decimal_text


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decimal(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def connection(self):
        conn = self.connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def init(self) -> None:
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    stats_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS apps (
                    app_id TEXT NOT NULL,
                    region TEXT NOT NULL,
                    title TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    url TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY(app_id, region)
                );

                CREATE TABLE IF NOT EXISTS source_claims (
                    claim_key TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    app_id TEXT NOT NULL,
                    region TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    deal_kind TEXT NOT NULL,
                    old_price TEXT,
                    new_price TEXT,
                    currency TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    captured_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_claim_app_region
                    ON source_claims(app_id, region, captured_at);

                CREATE TABLE IF NOT EXISTS price_state (
                    app_id TEXT NOT NULL,
                    region TEXT NOT NULL,
                    current_price TEXT NOT NULL,
                    previous_price TEXT,
                    currency TEXT NOT NULL,
                    observed_min TEXT NOT NULL,
                    observation_count INTEGER NOT NULL,
                    cycle INTEGER NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    PRIMARY KEY(app_id, region)
                );

                CREATE TABLE IF NOT EXISTS price_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    app_id TEXT NOT NULL,
                    region TEXT NOT NULL,
                    price TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    source TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    UNIQUE(run_id, app_id, region)
                );
                CREATE INDEX IF NOT EXISTS idx_observation_app_region
                    ON price_observations(app_id, region, id);

                CREATE TABLE IF NOT EXISTS alerts (
                    event_key TEXT PRIMARY KEY,
                    consumer TEXT NOT NULL,
                    app_id TEXT NOT NULL,
                    region TEXT NOT NULL,
                    title TEXT NOT NULL,
                    alert_type TEXT NOT NULL,
                    old_price TEXT,
                    new_price TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_health (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    item_count INTEGER NOT NULL,
                    app_id_rate REAL NOT NULL,
                    price_claim_rate REAL NOT NULL,
                    latest_published_at TEXT NOT NULL,
                    error TEXT NOT NULL,
                    captured_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ai_cache (
                    cache_key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                """
            )

    def start_run(self, kind: str) -> str:
        run_id = f"{kind}-{uuid.uuid4().hex}"
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO runs(run_id, kind, status, started_at) VALUES(?,?,?,?)",
                (run_id, kind, "running", utc_now()),
            )
        return run_id

    def finish_run(
        self, run_id: str, status: str, stats: dict | None = None, error: str = ""
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status=?, finished_at=?, stats_json=?, error=?
                WHERE run_id=?
                """,
                (
                    status,
                    utc_now(),
                    json.dumps(stats or {}, ensure_ascii=False),
                    error,
                    run_id,
                ),
            )

    def record_claims(self, claims: Iterable[Claim]) -> None:
        now = utc_now()
        with self.connection() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO source_claims(
                    claim_key, source, source_id, app_id, region, title, url,
                    published_at, deal_kind, old_price, new_price, currency,
                    payload_json, captured_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        claim.claim_key,
                        claim.source,
                        claim.source_id,
                        claim.app_id,
                        claim.region,
                        claim.title,
                        claim.url,
                        claim.published_at,
                        claim.deal_kind,
                        decimal_text(claim.old_price),
                        decimal_text(claim.new_price),
                        claim.currency,
                        json.dumps(claim.as_json(), ensure_ascii=False),
                        now,
                    )
                    for claim in claims
                ],
            )

    def observe(
        self, app: OfficialApp, run_id: str, source: str
    ) -> Transition:
        now = utc_now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM price_state WHERE app_id=? AND region=?",
                (app.app_id, app.region),
            ).fetchone()
            if row is None:
                previous = None
                minimum_before = None
                count_before = 0
                cycle = 0
                changed = False
                conn.execute(
                    """
                    INSERT INTO price_state(
                        app_id, region, current_price, previous_price, currency,
                        observed_min, observation_count, cycle, first_seen_at,
                        last_seen_at, changed_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        app.app_id,
                        app.region,
                        decimal_text(app.price),
                        None,
                        app.currency,
                        decimal_text(app.price),
                        1,
                        cycle,
                        now,
                        now,
                        now,
                    ),
                )
            else:
                current_before = _decimal(row["current_price"])
                previous_distinct = _decimal(row["previous_price"])
                minimum_before = _decimal(row["observed_min"])
                count_before = int(row["observation_count"])
                changed = current_before != app.price
                if changed:
                    previous = current_before
                    cycle = int(row["cycle"]) + 1
                    changed_at = now
                else:
                    previous = previous_distinct
                    cycle = int(row["cycle"])
                    changed_at = row["changed_at"]
                new_min = min(minimum_before, app.price) if minimum_before is not None else app.price
                conn.execute(
                    """
                    UPDATE price_state
                    SET current_price=?, previous_price=?, currency=?, observed_min=?,
                        observation_count=?, cycle=?, last_seen_at=?, changed_at=?
                    WHERE app_id=? AND region=?
                    """,
                    (
                        decimal_text(app.price),
                        decimal_text(previous),
                        app.currency,
                        decimal_text(new_min),
                        count_before + 1,
                        cycle,
                        now,
                        changed_at,
                        app.app_id,
                        app.region,
                    ),
                )
            metadata = {
                "primary_genre_id": app.primary_genre_id,
                "primary_genre_name": app.primary_genre_name,
                "seller": app.seller,
                "version": app.version,
                "release_notes": app.release_notes,
                "updated_at": app.updated_at,
                "minimum_os": app.minimum_os,
                "rating": app.rating,
                "rating_count": app.rating_count,
                "artwork_url": app.artwork_url,
                "formatted_price": app.formatted_price,
                "description": app.description,
                "content_rating": app.content_rating,
                "languages": app.languages,
            }
            conn.execute(
                """
                INSERT INTO apps(
                    app_id, region, title, currency, url, metadata_json,
                    first_seen_at, last_seen_at
                ) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(app_id, region) DO UPDATE SET
                    title=excluded.title,
                    currency=excluded.currency,
                    url=excluded.url,
                    metadata_json=excluded.metadata_json,
                    last_seen_at=excluded.last_seen_at
                """,
                (
                    app.app_id,
                    app.region,
                    app.title,
                    app.currency,
                    app.url,
                    json.dumps(metadata, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO price_observations(
                    run_id, app_id, region, price, currency, source, captured_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    app.app_id,
                    app.region,
                    decimal_text(app.price),
                    app.currency,
                    source,
                    now,
                ),
            )
        return Transition(
            app_id=app.app_id,
            region=app.region,
            previous_price=previous,
            current_price=app.price,
            observed_min_before=minimum_before,
            observation_count_before=count_before,
            cycle=cycle,
            changed=changed,
        )

    def was_alerted(self, event_key: str) -> bool:
        with self.connection() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM alerts WHERE event_key=? LIMIT 1", (event_key,)
                ).fetchone()
                is not None
            )

    def record_alerts(self, consumer: str, events: Iterable[DealEvent]) -> None:
        now = utc_now()
        with self.connection() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO alerts(
                    event_key, consumer, app_id, region, title, alert_type,
                    old_price, new_price, confidence, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        event.event_key,
                        consumer,
                        event.app.app_id,
                        event.app.region,
                        event.app.title,
                        event.alert_type,
                        decimal_text(event.old_price),
                        decimal_text(event.new_price),
                        event.confidence,
                        now,
                    )
                    for event in events
                ],
            )

    def record_source_health(self, run_id: str, row: dict) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO source_health(
                    run_id, source, ok, latency_ms, item_count, app_id_rate,
                    price_claim_rate, latest_published_at, error, captured_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    row["source"],
                    int(bool(row["ok"])),
                    int(row["latency_ms"]),
                    int(row["item_count"]),
                    float(row["app_id_rate"]),
                    float(row["price_claim_rate"]),
                    row.get("latest_published_at", ""),
                    row.get("error", ""),
                    utc_now(),
                ),
            )

    def cleanup(
        self,
        observation_days: int = 180,
        claim_days: int = 30,
        health_days: int = 90,
        alert_days: int = 365,
    ) -> None:
        now = datetime.now(timezone.utc)
        cutoffs = {
            "observations": (now - timedelta(days=observation_days)).isoformat(),
            "claims": (now - timedelta(days=claim_days)).isoformat(),
            "health": (now - timedelta(days=health_days)).isoformat(),
            "alerts": (now - timedelta(days=alert_days)).isoformat(),
        }
        with self.connection() as conn:
            conn.execute(
                "DELETE FROM price_observations WHERE captured_at < ?",
                (cutoffs["observations"],),
            )
            conn.execute(
                "DELETE FROM source_claims WHERE captured_at < ?", (cutoffs["claims"],)
            )
            conn.execute(
                "DELETE FROM source_health WHERE captured_at < ?", (cutoffs["health"],)
            )
            conn.execute(
                "DELETE FROM runs WHERE finished_at IS NOT NULL AND finished_at < ?",
                (cutoffs["health"],),
            )
            conn.execute(
                "DELETE FROM alerts WHERE created_at < ?", (cutoffs["alerts"],)
            )
            conn.execute("DELETE FROM ai_cache WHERE expires_at < ?", (utc_now(),))

    def get_ai_cache(self, cache_key: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT response_json FROM ai_cache
                WHERE cache_key=? AND expires_at>=?
                """,
                (cache_key, utc_now()),
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["response_json"])
        except json.JSONDecodeError:
            return None

    def set_ai_cache(
        self,
        cache_key: str,
        provider: str,
        model: str,
        response: dict,
        ttl_hours: int,
    ) -> None:
        created = datetime.now(timezone.utc)
        expires = created + timedelta(hours=max(1, ttl_hours))
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO ai_cache(
                    cache_key, provider, model, response_json, created_at, expires_at
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    provider=excluded.provider,
                    model=excluded.model,
                    response_json=excluded.response_json,
                    created_at=excluded.created_at,
                    expires_at=excluded.expires_at
                """,
                (
                    cache_key,
                    provider,
                    model,
                    json.dumps(response, ensure_ascii=False),
                    created.isoformat(),
                    expires.isoformat(),
                ),
            )
