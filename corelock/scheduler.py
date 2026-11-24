# Author: Sakshi Singh
# Module 2: OS Scheduler (team TriCoders, CoreLock)
"""
scheduler.py -- Module 2: OS Scheduler.

Algorithms:
    FCFS         - First Come First Served (by created_at)
    SJF          - Shortest Job First (fewest seats first)
    ROUND_ROBIN  - time-sliced execution with configurable quantum
                   (execution engine preempts after each quantum)
    PRIORITY     - lower priority number = served first

Phase-III change: ROUND_ROBIN is no longer "FCFS in disguise". The
scheduler now carries a `rr_quantum_s` setting that the ExecutionEngine
reads; in RR mode the browsing hold is broken into quantum-sized slices
with explicit context switches logged between them. This is visible on
the dashboard as PREEMPT events, a context-switches counter per txn,
and interleaved bars in the Gantt chart.
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
    def __init__(self, algorithm: Algorithm = Algorithm.FCFS,
                 rr_quantum_s: float = 0.3,
                 rr_switch_cost_s: float = 0.05):
        self.algorithm = algorithm
        self.rr_quantum_s = rr_quantum_s        # time slice per txn in RR
        self.rr_switch_cost_s = rr_switch_cost_s  # context-switch overhead
        self.ready_queue: deque[Transaction] = deque()
        self.execution_order: List[str] = []
        # Phase-III: timeline for the Gantt chart.
        # Each entry: {"txn_id","user","start","end","phase"}
        # phase in {"S_LOCK","HOLD","X_LOCK","COMMIT","PREEMPTED","ABORT"}
        self.timeline: List[dict] = []

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
        return items    # ROUND_ROBIN: arrival order; preemption is in engine

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
                "arrived_at": t.created_at,
                "seats": ",".join(t.seat_ids),
                "burst_seats": len(t.seat_ids),
                "priority": t.priority,
            }
            for i, t in enumerate(self._sorted_snapshot())
        ]

    def gantt_snapshot(self) -> List[str]:
        return list(self.execution_order)

    # ---- Phase-III: timeline for real Gantt chart -----------------------
    def record_slice(self, txn_id: str, user: str,
                     start: float, end: float, phase: str):
        """ExecutionEngine calls this to log a work slice for the Gantt chart."""
        if end > start:
            self.timeline.append({
                "txn_id": txn_id, "user": user,
                "start": start, "end": end,
                "duration": end - start, "phase": phase,
            })

    def timeline_snapshot(self) -> List[dict]:
        return list(self.timeline)
