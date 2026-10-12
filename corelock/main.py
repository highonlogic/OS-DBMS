"""
main.py -- CoreLock console demo.

Run with:
    python -m corelock.main
"""

from .seat_inventory import Cinema
from .transaction_manager import TransactionManager
from .lock_manager import LockManager
from .scheduler import Scheduler, Algorithm
from .execution_engine import ExecutionEngine
from .recovery_manager import RecoveryManager


def print_header(title: str):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def print_seat_grid(cinema: Cinema, show_id: str):
    show = cinema.get_show(show_id)
    print(f"\nSeat grid ({show.movie} @ {show.time}):")
    for r in range(show.rows):
        row_letter = chr(ord("A") + r)
        cells = []
        for c in range(1, show.cols + 1):
            seat = show.get_seat(f"{row_letter}{c}")
            symbol = {"AVAILABLE": ".", "LOCKED": "L", "BOOKED": "X"}[seat.state.value]
            cells.append(symbol)
        print(f"  Row {row_letter}: " + " ".join(cells))
    print("  Legend: . = available, L = locked (in cart), X = booked")


def main():
    cinema = Cinema.seed_demo_data()
    show_id = "S101"

    tm = TransactionManager()
    lm = LockManager(wait_timeout=2.0)
    sch = Scheduler(algorithm=Algorithm.PRIORITY)
    rec = RecoveryManager()
    engine = ExecutionEngine(cinema, tm, lm, sch, rec,
                             max_concurrent=6, monitor_interval=0.2)
    engine.start_monitor()

    # ---- Scenario 1-3 -------------------------------------------------------
    print_header("SCENARIO 1-3: Normal, Voluntary Rollback, Conflict")
    alice = tm.create_transaction("Alice", show_id, ["A1", "A2"], priority=1)
    bob = tm.create_transaction("Bob", show_id, ["B1", "B2"], priority=3)
    sch.add(alice); sch.add(bob)
    engine.run_all_threaded(sch.next_batch(2),
                            commit_map={bob.txn_id: False},
                            default_hold=0.3, wait=True)

    charlie = tm.create_transaction("Charlie", show_id, ["A1", "A2"], priority=2)
    sch.add(charlie)
    engine.run_all_threaded(sch.next_batch(1),
                            default_hold=0.3, wait=True)
    print_seat_grid(cinema, show_id)

    # ---- Scenario 4: Deadlock -----------------------------------------------
    print_header("SCENARIO 4: Deadlock (Dave vs Eve)")
    dave = tm.create_transaction("Dave", show_id, ["C1", "C2"], priority=4)
    eve = tm.create_transaction("Eve", show_id, ["C2", "C1"], priority=4)
    sch.add(dave); sch.add(eve)
    engine.run_all_threaded(sch.next_batch(2),
                            default_hold=1.5, wait=True)

    print("\nCycles detected:")
    for cyc in engine.deadlock_manager.cycle_history():
        print("  " + " -> ".join(cyc))
    print_seat_grid(cinema, show_id)

    # ---- Scenario 5: Crash & Recovery ---------------------------------------
    print_header("SCENARIO 5: Crash & Recovery")
    print("Simulating crash: dirty write on D1...")
    engine.recovery_manager.simulate_crash(cinema, "D1", show_id)
    print_seat_grid(cinema, show_id)

    print("\nRunning recovery (UNDO/REDO)...")
    report = engine.recovery_manager.recover(cinema)
    print(f"  UNDO: {report['undone']}")
    print(f"  REDO: {report['redone']}")
    print_seat_grid(cinema, show_id)

    # ---- Summary ------------------------------------------------------------
    print_header("EXECUTION ORDER")
    print(" -> ".join(sch.gantt_snapshot()))

    print_header("TRANSACTIONS")
    for s in tm.all_snapshots():
        print(s)

    engine.stop_monitor()


if __name__ == "__main__":
    main()
