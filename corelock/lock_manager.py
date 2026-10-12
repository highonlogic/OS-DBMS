"""
lock_manager.py -- Module 4: Lock & Concurrency Manager.

Strict 2PL with Shared (S) and Exclusive (X) locks on seats.

Compatibility matrix:
            S-Lock      X-Lock
  S-Lock    Compatible  Conflict
  X-Lock    Conflict    Conflict
"""

import threading
import time
from enum import Enum
from collections import defaultdict
from typing import Dict, List, Set, Tuple, Optional


class LockType(Enum):
    S = "S"
    X = "X"


class LockManager:
    def __init__(self, wait_timeout: float = 3.0):
        self._lock = threading.RLock()
        # seat_id -> {"type": LockType, "holders": {txn_id, ...}}
        self._lock_table: Dict[str, dict] = {}
        # txn_id -> set of seat_ids it holds a lock on
        self._held_by_txn: Dict[str, Set[str]] = defaultdict(set)
        # wait-for graph: waiter -> {holders it is blocked on}
        self.wait_for: Dict[str, Set[str]] = defaultdict(set)
        # per-seat FIFO queue of (waiter_txn, requested LockType)
        self.waiting_queue: Dict[str, List[Tuple[str, LockType]]] = defaultdict(list)
        self.wait_timeout = wait_timeout

    def _compatible(self, seat_id: str, requested: LockType, txn_id: str) -> bool:
        entry = self._lock_table.get(seat_id)
        if entry is None:
            return True
        holders = entry["holders"]
        if holders == {txn_id}:
            return True                          # sole holder -> upgrade OK
        if requested == LockType.S and entry["type"] == LockType.S:
            return True                          # S + S compatible
        return False

    def acquire(self, txn_id: str, seat_id: str, lock_type: LockType,
                should_abort=None) -> bool:
        """
        Blocking acquire with timeout. Returns True if granted, False if
        timed out. `should_abort` is a callback -> bool; if it returns True
        mid-wait, we exit early (used for deadlock-victim rollback).
        """
        start = time.time()
        registered = False
        while True:
            if should_abort and should_abort():
                with self._lock:
                    self.wait_for.pop(txn_id, None)
                    self.waiting_queue[seat_id] = [
                        (t, k) for (t, k) in self.waiting_queue[seat_id] if t != txn_id
                    ]
                return False

            with self._lock:
                if self._compatible(seat_id, lock_type, txn_id):
                    entry = self._lock_table.setdefault(
                        seat_id, {"type": lock_type, "holders": set()}
                    )
                    if lock_type == LockType.X:
                        entry["type"] = LockType.X
                    entry["holders"].add(txn_id)
                    self._held_by_txn[txn_id].add(seat_id)
                    self.wait_for.pop(txn_id, None)
                    self.waiting_queue[seat_id] = [
                        (t, k) for (t, k) in self.waiting_queue[seat_id] if t != txn_id
                    ]
                    return True

                other_holders = {
                    h for h in self._lock_table[seat_id]["holders"] if h != txn_id
                }
                if other_holders:
                    self.wait_for[txn_id] = other_holders

                if not registered:
                    self.waiting_queue[seat_id].append((txn_id, lock_type))
                    registered = True

            if time.time() - start > self.wait_timeout:
                with self._lock:
                    self.wait_for.pop(txn_id, None)
                    self.waiting_queue[seat_id] = [
                        (t, k) for (t, k) in self.waiting_queue[seat_id] if t != txn_id
                    ]
                return False
            time.sleep(0.05)

    def release_all(self, txn_id: str):
        with self._lock:
            seat_ids = self._held_by_txn.pop(txn_id, set())
            for seat_id in seat_ids:
                entry = self._lock_table.get(seat_id)
                if entry and txn_id in entry["holders"]:
                    entry["holders"].discard(txn_id)
                    if not entry["holders"]:
                        del self._lock_table[seat_id]
                    else:
                        entry["type"] = LockType.S
            self.wait_for.pop(txn_id, None)
            for seat_id in list(self.waiting_queue.keys()):
                self.waiting_queue[seat_id] = [
                    (t, k) for (t, k) in self.waiting_queue[seat_id] if t != txn_id
                ]
                if not self.waiting_queue[seat_id]:
                    del self.waiting_queue[seat_id]

    def locks_held_by(self, txn_id: str) -> Set[str]:
        with self._lock:
            return set(self._held_by_txn.get(txn_id, set()))

    def current_wait_for_graph(self) -> Dict[str, Set[str]]:
        with self._lock:
            return {t: set(hs) for t, hs in self.wait_for.items() if hs}

    def lock_table_snapshot(self) -> List[dict]:
        with self._lock:
            return [
                {
                    "seat_id": seat_id,
                    "lock_type": v["type"].value,
                    "holders": ", ".join(sorted(v["holders"])),
                    "num_holders": len(v["holders"]),
                }
                for seat_id, v in self._lock_table.items()
            ]

    def waiting_queue_snapshot(self) -> List[dict]:
        with self._lock:
            rows = []
            for seat_id, waiters in self.waiting_queue.items():
                for txn_id, kind in waiters:
                    holders = self._lock_table.get(seat_id, {}).get("holders", set()) - {txn_id}
                    rows.append({
                        "seat_id": seat_id,
                        "waiting_txn": txn_id,
                        "wants": kind.value,
                        "blocked_by": ", ".join(sorted(holders)) or "-",
                    })
            return rows
