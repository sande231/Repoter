"""
Distance Tracker Agent - Tracks daily distance traveled by mode.

Usage:
    # Log distance (from any terminal, even while agent runs):
    python distance_tracker.py log 5.2 walking
    python distance_tracker.py log 12 driving
    python distance_tracker.py log 3 cycling

    # Check today's total:
    python distance_tracker.py today

    # Run the agent (reports to Synapse every 60s, with self-healing):
    python distance_tracker.py run
"""

import sys
import sqlite3
import asyncio
from datetime import datetime, date
from agent_sdk import AgentSDK
from structured_logger import StructuredLogger

DB_PATH = "distance_tracker.db"
VALID_MODES = {"walking", "driving", "cycling", "running", "transit"}


class DistanceTracker:
    """Agent that tracks and reports daily travel distance."""

    def __init__(self):
        self.agent_id = "distance-tracker"
        self.logger = StructuredLogger(agent_id=self.agent_id)
        self.sdk = AgentSDK(
            agent_id=self.agent_id,
            ingestion_url="http://localhost:5000",
            tags={"agent_type": "distance_tracker", "service": "Distance Tracker"},
        )
        self._init_db()

    def _init_db(self):
        """Create the distance log table if it doesn't exist."""
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS distance_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                log_date TEXT NOT NULL,
                mode TEXT NOT NULL,
                distance_km REAL NOT NULL,
                logged_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    # ---------- Logging distances ----------

    def log_distance(self, distance_km: float, mode: str) -> dict:
        """Log a distance entry. Returns today's updated totals."""
        mode = mode.lower().strip()
        if mode not in VALID_MODES:
            raise ValueError(f"Mode must be one of: {', '.join(sorted(VALID_MODES))}")
        if distance_km <= 0 or distance_km > 2000:
            raise ValueError("Distance must be between 0 and 2000 km")

        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO distance_log (log_date, mode, distance_km) VALUES (?, ?, ?)",
            (date.today().isoformat(), mode, distance_km),
        )
        conn.commit()
        conn.close()

        self.logger.log_event("distance_logged", mode=mode, distance_km=distance_km)
        return self.get_today_totals()

    def get_today_totals(self) -> dict:
        """Get today's distance totals broken down by mode."""
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT mode, SUM(distance_km), COUNT(*) FROM distance_log "
            "WHERE log_date = ? GROUP BY mode",
            (date.today().isoformat(),),
        ).fetchall()
        conn.close()

        by_mode = {mode: round(total, 2) for mode, total, _ in rows}
        return {
            "date": date.today().isoformat(),
            "total_km": round(sum(by_mode.values()), 2),
            "by_mode": by_mode,
            "entries": sum(count for _, _, count in rows),
        }

    # ---------- Agent behavior (for Synapse) ----------

    def register(self):
        """Register with the ingestion server."""
        self.sdk.register({
            "name": "Distance Tracker",
            "type": "distance_tracker",
            "description": "Tracks daily distance traveled by mode.",
            "version": "1.0.0",
        })

    def do_work(self):
        """The agent's work cycle: report today's totals as telemetry."""
        totals = self.get_today_totals()
        metrics = {
            "status": "HEALTHY",
            "tasks_completed": 1,
            "total_km_today": totals["total_km"],
            "entries_today": totals["entries"],
        }
        # Flatten per-mode distances into metrics (e.g. km_walking, km_driving)
        for mode, km in totals["by_mode"].items():
            metrics[f"km_{mode}"] = km

        self.sdk.send_metrics(metrics)
        self.logger.log_event("totals_reported", **totals["by_mode"], total=totals["total_km"])


# ---------- CLI ----------

def print_totals(totals: dict):
    print(f"\n📊 Distance for {totals['date']}")
    print(f"   Total: {totals['total_km']} km ({totals['entries']} entries)")
    for mode, km in totals["by_mode"].items():
        print(f"   - {mode}: {km} km")
    print()


def main():
    tracker = DistanceTracker()

    if len(sys.argv) < 2:
        print(__doc__)
        return

    command = sys.argv[1].lower()

    if command == "log":
        if len(sys.argv) != 4:
            print("Usage: python distance_tracker.py log <km> <mode>")
            print(f"Modes: {', '.join(sorted(VALID_MODES))}")
            return
        try:
            totals = tracker.log_distance(float(sys.argv[2]), sys.argv[3])
            print(f"✅ Logged {sys.argv[2]} km {sys.argv[3]}")
            print_totals(totals)
        except ValueError as e:
            print(f"❌ {e}")

    elif command == "today":
        print_totals(tracker.get_today_totals())

    elif command == "run":
        from healing_wrapper import make_healable, run_with_healing
        tracker.register()
        healable = make_healable(tracker, agent_id=tracker.agent_id)
        print("🏃 Distance Tracker agent running with self-healing (Ctrl+C to stop)")
        asyncio.run(run_with_healing(
            healable,
            work_fn=tracker.do_work,
            work_interval_seconds=60,
        ))

    else:
        print(f"Unknown command: {command}")
        print(__doc__)


if __name__ == "__main__":
    main()