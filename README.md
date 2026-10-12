# CoreLock — Movie Ticket Booking Concurrency Simulator

**Team:** TriCoders | **Project Code:** OSDBMS-V-2026-T235

Combined OS + DBMS project. A movie-seat booking is modeled as a database
transaction; multiple users are simulated with real Python threads; an
OS-style scheduler decides request order; a Strict-2PL lock manager
prevents double-booking; a deadlock manager detects and resolves
circular waits; a recovery manager demonstrates crash recovery via
WAL-based UNDO/REDO.

See `PROJECT_CONTEXT.md` for the full design writeup.

## Console demo

```bash
python -m corelock.main
```

## Live dashboard

```bash
pip install -r requirements.txt
streamlit run corelock/dashboard/app.py
```

### What the dashboard shows

- **Booking form + live seat map, side by side** — the seat you book
  visibly turns yellow (LOCKED / "in cart"), then red (BOOKED).
- **Hold time slider 0.5s → 10s** — bigger hold makes LOCKED and WAITING
  states easy to see on stage.
- Both **Submit & COMMIT** and **Submit & ROLLBACK (voluntary)** buttons
  — the rollback path holds the seat then abandons payment.
- **Scheduler algorithm selector** with a "Queue for scheduler" mode:
  queue several bookings with different priorities / seat counts, watch
  the algorithm reorder them in the Ready Queue table, then click
  "Run all queued" to see the order actually applied.
- **Scenario buttons for evaluators:**
  - **Rush Hour** — configurable number of users flooding overlapping seats.
  - **Trigger Deadlock** — Dave [C1,C2] vs Eve [C2,C1]. The deadlock
    manager has a grace period so the wait-for graph, the pending-cycle
    banner, and the waiting queue are all visible *before* the victim
    is rolled back. This is the module the mentor cares most about.
  - **Simulate Crash** — dirty WRITE with no COMMIT on any seat.
  - **Run Recovery** — WAL scan → UNDO/REDO → report shown in the UI.
- **Waiting queue, Wait-for graph, Lock table, Deadlock history**
  — all as styled tables.
- **Transactions table** with colour-coded state badges.
- **WAL log table** (WRITE / COMMIT / ROLLBACK rows, colour-coded).
- **Event log** — every S-REQUEST, S-LOCK, X-REQUEST, X-LOCK, BROWSING,
  COMMIT, ROLLBACK, DEADLOCK event with a timestamp.

## Project structure

```
corelock/
├── seat_inventory.py       # Cinema → Screen → Show → Seat
├── transaction_manager.py  # Module 1: txn lifecycle
├── scheduler.py            # Module 2: FCFS / SJF / RR / Priority
├── execution_engine.py     # Module 3: threaded execution
├── lock_manager.py         # Module 4: S/X locks, Strict 2PL
├── deadlock_manager.py     # Module 5: wait-for graph, victim selection
├── recovery_manager.py     # Module 6: WAL, UNDO/REDO
├── dashboard/app.py        # Module 7: Streamlit UI
├── logs/                   # WAL written here at runtime
└── main.py                 # console demo
```
