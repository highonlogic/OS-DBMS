# CoreLock — Phase III Changes

**Team:** TriCoders · **Project Code:** OSDBMS-V-2026-T235
**Members:** Anmol Tyagi (lead), Divyanshi Vatsa, Sakshi Singh

This document lists every change made between the Phase-II submission and
the Phase-III submission, grouped by (a) the pending tasks explicitly
flagged in the Phase-II report, and (b) additional polish / fixes.

---

## 1. Phase-II pending tasks — status

The Phase-II report listed six pending tasks. All six are now addressed.

| # | Task | Assignee (per Phase-II) | Status in Phase-III | Where to see it |
|---|---|---|---|---|
| 1 | **Perfect working of Round Robin** | Sakshi Singh | ✅ Done — real time-slicing | `scheduler.py` (quantum attrs), `execution_engine.py` (preempt loop), Event log (PREEMPT events), Transactions table (`Ctx Switches` column), Gantt chart (red PREEMPTED bars) |
| 2 | **Real Gantt chart, CPU utilization, Lock-wait metrics** | Divyanshi Vatsa | ✅ Done — plotly timeline + metrics strip | Dashboard "Gantt chart" panel, metrics row ("CPU util", "Avg lock-wait (s)") |
| 3 | **Benchmark: throughput, avg waiting time, deadlock frequency per algorithm** | Anmol Tyagi | ✅ Done — standalone module + dashboard panel | `corelock/benchmark.py`, Dashboard "📈 Benchmark" panel |
| 4 | **WAL checkpoints and more recovery tests** | Sakshi Singh | ✅ Done — checkpoint API + fast-path recovery | `recovery_manager.py` (`take_checkpoint`, `_latest_checkpoint`, checkpoint-aware `recover`), Dashboard "📸 Checkpoint" button + checkpoint table |
| 5 | **Heavy Load Test (multiple threads)** | Anmol Tyagi | ✅ Done — standalone sweep + dashboard panel + consistency check | `corelock/load_test.py`, Dashboard "🔥 Load test" panel |
| 6 | **Wait-for graph drawing** | Divyanshi Vatsa | ✅ Done — real networkx graph with cycle highlighting | Dashboard "🕸️ Wait-for graph" panel (replaces old table) |

---

## 2. New files

| File | Purpose | Author |
|---|---|---|
| `corelock/benchmark.py` | Compare FCFS / SJF / PRIORITY / RR on identical synthetic workloads. CLI (`python -m corelock.benchmark -n 30 -c medium`) and library (`run_benchmark()`). | Anmol |
| `corelock/load_test.py` | Sweep concurrent-thread counts from 10 → N under one algorithm. CLI (`python -m corelock.load_test --max 60`) and library (`run_load_sweep()`). Each level is **consistency-checked**: no stuck LOCKED seats, no orphan BOOKED seats. | Anmol |
| `PHASE_III_CHANGES.md` | This file. | Anmol |

---

## 3. Changed files

### `corelock/transaction_manager.py` (Sakshi)
- **New metric fields on `Transaction`:**
  - `lock_wait_s`      — cumulative seconds spent blocked on lock acquires
  - `active_time_s`    — cumulative seconds doing actual work
  - `context_switches` — RR preemptions suffered
- **New helpers on `TransactionManager`:**
  - `add_lock_wait()`, `add_active_time()`, `record_context_switch()`
- **`snapshot()`** now includes `lock_wait_s` and `ctx_switches` so the
  dashboard table shows them.

### `corelock/scheduler.py` (Sakshi)
- **`Scheduler.__init__`** now accepts `rr_quantum_s` and
  `rr_switch_cost_s`. These are what makes Round-Robin behave as RR
  rather than FCFS-in-disguise.
- **New field `timeline`** (list of `{txn_id, user, start, end, phase}`)
  with a `record_slice()` helper. The ExecutionEngine logs a slice at
  the end of every phase (S_LOCK, HOLD, X_LOCK, COMMIT, PREEMPTED,
  ABORT). The dashboard reads this to draw the Gantt chart.
- Round-Robin now has a clear comment that it is interleaved via
  quantum preemption in the engine, not by sorting here.

### `corelock/execution_engine.py` (Anmol)
- **RR time-slicing in `_run_transaction_body`:** when
  `scheduler.algorithm == ROUND_ROBIN`, the browsing hold is cut into
  `rr_quantum_s`-sized chunks. After each chunk the engine
  (a) increments `txn.context_switches`,
  (b) logs a `PREEMPT` event,
  (c) sleeps for `rr_switch_cost_s` as a visible context-switch cost,
  (d) records a `PREEMPTED` slice into the Gantt timeline.
- **`_acquire_tracked()`** wraps every lock acquire, measures how long
  the thread was blocked, and records that into `txn.lock_wait_s`.
- **CPU utilization sampling:** `_enter_active` / `_leave_active` wrap
  the real work section inside the semaphore; `cpu_utilization()`
  returns `{wall_s, busy_s, capacity_s, utilization, max_concurrent}`.
  "Capacity" is `wall_s × max_concurrent`, so a utilization of 1.0
  means every engine slot was busy every moment.
- Every phase of `_run_transaction_body` now calls
  `scheduler.record_slice(...)` so the Gantt chart shows real timing,
  not just arrival order.

### `corelock/recovery_manager.py` (Anmol)
- **`take_checkpoint(cinema, active_txns)`** — captures the current
  committed seat state as a `CHECKPOINT` record in the WAL *and* in
  `self.checkpoints`.
- **`_latest_checkpoint()` / `_restore_from_checkpoint()`** — scans the
  WAL backwards for the most recent checkpoint and restores every seat
  from its snapshot.
- **`recover()`** is now **checkpoint-aware**. If a checkpoint exists,
  it restores from the checkpoint and only replays WAL records written
  AFTER the checkpoint — the standard DBMS fast-path. The report now
  returns `restored_from_checkpoint`, `replayed_records`, and
  `used_checkpoint` so you can prove the fast-path actually ran.
- **`recovery_stats()`** — totals for the dashboard metrics strip
  (write / commit / rollback / checkpoint counts).

### `corelock/dashboard/app.py` (Anmol)
Expanded roughly from 716 lines to 1047 lines. New sections:

- **Metrics strip** now shows Avg lock-wait (s), CPU util %, RR context
  switches, WAL writes / commits, and total checkpoints.
- **📸 Checkpoint button** next to Crash / Recover, so a mentor can
  demo "take checkpoint → crash → recover with fast-path" in one flow.
- **Real Gantt chart** (plotly `px.timeline`) replacing the old
  text list. Each txn gets one row; phases are color-coded. RR
  preemptions show up as red bars between HOLD slices.
- **Real wait-for graph** (networkx `DiGraph` → matplotlib → PNG).
  Nodes and edges inside a detected cycle are highlighted in red.
- **📈 Benchmark panel** — inline UI to call `run_benchmark()`;
  renders four bar charts (throughput, avg wait, deadlocks, abort rate)
  and offers a CSV download.
- **🔥 Load test panel** — inline UI to call `run_load_sweep()`;
  renders line charts (throughput vs threads, lock-wait + CPU util vs
  threads, deadlocks vs threads) and shows a consistency verdict.
- **RR quantum slider** in the sidebar — lets the mentor change the
  time quantum live and see how it affects context-switch counts and
  Gantt interleaving.
- **Checkpoint table** below the WAL table — shows every checkpoint
  with its timestamp, active-txn set, and # seats captured.
- **CHECKPOINT WAL rows** are colour-coded (purple) in the WAL table.

### `corelock/main.py`
Grew from 5 to 7 scripted scenarios: added scenario 5 (WAL checkpoint)
and scenario 7 (Round-Robin preemption demonstration), and prints the
CPU-utilization summary at the end.

### `.gitignore`
Extended to cover: `__pycache__`, `*.pyc`, virtualenvs, runtime
`*.jsonl` logs, `*.csv` benchmark/load-test output, IDE junk,
`.streamlit/` cache.

### `requirements.txt`
Added `plotly`, `networkx`, `matplotlib` for the Phase-III dashboard
graphs. Core modules (and `main.py`, `benchmark.py`, `load_test.py`)
still depend only on the Python standard library.

---

## 4. Author map (updated for Phase-III)

| Module | Author | Phase-III contribution |
|---|---|---|
| `transaction_manager.py` | Sakshi Singh | Lock-wait / active-time / context-switch tracking |
| `scheduler.py`           | Sakshi Singh | RR time-quantum fields, timeline recording |
| `execution_engine.py`    | Anmol Tyagi  | RR preemption loop, CPU util sampling, Gantt recording, lock-wait tracking |
| `lock_manager.py`        | Divyanshi Vatsa | (unchanged from Phase-II) |
| `deadlock_manager.py`    | Divyanshi Vatsa | (unchanged from Phase-II) |
| `recovery_manager.py`    | Anmol Tyagi  | Working CHECKPOINT, checkpoint-aware recovery, recovery_stats |
| `seat_inventory.py`      | Divyanshi Vatsa | (unchanged from Phase-II) |
| `dashboard/app.py`       | Anmol Tyagi  | Real Gantt + wait-for graph + metrics + checkpoint UI + benchmark + load-test panels |
| `benchmark.py`           | Anmol Tyagi  | **new in Phase-III** |
| `load_test.py`           | Anmol Tyagi  | **new in Phase-III** |

---

## 5. How to demo Phase-III to the mentor (5 minutes)

1. **Console demo** (shows all 7 scenarios in one run, including
   checkpoint fast-path and RR preemption):
   ```bash
   python -m corelock.main
   ```
2. **Live dashboard** (visual proof of every pending task):
   ```bash
   streamlit run corelock/dashboard/app.py
   ```
   Then on screen:
   - Set algorithm to **ROUND_ROBIN**, hold = 3s, submit a booking →
     watch the Gantt chart build HOLD + red PREEMPTED slices.
   - Click **📸 Checkpoint** → **💥 Crash** on seat E1 →
     **🛠️ Recover**. The success banner says
     "fast-path from checkpoint: N seats restored, M WAL records replayed".
   - Click **⚠️ Deadlock** (hold 5s) → watch the wait-for graph form
     with red nodes + red edges inside the cycle, then the pending-cycle
     banner counts down, then the min-work victim is rolled back.
3. **Benchmark panel** → **▶ Run benchmark** (n=25, medium contention) →
   see the four algorithms compared on identical workload. Download CSV.
4. **Load-test panel** → **▶ Run load sweep** (10 → 50 step 10) → see
   throughput saturate, lock-wait rise, and the consistency check pass
   at every level.
5. **CLI benchmarks** (for the report appendix):
   ```bash
   python -m corelock.benchmark -n 30 -c high  --hold 0.3 --csv bench_high.csv
   python -m corelock.load_test  --max 60      --algo PRIORITY --csv load.csv
   ```
