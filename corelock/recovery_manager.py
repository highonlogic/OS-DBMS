# Author: Anmol Tyagi
# Module 6: Recovery Manager (team TriCoders, CoreLock)
"""
recovery_manager.py -- Module 6: Recovery Manager.

Write-Ahead Log (WAL) + UNDO/REDO recovery.
Log entries (one JSON object per line, in logs/transaction_log.jsonl):
    WRITE    : {"txn_id", "seat_id", "op": "WRITE", "before", "after", "ts"}
    COMMIT   : {"txn_id", "op": "COMMIT", "ts"}
    ROLLBACK : {"txn_id", "op": "ROLLBACK", "ts"}

Recovery:
    UNDO -> any WRITE with no matching COMMIT is reverted.
    REDO -> any WRITE whose txn has a COMMIT but whose in-memory state
            doesn't match after-image is re-applied.
"""

import json
import os
import time
import threading
from typing import Dict, List
from .seat_inventory import Cinema, SeatState


class RecoveryManager:
    def __init__(self, log_path: str = None, truncate: bool = True):
        self.log_path = log_path or os.path.join(
            os.path.dirname(__file__), "logs", "transaction_log.jsonl"
        )
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        if truncate:
            open(self.log_path, "w").close()
        self._file_lock = threading.Lock()
        self.checkpoints: List[dict] = []

    def log_write(self, txn_id: str, seat_id: str, before: str, after: str):
        self._append({"txn_id": txn_id, "seat_id": seat_id, "op": "WRITE",
                      "before": before, "after": after, "ts": time.time()})

    def log_commit(self, txn_id: str):
        self._append({"txn_id": txn_id, "op": "COMMIT", "ts": time.time()})

    def log_rollback(self, txn_id: str):
        self._append({"txn_id": txn_id, "op": "ROLLBACK", "ts": time.time()})

    def _append(self, record: dict):
        with self._file_lock:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(record) + "\n")

    def read_log(self) -> List[dict]:
        if not os.path.exists(self.log_path):
            return []
        with self._file_lock:
            with open(self.log_path) as f:
                return [json.loads(line) for line in f if line.strip()]

    def log_snapshot(self) -> List[dict]:
        """WAL rows formatted for a dashboard table."""
        rows = []
        for r in self.read_log():
            rows.append({
                "ts": round(r.get("ts", 0), 3),
                "txn_id": r.get("txn_id", "-"),
                "op": r.get("op", "-"),
                "seat_id": r.get("seat_id", "-"),
                "before": r.get("before", "-"),
                "after": r.get("after", "-"),
            })
        return rows

    def simulate_crash(self, cinema: Cinema, seat_id: str, show_id: str):
        """Payment 'succeeded' (WRITE logged) but process died before COMMIT.
        Force the in-memory seat into a dirty BOOKED state so `recover()`
        has something real to UNDO."""
        show = cinema.get_show(show_id)
        if show is None:
            return
        seat = show.get_seat(seat_id)
        if seat is None:
            return
        before = seat.state.value
        seat.state = SeatState.BOOKED
        seat.booked_by = "CRASH_DIRTY_WRITE"
        self._append({
            "txn_id": "T_CRASH", "seat_id": seat_id, "op": "WRITE",
            "before": before, "after": SeatState.BOOKED.value, "ts": time.time(),
        })

    def recover(self, cinema: Cinema) -> Dict[str, list]:
        log = self.read_log()
        committed = {r["txn_id"] for r in log if r.get("op") == "COMMIT"}
        undone, redone = [], []

        for record in log:
            if record.get("op") != "WRITE":
                continue
            txn_id, seat_id = record["txn_id"], record["seat_id"]
            show = self._find_show_with_seat(cinema, seat_id)
            if show is None:
                continue
            seat = show.get_seat(seat_id)

            if txn_id in committed:
                if seat.state.value != record["after"]:
                    seat.state = SeatState[record["after"]]
                    redone.append({"txn_id": txn_id, "seat_id": seat_id,
                                   "restored_to": record["after"]})
            else:
                if seat.state.value != record["before"]:
                    seat.state = SeatState[record["before"]]
                    seat.booked_by = None
                    seat.locked_by = None
                    undone.append({"txn_id": txn_id, "seat_id": seat_id,
                                   "restored_to": record["before"]})
        return {"undone": undone, "redone": redone}

    def _find_show_with_seat(self, cinema: Cinema, seat_id: str):
        for screen in cinema.screens.values():
            for show in screen.shows.values():
                if seat_id in show.seats:
                    return show
        return None
