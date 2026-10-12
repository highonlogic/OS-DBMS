# Author: Divyanshi Vatsa
# Module 5: Deadlock Manager (team TriCoders, CoreLock)
"""
deadlock_manager.py -- Module 5: Deadlock Manager.

Builds a wait-for graph from LockManager's wait_for edges (waiter -> {holders})
and detects cycles via DFS. If a cycle is found, chooses the min-work
transaction as the victim, releases its locks, and marks it ABORTED.

`resolution_delay_s` = grace period (default 0.8s). A cycle must be
observed for that many seconds before the victim is rolled back. This
gives the dashboard time to visibly render the wait-for graph and the
waiting queue -- essential for demonstrating deadlock detection live
to a mentor / evaluator.
"""

import time
from typing import Dict, List, Optional, Set


class DeadlockManager:
    def __init__(self, lock_manager, transaction_manager,
                 resolution_delay_s: float = 0.8):
        self.lock_manager = lock_manager
        self.transaction_manager = transaction_manager
        self.detected_cycles_log: List[List[str]] = []
        self.resolution_delay_s = resolution_delay_s
        # signature of the currently-observed cycle, and when we first saw it
        self._pending_cycle: Optional[frozenset] = None
        self._pending_since: Optional[float] = None
        self._aborted_by_deadlock: Set[str] = set()

    def _find_cycle(self, graph: Dict[str, Set[str]]) -> Optional[List[str]]:
        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {n: WHITE for n in graph}
        parent: Dict[str, Optional[str]] = {n: None for n in graph}

        def dfs(u: str) -> Optional[List[str]]:
            color[u] = GRAY
            for v in graph.get(u, set()):
                if v not in color:
                    color[v] = WHITE
                if color[v] == GRAY:
                    cyc = [v]
                    cur = u
                    while cur is not None and cur != v:
                        cyc.append(cur)
                        cur = parent.get(cur)
                    cyc.append(v)
                    cyc.reverse()
                    return cyc
                if color[v] == WHITE:
                    parent[v] = u
                    found = dfs(v)
                    if found:
                        return found
            color[u] = BLACK
            return None

        for node in list(graph.keys()):
            if color.get(node, WHITE) == WHITE:
                r = dfs(node)
                if r:
                    return r
        return None

    def check_and_resolve(self) -> Optional[str]:
        """Called from the monitor loop. Returns victim txn_id if one was rolled back."""
        graph = self.lock_manager.current_wait_for_graph()
        cycle = self._find_cycle(graph)

        if not cycle:
            # no cycle right now -> clear the pending timer
            self._pending_cycle = None
            self._pending_since = None
            return None

        sig = frozenset(cycle)
        now = time.time()

        if self._pending_cycle != sig:
            # first time we see this specific cycle
            self._pending_cycle = sig
            self._pending_since = now
            return None

        # same cycle still present -- has enough time passed to resolve?
        if now - self._pending_since < self.resolution_delay_s:
            return None

        # OK, resolve it
        self.detected_cycles_log.append(cycle)
        self._pending_cycle = None
        self._pending_since = None

        candidates = [
            self.transaction_manager.get(tid)
            for tid in set(cycle) if self.transaction_manager.get(tid)
        ]
        candidates = [c for c in candidates if c is not None]
        if not candidates:
            return None

        # min-work victim (fewest ops wins the tiebreak by priority)
        victim = min(candidates, key=lambda t: (len(t.operations), -t.priority))
        self._aborted_by_deadlock.add(victim.txn_id)
        self.lock_manager.release_all(victim.txn_id)
        self.transaction_manager.mark_aborted(victim, reason="deadlock victim")
        return victim.txn_id

    def was_aborted_by_deadlock(self, txn_id: str) -> bool:
        return txn_id in self._aborted_by_deadlock

    def cycle_history(self) -> List[List[str]]:
        return list(self.detected_cycles_log)

    def wait_for_edges(self) -> List[dict]:
        rows = []
        for waiter, holders in self.lock_manager.current_wait_for_graph().items():
            for h in holders:
                rows.append({"waiter": waiter, "holder": h})
        return rows

    def pending_cycle_info(self) -> Optional[dict]:
        """Cycle currently observed but not yet resolved (still within grace)."""
        if self._pending_cycle is None:
            return None
        elapsed = time.time() - (self._pending_since or time.time())
        return {
            "txns": sorted(self._pending_cycle),
            "elapsed_s": round(elapsed, 2),
            "will_resolve_in_s": round(max(0.0, self.resolution_delay_s - elapsed), 2),
        }
