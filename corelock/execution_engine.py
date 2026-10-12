"""
execution_engine.py -- Module 3: Concurrent Execution Engine.

Runs each transaction on its own thread. A semaphore caps concurrent
runners. A background monitor thread runs the DeadlockManager loop.

Booking lifecycle:
  1. Growing phase (S-Locks) -- seats visibly LOCKED (yellow "in cart").
  2. Browse hold -- seats stay LOCKED for `hold_seconds`. This is the
     window in which deadlocks can form and be observed live.
  3. Voluntary rollback path: if should_commit=False, abort here.
  4. Upgrade phase (X-Locks) -- confirm payment.
  5. Commit -- flip to BOOKED, release all locks (Strict 2PL).

If the DeadlockManager rolls this txn back from outside, the thread
notices via `_was_externally_aborted()` after every acquire and every
sleep tick, and exits cleanly without double-committing.
"""

import threading
import time
from typing import Dict, List, Optional

from .transaction_manager import TransactionManager, Transaction, TxnState, OpType
from .lock_manager import LockManager, LockType
from .scheduler import Scheduler
from .deadlock_manager import DeadlockManager
from .recovery_manager import RecoveryManager
from .seat_inventory import Cinema, SeatState


class ExecutionEngine:
    def __init__(self, cinema: Cinema, txn_manager: TransactionManager,
                 lock_manager: LockManager, scheduler: Scheduler,
                 recovery_manager: RecoveryManager,
                 max_concurrent: int = 6,
                 monitor_interval: float = 0.4):
        self.cinema = cinema
        self.txn_manager = txn_manager
        self.lock_manager = lock_manager
        self.scheduler = scheduler
        self.recovery_manager = recovery_manager
        self.deadlock_manager = DeadlockManager(lock_manager, txn_manager)

        self._semaphore = threading.Semaphore(max_concurrent)
        self._monitor_stop = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._state_lock = threading.Lock()
        self.monitor_interval = monitor_interval

        self.event_log: List[dict] = []
        self._event_lock = threading.Lock()
        self.deadlock_victims: List[str] = []

    # ---- monitor control -----------------------------------------------------
    def start_monitor(self):
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(
            target=self._deadlock_monitor_loop, daemon=True
        )
        self._monitor_thread.start()

    def stop_monitor(self):
        self._monitor_stop.set()

    def _deadlock_monitor_loop(self):
        while not self._monitor_stop.is_set():
            victim = self.deadlock_manager.check_and_resolve()
            if victim:
                self.deadlock_victims.append(victim)
                self._release_seats_for(victim)
                self._log("DEADLOCK", f"cycle resolved, victim = {victim}", victim)
            time.sleep(self.monitor_interval)

    def _release_seats_for(self, txn_id: str):
        for screen in self.cinema.screens.values():
            for show in screen.shows.values():
                for seat in show.seats.values():
                    if seat.locked_by == txn_id:
                        with self._state_lock:
                            seat.state = SeatState.AVAILABLE
                            seat.locked_by = None

    # ---- event log -----------------------------------------------------------
    def _log(self, event_type: str, message: str, txn_id: str = "-"):
        with self._event_lock:
            self.event_log.append({
                "ts": round(time.time(), 3),
                "event": event_type,
                "txn": txn_id,
                "message": message,
            })

    def get_event_log(self) -> List[dict]:
        with self._event_lock:
            return list(self.event_log)

    # ---- abort detection -----------------------------------------------------
    def _was_externally_aborted(self, txn: Transaction) -> bool:
        return txn.state == TxnState.ABORTED

    def _should_abort_cb(self, txn: Transaction):
        """Returns a closure the LockManager calls periodically while blocked."""
        return lambda: self._was_externally_aborted(txn)

    def _finalize_external_abort(self, txn: Transaction):
        show = self.cinema.get_show(txn.show_id)
        if show is not None:
            with self._state_lock:
                for seat in show.seats.values():
                    if seat.locked_by == txn.txn_id:
                        seat.state = SeatState.AVAILABLE
                        seat.locked_by = None
        self.lock_manager.release_all(txn.txn_id)
        self.recovery_manager.log_rollback(txn.txn_id)
        self._log("ROLLBACK",
                  f"external abort: {txn.abort_reason or 'unknown'}",
                  txn.txn_id)

    # ---- main lifecycle ------------------------------------------------------
    def run_transaction(self, txn: Transaction, hold_seconds: float = 1.5,
                        should_commit: bool = True):
        with self._semaphore:
            self.txn_manager.mark_active(txn)
            self._log("START",
                      f"{txn.user} requests seats {txn.seat_ids} "
                      f"({'COMMIT' if should_commit else 'ROLLBACK-mode'})",
                      txn.txn_id)

            show = self.cinema.get_show(txn.show_id)
            if show is None:
                self._log("ERROR", f"unknown show {txn.show_id}", txn.txn_id)
                self._abort(txn, reason=f"unknown show {txn.show_id}")
                return

            # pre-check: already booked?
            for seat_id in txn.seat_ids:
                seat = show.get_seat(seat_id)
                if seat is None:
                    self._log("CONFLICT", f"no such seat {seat_id}", txn.txn_id)
                    self._abort(txn, reason=f"no such seat {seat_id}")
                    return
                if seat.state == SeatState.BOOKED:
                    self._log("CONFLICT",
                              f"{seat_id} already booked by {seat.booked_by}",
                              txn.txn_id)
                    self._abort(txn, reason=f"{seat_id} already booked")
                    return

            # ---- Phase 1: S-Locks ---------------------------------------
            for seat_id in txn.seat_ids:
                if self._was_externally_aborted(txn):
                    self._finalize_external_abort(txn); return
                txn.log_operation(OpType.READ, seat_id)
                self.txn_manager.mark_waiting(txn, seat_id)
                self._log("S-REQUEST", f"S-lock on {seat_id}", txn.txn_id)

                got = self.lock_manager.acquire(
                    txn.txn_id, seat_id, LockType.S,
                    should_abort=self._should_abort_cb(txn),
                )
                if self._was_externally_aborted(txn):
                    self._finalize_external_abort(txn); return
                if not got:
                    self._log("WAIT-TIMEOUT",
                              f"gave up on S-lock for {seat_id}", txn.txn_id)
                    self._abort(txn, reason=f"timeout on S-lock for {seat_id}")
                    return

                self.txn_manager.mark_active(txn)
                with self._state_lock:
                    seat = show.get_seat(seat_id)
                    if seat.state == SeatState.AVAILABLE:
                        seat.state = SeatState.LOCKED
                        seat.locked_by = txn.txn_id
                self._log("S-LOCK", f"acquired S on {seat_id}", txn.txn_id)

            # ---- browsing hold (chunked so we react to external abort) --
            self._log("BROWSING",
                      f"holding {txn.seat_ids} for {hold_seconds:.1f}s "
                      f"({'will confirm' if should_commit else 'will rollback voluntarily'})",
                      txn.txn_id)
            slept = 0.0
            step = 0.1
            while slept < hold_seconds:
                if self._was_externally_aborted(txn):
                    self._finalize_external_abort(txn); return
                time.sleep(min(step, hold_seconds - slept))
                slept += step

            # ---- voluntary rollback path --------------------------------
            if not should_commit:
                self._log("ROLLBACK",
                          "voluntary rollback (payment abandoned)", txn.txn_id)
                self._abort(txn, reason="voluntary rollback")
                return

            # ---- Phase 2: X-Locks ---------------------------------------
            for seat_id in txn.seat_ids:
                if self._was_externally_aborted(txn):
                    self._finalize_external_abort(txn); return
                self.txn_manager.mark_waiting(txn, seat_id)
                self._log("X-REQUEST",
                          f"upgrade to X-lock on {seat_id}", txn.txn_id)

                got = self.lock_manager.acquire(
                    txn.txn_id, seat_id, LockType.X,
                    should_abort=self._should_abort_cb(txn),
                )
                if self._was_externally_aborted(txn):
                    self._finalize_external_abort(txn); return
                if not got:
                    self._log("WAIT-TIMEOUT",
                              f"gave up on X-lock for {seat_id}", txn.txn_id)
                    self._abort(txn, reason=f"timeout on X-lock for {seat_id}")
                    return

                self.txn_manager.mark_active(txn)
                seat = show.get_seat(seat_id)
                before = seat.state.value
                self.recovery_manager.log_write(txn.txn_id, seat_id,
                                                before, SeatState.BOOKED.value)
                txn.log_operation(OpType.WRITE, seat_id)
                self._log("X-LOCK", f"acquired X on {seat_id}", txn.txn_id)

            if self._was_externally_aborted(txn):
                self._finalize_external_abort(txn); return

            # ---- Phase 3: COMMIT ----------------------------------------
            with self._state_lock:
                for seat_id in txn.seat_ids:
                    seat = show.get_seat(seat_id)
                    seat.state = SeatState.BOOKED
                    seat.booked_by = txn.user
                    seat.locked_by = None

            self.recovery_manager.log_commit(txn.txn_id)
            self.lock_manager.release_all(txn.txn_id)
            self.txn_manager.mark_committed(txn)
            self._log("COMMIT",
                      f"booked seats {txn.seat_ids} for {txn.user}",
                      txn.txn_id)

    def _abort(self, txn: Transaction, reason: Optional[str] = None):
        show = self.cinema.get_show(txn.show_id)
        if show is not None:
            with self._state_lock:
                for seat_id in list(self.lock_manager.locks_held_by(txn.txn_id)):
                    seat = show.get_seat(seat_id)
                    if seat and seat.locked_by == txn.txn_id:
                        seat.state = SeatState.AVAILABLE
                        seat.locked_by = None
        self.recovery_manager.log_rollback(txn.txn_id)
        self.lock_manager.release_all(txn.txn_id)
        self.txn_manager.mark_aborted(txn, reason=reason)

    # ---- launcher ------------------------------------------------------------
    def run_all_threaded(self, transactions: List[Transaction],
                         hold_map: Optional[Dict[str, float]] = None,
                         commit_map: Optional[Dict[str, bool]] = None,
                         default_hold: float = 1.5,
                         wait: bool = False) -> List[threading.Thread]:
        """
        Launch one thread per transaction. wait=False (default) returns
        immediately so the UI can poll live state. If wait=True, join
        all threads before returning.
        """
        hold_map = hold_map or {}
        commit_map = commit_map or {}
        threads = []
        for txn in transactions:
            t = threading.Thread(
                target=self.run_transaction,
                args=(txn,
                      hold_map.get(txn.txn_id, default_hold),
                      commit_map.get(txn.txn_id, True)),
                daemon=True,
            )
            threads.append(t)
            t.start()
        if wait:
            for t in threads:
                t.join()
        return threads
