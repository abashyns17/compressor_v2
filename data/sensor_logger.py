"""
Sensor Logger — SQLite time-series storage.
Logs every sensor reading for trend analysis and graph generation.

DB path resolution order:
  1. SENSOR_LOG_PATH environment variable (set this on Railway to a volume mount)
  2. data/logs/sensor_log.db relative to this file (local dev default)

On Railway: set SENSOR_LOG_PATH=/data/logs/sensor_log.db and mount a
volume at /data/logs to persist across deploys.
"""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Respect env var so Railway volume mount works without code changes
_env_path = os.environ.get("SENSOR_LOG_PATH")
DB_PATH = Path(_env_path) if _env_path else Path(__file__).parent / "logs" / "sensor_log.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sensor_readings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL,
                scenario    TEXT NOT NULL DEFAULT 'normal',
                P1          REAL, P2 REAL, P3 REAL, P4 REAL,
                T1          REAL, T2 REAL,
                PSW1        REAL,
                load_pct    REAL,
                ambient_f   REAL,
                P4_P3_delta REAL,
                T1_T2_delta REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS component_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT NOT NULL,
                scenario        TEXT NOT NULL DEFAULT 'normal',
                component_id    TEXT NOT NULL,
                health_pct      REAL,
                operating_hours REAL,
                is_fault_risk   INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fault_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL,
                scenario    TEXT,
                fault_code  TEXT,
                severity    TEXT,
                value       REAL,
                threshold   REAL,
                message     TEXT
            )
        """)
        conn.commit()


def log_reading(reading_dict: dict, scenario: str = "normal"):
    """Persist a sensor reading to the database."""
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO sensor_readings
                (timestamp, scenario, P1, P2, P3, P4, T1, T2, PSW1,
                 load_pct, ambient_f, P4_P3_delta, T1_T2_delta)
            VALUES
                (:timestamp, :scenario, :P1, :P2, :P3, :P4, :T1, :T2,
                 :PSW1, :load_pct, :ambient_f, :P4_P3_delta, :T1_T2_delta)
        """, {**reading_dict, "scenario": scenario})
        conn.commit()


def log_components(component_health: dict, scenario: str = "normal"):
    """Snapshot component health."""
    ts = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        for cid, data in component_health.items():
            conn.execute("""
                INSERT INTO component_snapshots
                    (timestamp, scenario, component_id, health_pct,
                     operating_hours, is_fault_risk)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (ts, scenario, cid,
                  data["health_pct"], data["operating_hours"],
                  1 if data["is_fault_risk"] else 0))
        conn.commit()


def log_fault(fault: dict, scenario: str = "normal"):
    """Record a fault event."""
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO fault_events
                (timestamp, scenario, fault_code, severity, value, threshold, message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            scenario,
            fault.get("code"),
            fault.get("severity"),
            fault.get("value"),
            fault.get("threshold"),
            fault.get("message", ""),
        ))
        conn.commit()


def get_recent_readings(limit: int = 100,
                         scenario: Optional[str] = None,
                         sensor: Optional[str] = None) -> list:
    """Fetch recent sensor readings, optionally filtered."""
    query = "SELECT * FROM sensor_readings"
    params = []
    conditions = []

    if scenario:
        conditions.append("scenario = ?")
        params.append(scenario)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        result = [dict(row) for row in rows]

    if sensor and result:
        return [{"timestamp": r["timestamp"], sensor: r.get(sensor)} for r in result]

    return result


def get_sensor_trend(sensor: str, hours_back: float = 24.0,
                      scenario: Optional[str] = None) -> list:
    """Time series for a single sensor over the last N hours."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) -
              timedelta(hours=hours_back)).isoformat()

    query = f"""
        SELECT timestamp, {sensor}
        FROM sensor_readings
        WHERE timestamp >= ?
    """
    params = [cutoff]

    if scenario:
        query += " AND scenario = ?"
        params.append(scenario)

    query += " ORDER BY timestamp ASC"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [{"timestamp": r["timestamp"], sensor: r[sensor]} for r in rows]


def get_fault_history(limit: int = 50) -> list:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM fault_events ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(row) for row in rows]


# Initialise on import
init_db()
