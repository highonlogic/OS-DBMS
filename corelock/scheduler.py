   # Author: Sakshi Singh
   # Module 2: OS Scheduler (team TriCoders, CoreLock)
"""
scheduler.py -- Module 2: OS Scheduler.

Algorithms:
    FCFS         - First Come First Served (by created_at)
    SJF          - Shortest Job First (fewest seats first)
    ROUND_ROBIN  - arrival order (fairness comes from time-slicing)
    PRIORITY     - lower priority number = served first
"""

from enum import Enum
from collections import deque
from typing import List
from .transaction_manager import Transaction


class Algorithm(Enum):
    FCFS = "FCFS"
    SJF = "SJF"
    ROUND_ROBIN = "ROUND_ROBIN"
    PRIORITY = "PRIORITY"


class Scheduler:
    def __init__(self, algorithm: Algorithm = Algorithm.FCFS, time_quantum: int = 1):
        self.algorithm = algorithm
        self.time_quantum = time_quantum
        self.ready_queue: deque[Transaction] = deque()
        self.execution_order: List[str] = []

    def add(self, txn: Transaction):
        self.ready_queue.append(txn)

    def _sorted_snapshot(self) -> List[Transaction]:
        items = list(self.ready_queue)
        if self.algorithm == Algorithm.FCFS:
            return sorted(items, key=lambda t: t.created_at)
        if self.algorithm == Algorithm.SJF:
            return sorted(items, key=lambda t: (len(t.seat_ids), t.created_at))
        if self.algorithm == Algorithm.PRIORITY:
            return sorted(items, key=lambda t: (t.priority, t.created_at))
        return items    # ROUND_ROBIN: arrival order

    def next_batch(self, n: int = 1) -> List[Transaction]:
        ordered = self._sorted_snapshot()
        batch = ordered[:n]
        for txn in batch:
            self.ready_queue.remove(txn)
            self.execution_order.append(txn.txn_id)
        return batch

    def has_pending(self) -> bool:
        return len(self.ready_queue) > 0

    def ready_snapshot(self) -> List[dict]:
        """Ready queue in current algorithm order -- shows the algo actually working."""
        return [
            {
                "order": i + 1,
                "txn_id": t.txn_id, "user": t.user,
                "arrived_at": t.created_at,         # absolute Unix ts (relative in UI)
                "seats": ",".join(t.seat_ids),
                "burst_seats": len(t.seat_ids),     # SJF proxy: 1 seat = 1 burst unit
                "priority": t.priority,
            }
            for i, t in enumerate(self._sorted_snapshot())
        ]

    def gantt_snapshot(self) -> List[str]:
        return list(self.execution_order)
