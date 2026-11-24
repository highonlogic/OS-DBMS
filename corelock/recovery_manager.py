# Author: Anmol Tyagi
# Module 6: Recovery Manager (team TriCoders, CoreLock)
"""
recovery_manager.py -- Module 6: Recovery Manager.

Write-Ahead Log (WAL) + UNDO/REDO recovery + CHECKPOINTS.

Log entries (one JSON object per line, in logs/transaction_log.jsonl):
    WRITE      : {"txn_id","seat_id","op":"WRITE","before","after","ts"}
    COMMIT     : {"txn_id","op":"COMMIT","ts"}
    ROLLBACK   : {"txn_id","op":"ROLLBACK","ts"}
    CHECKPOINT : {"op":"CHECKPOINT","ts","active_txns":[...],"snapshot":{...}}

Recovery with checkpoints:
    1. Scan backwards to find the latest CHECKPOINT record.
    2. Restore all seat states from the checkpoint's snapshot
       (fast path -- no need to replay the entire log).
    3. From the checkpoint onwards: standard UNDO/REDO
       -- any WRITE with no matching COMMIT is reverted (UNDO).
       -- any WRITE whose txn has a COMMIT but whose in-memory state
          doesn't match after-image is re-applied (REDO).

Phase-III changes:
    * take_checkpoint() -- actually implemented (was a no-op before)
    * recover() -- uses the latest checkpoint as a starting point
    * recovery_stats() -- for the dashboard metrics panel
"""

import json
import os
import time
import threading
from typing import Dict, List, Optional
from .seat_inventory import Cinema, SeatState


class RecoveryManager:
    def __init__(self, log_path: Optional[str] = None, truncate: bool = True):
        self.log_path = log_path or os.path.join(
            os.path.dirname(__file__), "logs", "transaction_log.jsonl"
        )
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        if truncate:
            open(self.log_path, "w").close()
        self._file_lock = threading.Lock()
        self.checkpoints: List[dict] = []

    # ---- WAL write helpers ----------------------------------------------
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

    # ---- Phase-III: CHECKPOINTS ----------------------------------------
    def take_checkpoint(self, cinema: Cinema,
                        active_txns: Optional[List[str]] = None) -> dict:
        """
        Capture the current committed seat state as a checkpoint.
        Written to the WAL as a CHECKPOINT record AND kept in-memory
        in self.checkpoints for fast lookup. Recovery can then start
        from the latest checkpoint instead of replaying the full log.
        """
        snapshot: Dict[str, Dict[str, str]] = {}  # seat_id -> {state, booked_by}
        for screen in cinema.screens.values():
            for show in screen.shows.values():
                for seat in show.seats.values():
                    snapshot[seat.seat_id] = {
                        "state": seat.state.value,
                        "booked_by": seat.booked_by or "",
                    }
        record = {
            "op": "CHECKPOINT",
            "ts": time.time(),
            "active_txns": list(active_txns or []),
            "snapshot": snapshot,
        }
        self._append(record)
        self.checkpoints.append(record)
        return record

    def _latest_checkpoint(self) -> Optional[dict]:
        """Scan WAL backwards for the most recent CHECKPOINT record."""
        log = self.read_log()
        for record in reversed(log):
            if record.get("op") == "CHECKPOINT":
                return record
        return None

    def _restore_from_checkpoint(self, cinema: Cinema, cp: dict) -> int:
        """Restore seat states from a checkpoint. Returns # seats restored."""
        restored = 0
        for seat_id, snap in cp.get("snapshot", {}).items():
            show = self._find_show_with_seat(cinema, seat_id)
            if show is None:
                continue
            seat = show.get_seat(seat_id)
            if seat is None:
                continue
            desired = SeatState[snap["state"]]
            if seat.state != desired:
                seat.state = desired
                seat.booked_by = snap["booked_by"] or None
                seat.locked_by = None
                restored += 1
        return restored

    def checkpoint_snapshot(self) -> List[dict]:
        """Checkpoints formatted for a dashboard table."""
        return [
            {
                "ts": round(cp.get("ts", 0), 3),
                "active_txns": ", ".join(cp.get("active_txns", [])) or "-",
                "seats_captured": len(cp.get("snapshot", {})),
            }
            for cp in self.checkpoints
        ]

    # ---- Crash simulation -----------------------------------------------
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

    # ---- Recovery with checkpoint fast-path -----------------------------
    def recover(self, cinema: Cinema) -> Dict[str, list]:
        """
        Checkpoint-aware recovery.
          1. If a CHECKPOINT exists, restore its seat snapshot first,
             then only replay WAL records AFTER the checkpoint.
          2. Otherwise, replay the entire WAL (original behaviour).
        Returns {"undone": [...], "redone": [...], "restored_from_checkpoint": N}
        """
        log = self.read_log()
        cp = self._latest_checkpoint()
        restored_from_cp = 0

        if cp is not None:
            restored_from_cp = self._restore_from_checkpoint(cinema, cp)
            cp_ts = cp["ts"]
            replay = [r for r in log
                      if r.get("op") in ("WRITE", "COMMIT", "ROLLBACK")
                      and r.get("ts", 0) > cp_ts]
        else:
            replay = [r for r in log
                      if r.get("op") in ("WRITE", "COMMIT", "ROLLBACK")]

        committed = {r["txn_id"] for r in replay if r.get("op") == "COMMIT"}
        undone, redone = [], []

        for record in replay:
            if record.get("op") != "WRITE":
                continue
            txn_id, seat_id = record["txn_id"], record["seat_id"]
            show = self._find_show_with_seat(cinema, seat_id)
            if show is None:
                continue
            seat = show.get_seat(seat_id)

            if txn_id in committed:
                # REDO: committed write but state doesn't reflect it
                if seat.state.value != record["after"]:
                    seat.state = SeatState[record["after"]]
                    redone.append({"txn_id": txn_id, "seat_id": seat_id,
                                   "restored_to": record["after"]})
            else:
                # UNDO: uncommitted write
                if seat.state.value != record["before"]:
                    seat.state = SeatState[record["before"]]
                    seat.booked_by = None
                    seat.locked_by = None
                    undone.append({"txn_id": txn_id, "seat_id": seat_id,
                                   "restored_to": record["before"]})
        return {
            "undone": undone,
            "redone": redone,
            "restored_from_checkpoint": restored_from_cp,
            "replayed_records": len(replay),
            "used_checkpoint": cp is not None,
        }

    def recovery_stats(self) -> dict:
        """Summary for the dashboard metrics panel."""
        log = self.read_log()
        return {
            "total_wal_records": len(log),
            "write_records":    sum(1 for r in log if r.get("op") == "WRITE"),
            "commit_records":   sum(1 for r in log if r.get("op") == "COMMIT"),
            "rollback_records": sum(1 for r in log if r.get("op") == "ROLLBACK"),
            "checkpoints":      len(self.checkpoints),
        }

    def _find_show_with_seat(self, cinema: Cinema, seat_id: str):
        for screen in cinema.screens.values():
            for show in screen.shows.values():
                if seat_id in show.seats:
                    return show
        return None
