   # Author: Sakshi Singh
   # Module 1: Transaction Manager (team TriCoders, CoreLock)
"""
transaction_manager.py -- Module 1: Transaction Manager.

Tracks the lifecycle of every booking transaction:
    CREATED -> ACTIVE -> (READ / WRITE / WAITING)* -> COMMITTED
                                                    -> ABORTED
"""

import itertools
import time
import threading
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Set


class TxnState(Enum):
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    WAITING = "WAITING"
    COMMITTED = "COMMITTED"
    ABORTED = "ABORTED"


class OpType(Enum):
    READ = "READ"
    WRITE = "WRITE"
    COMMIT = "COMMIT"
    ROLLBACK = "ROLLBACK"


@dataclass
class Operation:
    op_type: OpType
    seat_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class Transaction:
    txn_id: str
    user: str
    show_id: str
    seat_ids: List[str]
    priority: int = 5
    state: TxnState = TxnState.CREATED
    operations: List[Operation] = field(default_factory=list)

    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    waiting_on_seat: Optional[str] = None
    abort_reason: Optional[str] = None

    read_set: Set[str] = field(default_factory=set)
    write_set: Set[str] = field(default_factory=set)

    def log_operation(self, op_type: OpType, seat_id: Optional[str] = None):
        self.operations.append(Operation(op_type, seat_id))
        if op_type == OpType.READ and seat_id:
            self.read_set.add(seat_id)
        elif op_type == OpType.WRITE and seat_id:
            self.write_set.add(seat_id)

    @property
    def waiting_time(self) -> float:
        if self.started_at is None:
            return 0.0
        return self.started_at - self.created_at

    @property
    def turnaround_time(self) -> float:
        if self.finished_at is None:
            return 0.0
        return self.finished_at - self.created_at

    def snapshot(self) -> dict:
        return {
            "txn_id": self.txn_id,
            "user": self.user,
            "seats": ",".join(self.seat_ids),
            "priority": self.priority,
            "state": self.state.value,
            "waiting_on": self.waiting_on_seat or "-",
            "wait_s": round(self.waiting_time, 3),
            "turnaround_s": round(self.turnaround_time, 3),
            "ops": len(self.operations),
            "reason": self.abort_reason or "-",
        }


class TransactionManager:
    _id_counter = itertools.count(1)

    def __init__(self):
        self._lock = threading.Lock()
        self.transactions: dict = {}

    def create_transaction(self, user: str, show_id: str, seat_ids: List[str],
                           priority: int = 5) -> Transaction:
        txn_id = f"T{next(self._id_counter)}"
        txn = Transaction(txn_id=txn_id, user=user, show_id=show_id,
                          seat_ids=seat_ids, priority=priority)
        with self._lock:
            self.transactions[txn_id] = txn
        return txn

    def mark_active(self, txn: Transaction):
        txn.started_at = txn.started_at or time.time()
        txn.state = TxnState.ACTIVE
        txn.waiting_on_seat = None

    def mark_waiting(self, txn: Transaction, seat_id: str):
        txn.state = TxnState.WAITING
        txn.waiting_on_seat = seat_id

    def mark_committed(self, txn: Transaction):
        txn.log_operation(OpType.COMMIT)
        txn.state = TxnState.COMMITTED
        txn.finished_at = time.time()
        txn.waiting_on_seat = None

    def mark_aborted(self, txn: Transaction, reason: Optional[str] = None):
        if txn.state != TxnState.ABORTED:
            txn.log_operation(OpType.ROLLBACK)
        txn.state = TxnState.ABORTED
        txn.finished_at = time.time()
        txn.waiting_on_seat = None
        if reason:
            txn.abort_reason = reason

    def all_snapshots(self) -> List[dict]:
        with self._lock:
            return [t.snapshot() for t in self.transactions.values()]

    def get(self, txn_id: str) -> Optional[Transaction]:
        return self.transactions.get(txn_id)
