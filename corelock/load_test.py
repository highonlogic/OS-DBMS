# Author: Anmol Tyagi
# Phase-III: Heavy load test (team TriCoders, CoreLock)
"""
load_test.py -- Stress test the engine with many concurrent booking threads.

Where benchmark.py compares algorithms on a fixed workload, this script
pushes a fixed algorithm hard to see how the system holds up as the
number of concurrent threads grows. For each level N it reports:

    * commits / aborts / deadlocks
    * throughput (commits per wall-second)
    * avg lock-wait seconds per txn
    * engine CPU utilization
    * whether the system remained consistent
      (every booked seat booked by exactly one user, no stuck LOCKED seats)

Run:
    python -m corelock.load_test
    python -m corelock.load_test --max 100 --step 20 --algo PRIORITY
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field
from typing import List

from .benchmark import (
    generate_workload, _fresh_engine, BenchmarkResult,
)
from .scheduler import Algorithm
from .seat_inventory import SeatState


# ---------------------------------------------------------------------------
# Consistency check
# ---------------------------------------------------------------------------

def _consistency_report(cinema) -> dict:
    """
    After a load run, confirm the seat map is in a legal state:
      * No seat left in LOCKED (every lock should have been released).
      * Every BOOKED seat has exactly one booked_by.
    """
    stuck_locked, bad_booked = [], []
    booked_count = 0
    for screen in cinema.screens.values():
        for show in screen.shows.values():
            for seat in show.seats.values():
                if seat.state == SeatState.LOCKED:
                    stuck_locked.append(seat.seat_id)
                if seat.state == SeatState.BOOKED:
                    booked_count += 1
                    if not seat.booked_by or seat.booked_by == "CRASH_DIRTY_WRITE":
                        bad_booked.append(seat.seat_id)
    return {
        "booked_seats": booked_count,
        "stuck_locked_seats": stuck_locked,
        "booked_without_owner": bad_booked,
        "consistent": not stuck_locked and not bad_booked,
    }


# ---------------------------------------------------------------------------
# One load level
# ---------------------------------------------------------------------------

@dataclass
class LoadLevelResult:
    num_threads: int
    algorithm: str

    wall_s: float = 0.0
    committed: int = 0
    aborted: int = 0
    deadlocks: int = 0
    throughput_per_s: float = 0.0
    avg_lock_wait_s: float = 0.0
    cpu_utilization: float = 0.0
    context_switches: int = 0

    consistent: bool = True
    stuck_locked: List[str] = field(default_factory=list)
    booked_without_owner: List[str] = field(default_factory=list)

    def row(self) -> dict:
        return {
            "num_threads":       self.num_threads,
            "algorithm":         self.algorithm,
            "wall_s":            round(self.wall_s, 3),
            "committed":         self.committed,
            "aborted":           self.aborted,
            "deadlocks":         self.deadlocks,
            "throughput_per_s":  round(self.throughput_per_s, 3),
            "avg_lock_wait_s":   round(self.avg_lock_wait_s, 3),
            "cpu_utilization":   round(self.cpu_utilization, 3),
            "context_switches":  self.context_switches,
            "consistent":        self.consistent,
            "stuck_locked":      ",".join(self.stuck_locked) or "-",
        }


def run_load_level(num_threads: int,
                   algorithm: Algorithm = Algorithm.PRIORITY,
                   hold_seconds: float = 0.3,
                   max_concurrent: int = 8,
                   contention: str = "medium",
                   seed: int = 42,
                   show_id: str = "S101") -> LoadLevelResult:
    cinema, tm, sch, eng, rec = _fresh_engine(algorithm, max_concurrent)
    workload = generate_workload(num_threads, seed=seed, contention=contention)

    for req in workload:
        txn = tm.create_transaction(req.user, show_id, req.seats, req.priority)
        sch.add(txn)

    batch = sch.next_batch(len(workload))
    t0 = time.time()
    eng.run_all_threaded(batch, default_hold=hold_seconds, wait=True)
    wall = time.time() - t0

    time.sleep(0.3)
    eng.stop_monitor()

    snaps = tm.all_snapshots()
    committed = sum(1 for s in snaps if s["state"] == "COMMITTED")
    aborted   = sum(1 for s in snaps if s["state"] == "ABORTED")
    total     = max(1, len(snaps))
    avg_lw    = sum(s["lock_wait_s"] for s in snaps) / total
    ctx_sw    = sum(s["ctx_switches"] for s in snaps)
    cpu       = eng.cpu_utilization()
    consistency = _consistency_report(cinema)

    return LoadLevelResult(
        num_threads=num_threads,
        algorithm=algorithm.value,
        wall_s=wall,
        committed=committed,
        aborted=aborted,
        deadlocks=len(eng.deadlock_manager.cycle_history()),
        throughput_per_s=committed / max(1e-6, wall),
        avg_lock_wait_s=avg_lw,
        cpu_utilization=cpu["utilization"],
        context_switches=ctx_sw,
        consistent=consistency["consistent"],
        stuck_locked=consistency["stuck_locked_seats"],
        booked_without_owner=consistency["booked_without_owner"],
    )


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------

def run_load_sweep(start: int = 10, stop: int = 60, step: int = 10,
                   algorithm: Algorithm = Algorithm.PRIORITY,
                   hold_seconds: float = 0.3,
                   max_concurrent: int = 8,
                   contention: str = "medium",
                   seed: int = 42) -> List[LoadLevelResult]:
    results = []
    for n in range(start, stop + 1, step):
        r = run_load_level(n, algorithm=algorithm,
                           hold_seconds=hold_seconds,
                           max_concurrent=max_concurrent,
                           contention=contention, seed=seed)
        results.append(r)
    return results


def format_table(results: List[LoadLevelResult]) -> str:
    rows = [r.row() for r in results]
    headers = ["num_threads", "wall_s", "committed", "aborted", "deadlocks",
               "throughput_per_s", "avg_lock_wait_s", "cpu_utilization",
               "context_switches", "consistent"]
    widths = {h: max(len(h),
                     *(len(str(r[h])) for r in rows))
              for h in headers}
    out = []
    out.append(" | ".join(h.ljust(widths[h]) for h in headers))
    out.append("-+-".join("-" * widths[h] for h in headers))
    for r in rows:
        out.append(" | ".join(str(r[h]).ljust(widths[h]) for h in headers))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="CoreLock heavy-load (multi-thread) test.")
    ap.add_argument("--start", type=int, default=10)
    ap.add_argument("--max",   type=int, default=60)
    ap.add_argument("--step",  type=int, default=10)
    ap.add_argument("--algo",  default="PRIORITY",
                    choices=[a.value for a in Algorithm])
    ap.add_argument("--hold",  type=float, default=0.3)
    ap.add_argument("--concurrent", type=int, default=8,
                    help="engine semaphore cap "
                         "(real parallelism ceiling under the GIL)")
    ap.add_argument("--contention",
                    choices=["low", "medium", "high"], default="medium")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--csv", type=str, default=None)
    args = ap.parse_args()

    print(f"\nCoreLock Load Sweep")
    print(f"  algorithm={args.algo}, contention={args.contention}")
    print(f"  threads from {args.start} to {args.max} (step {args.step})")
    print(f"  hold={args.hold}s, engine max_concurrent={args.concurrent}\n")

    results = run_load_sweep(
        start=args.start, stop=args.max, step=args.step,
        algorithm=Algorithm(args.algo),
        hold_seconds=args.hold,
        max_concurrent=args.concurrent,
        contention=args.contention,
        seed=args.seed,
    )
    print(format_table(results))

    inconsistent = [r for r in results if not r.consistent]
    if inconsistent:
        print(f"\n⚠️  CONSISTENCY FAILURES at thread counts: "
              f"{', '.join(str(r.num_threads) for r in inconsistent)}")
    else:
        print(f"\n✅ All {len(results)} load levels left the system consistent "
              f"(no stuck LOCKED seats, no orphan BOOKED seats).")

    if args.csv:
        import csv
        rows = [r.row() for r in results]
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"CSV written to {args.csv}")


if __name__ == "__main__":
    main()
