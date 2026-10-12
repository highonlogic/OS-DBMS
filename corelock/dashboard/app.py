"""
dashboard/app.py -- CoreLock live dashboard.

Run with:
    streamlit run corelock/dashboard/app.py

Layout (single scroll, no tabs):
  * Top: booking form (LEFT) + live seat map (RIGHT) side by side.
  * Metrics strip: Committed / Aborted / Waiting / Active / Deadlocks / Avg wait.
  * Scenario buttons: Rush Hour / Trigger Deadlock / Crash / Recover.
  * Scheduler: ready queue (in current-algo order) + "Run scheduler queue" button.
  * Waiting + locks: waiting queue, wait-for graph, lock table, deadlock cycles.
  * Transactions table.
  * WAL log table + Event log table.
"""

import os
import sys
import time

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from corelock.seat_inventory import Cinema, SeatState
from corelock.transaction_manager import TransactionManager
from corelock.lock_manager import LockManager
from corelock.scheduler import Scheduler, Algorithm
from corelock.execution_engine import ExecutionEngine
from corelock.recovery_manager import RecoveryManager

SHOW_ID = "S101"

st.set_page_config(
    page_title="CoreLock",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================================
# CSS
# ============================================================================

st.markdown(
    """
    <style>
      h1 { margin-top: 0 !important; padding-top: 0 !important; }
      .seat-cell {
        display: block;
        text-align: center;
        padding: 8px 2px;
        margin: 3px 0;
        border-radius: 8px;
        font-weight: 600;
        font-size: 13px;
        border: 2px solid transparent;
        min-height: 40px;
      }
      .seat-available { background: #DCFCE7; color: #166534; border-color: #86EFAC; }
      .seat-locked    { background: #FEF3C7; color: #92400E; border-color: #FBBF24; }
      .seat-booked    { background: #FEE2E2; color: #991B1B; border-color: #F87171; }
      .screen-bar {
        text-align:center; background:#334155; color:#F1F5F9;
        padding:6px; border-radius:6px; margin-bottom:10px;
        font-weight:600; letter-spacing:6px;
      }
      .stage-title {
        font-size: 17px; font-weight: 700; color: #1E293B;
        border-bottom: 2px solid #E2E8F0; padding-bottom: 4px;
        margin-top: 6px; margin-bottom: 8px;
      }
      .pending-cycle {
        background: #FEF3C7; border: 2px solid #F59E0B;
        padding: 10px 14px; border-radius: 8px; margin: 8px 0;
        color: #92400E; font-weight: 600;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

# ============================================================================
# State
# ============================================================================

def build_engine():
    cinema = Cinema.seed_demo_data()
    tm = TransactionManager()
    lm = LockManager(wait_timeout=3.0)
    sch = Scheduler(algorithm=Algorithm.PRIORITY)
    rec = RecoveryManager()
    eng = ExecutionEngine(cinema, tm, lm, sch, rec,
                          max_concurrent=6, monitor_interval=0.4)
    eng.start_monitor()
    return dict(cinema=cinema, txn_manager=tm, lock_manager=lm,
                scheduler=sch, recovery_manager=rec, engine=eng)


def init_state():
    if "engine" not in st.session_state:
        for k, v in build_engine().items():
            st.session_state[k] = v
        st.session_state.recovery_report = None
        st.session_state.running_threads = []
        st.session_state.auto_refresh = True
        st.session_state.guest_counter = 0
        st.session_state.sim_start = time.time()


def rel_time(ts: float) -> float:
    """Convert an absolute Unix timestamp to seconds-since-simulation-start."""
    try:
        return round(ts - st.session_state.sim_start, 3)
    except Exception:
        return round(ts, 3)


init_state()

cinema           = st.session_state.cinema
txn_manager      = st.session_state.txn_manager
lock_manager     = st.session_state.lock_manager
scheduler        = st.session_state.scheduler
recovery_manager = st.session_state.recovery_manager
engine           = st.session_state.engine


def any_thread_alive():
    st.session_state.running_threads = [
        t for t in st.session_state.running_threads if t.is_alive()
    ]
    return bool(st.session_state.running_threads)


# ============================================================================
# HEADER (minimal)
# ============================================================================

st.title("🎬 CoreLock — Movie Ticket Booking Simulator")

# ============================================================================
# SIDEBAR
# ============================================================================

with st.sidebar:
    st.header("⚙️ Simulation controls")
    st.session_state.auto_refresh = st.toggle(
        "Auto-refresh (live updates)", value=st.session_state.auto_refresh,
        help="When ON, the page reruns every 0.5s while background threads "
             "are working, so you see LOCKED / WAITING states as they happen.",
    )

    st.divider()
    st.subheader("🎯 Legend")
    st.markdown(
        '<div class="seat-cell seat-available">Available (green)</div>'
        '<div class="seat-cell seat-locked">Locked / in cart (yellow)</div>'
        '<div class="seat-cell seat-booked">Booked (red)</div>',
        unsafe_allow_html=True,
    )
    st.caption("Transactions in the table use the same colours for their state.")

    st.divider()
    st.subheader("🔄 Reset")
    if st.button("Reset entire simulation", use_container_width=True):
        try:
            engine.stop_monitor()
        except Exception:
            pass
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()


# ============================================================================
# TOP SECTION: BOOKING FORM (LEFT) + SEAT MAP (RIGHT)
# ============================================================================

st.markdown('<div class="stage-title">🎟️ Book a seat + Live seat map</div>',
            unsafe_allow_html=True)

form_col, map_col = st.columns([1, 1], gap="large")

# ---------------------------- Booking form (left) ----------------------------
with form_col:
    st.markdown("**Submit a booking request**")

    f_row1 = st.columns([2, 2])
    with f_row1[0]:
        user_name = st.text_input(
            "User name",
            value=f"Guest{st.session_state.guest_counter + 1}",
            key="user_name_input",
        )
    with f_row1[1]:
        seats_input = st.text_input(
            "Seat(s), comma-separated",
            value="A1",
            key="seats_input",
            help="e.g. A1  or  A1, A2, A3",
        )

    algo_name = st.selectbox(
        "Scheduler algorithm",
        [a.value for a in Algorithm],
        index=[a.value for a in Algorithm].index(scheduler.algorithm.value),
        key="algo_input",
        help="FCFS: earliest arrival first. "
             "SJF: shortest burst first (burst = number of seats, since each seat "
             "needs its own lock-acquire + commit cycle). "
             "PRIORITY: lowest priority number first. "
             "ROUND_ROBIN: arrival order (no preemption in this sim, so visually "
             "identical to FCFS -- real RR fairness comes from time-slicing).",
    )
    scheduler.algorithm = Algorithm(algo_name)
    st.caption(
        "ℹ️ The algorithm only visibly re-orders when **multiple bookings are "
        "queued at the same time**. Tick the box below, add several, then switch "
        "algorithms to watch the Ready Queue table re-order."
    )

    f_row2 = st.columns([1, 2])
    with f_row2[0]:
        # Priority is used by exactly one algorithm. Hide the input otherwise
        # so users don't think it affects FCFS / SJF / RR.
        if algo_name == "PRIORITY":
            priority = st.number_input(
                "Priority (lower = higher)", 1, 10, 5,
                key="priority_input",
                help="Used by the PRIORITY scheduling algorithm.",
            )
        else:
            priority = 5
            st.caption(
                f"_Priority input hidden — `{algo_name}` doesn't use priority._"
            )
    with f_row2[1]:
        hold_time = st.slider(
            "Hold time -- seat stays LOCKED (yellow) for this many seconds",
            min_value=0.5, max_value=10.0, value=3.0, step=0.5,
            key="hold_time_input",
            help="Long hold = you can see the LOCKED state clearly, "
                 "and it lets deadlocks form and be resolved visibly.",
        )

    queue_only = st.checkbox(
        "Queue for scheduler (don't run yet)",
        value=False, key="queue_only_input",
        help="Submissions go to the Ready Queue instead of running immediately, "
             "so you can see the algorithm effect clearly.",
    )

    if st.button("📥 Load 4 demo bookings into the queue",
                 use_container_width=True,
                 help="Quickly fills the ready queue with 4 bookings of varied "
                      "seat-count and priority, so switching the algorithm "
                      "visibly re-orders them."):
        demo_bookings = [
            ("Alice",   ["A3", "A4", "A5"], 5),   # 3 seats, prio 5
            ("Bob",     ["B1"],              1),  # 1 seat,  prio 1 (highest)
            ("Charlie", ["C1", "C2"],        8),  # 2 seats, prio 8 (lowest)
            ("Dave",    ["D1"],              3),  # 1 seat,  prio 3
        ]
        for u, seats, p in demo_bookings:
            st.session_state.guest_counter += 1
            txn = txn_manager.create_transaction(u, SHOW_ID, seats, priority=p)
            scheduler.add(txn)
        st.rerun()

    def _parse_seats():
        return [s.strip().upper() for s in seats_input.split(",") if s.strip()]

    b_row = st.columns(2)
    with b_row[0]:
        if st.button("✅ Submit & COMMIT",
                     use_container_width=True, type="primary"):
            seat_ids = _parse_seats()
            if seat_ids:
                st.session_state.guest_counter += 1
                txn = txn_manager.create_transaction(
                    user_name, SHOW_ID, seat_ids, priority=int(priority)
                )
                scheduler.add(txn)
                if not queue_only:
                    batch = scheduler.next_batch(1)
                    threads = engine.run_all_threaded(
                        batch, default_hold=float(hold_time), wait=False,
                    )
                    st.session_state.running_threads.extend(threads)
                st.rerun()

    with b_row[1]:
        if st.button("↩️ Submit & ROLLBACK (voluntary)",
                     use_container_width=True):
            seat_ids = _parse_seats()
            if seat_ids:
                st.session_state.guest_counter += 1
                txn = txn_manager.create_transaction(
                    user_name, SHOW_ID, seat_ids, priority=int(priority)
                )
                scheduler.add(txn)
                if not queue_only:
                    batch = scheduler.next_batch(1)
                    threads = engine.run_all_threaded(
                        batch,
                        commit_map={txn.txn_id: False},
                        default_hold=float(hold_time), wait=False,
                    )
                    st.session_state.running_threads.extend(threads)
                st.rerun()

    st.caption(
        "**Submit & COMMIT** confirms payment after the hold. "
        "**Submit & ROLLBACK** demonstrates a voluntary rollback -- the user "
        "holds the seat, then abandons payment; the seat returns to AVAILABLE."
    )

# ---------------------------- Seat map (right) ------------------------------
with map_col:
    st.markdown("**Live seat map**")
    show = cinema.get_show(SHOW_ID)
    st.caption(f"Show {show.show_id} — {show.movie} @ {show.time} "
               f"({show.rows}×{show.cols})")

    st.markdown('<div class="screen-bar">SCREEN</div>', unsafe_allow_html=True)

    state_class = {
        "AVAILABLE": "seat-available",
        "LOCKED":    "seat-locked",
        "BOOKED":    "seat-booked",
    }
    for r in range(show.rows):
        row_letter = chr(ord("A") + r)
        cols_ui = st.columns(show.cols)
        for c in range(1, show.cols + 1):
            seat = show.get_seat(f"{row_letter}{c}")
            css = state_class[seat.state.value]
            tip = f"{seat.seat_id} — {seat.state.value}"
            if seat.locked_by:
                tip += f" (locked by {seat.locked_by})"
            if seat.booked_by:
                tip += f" (booked by {seat.booked_by})"
            cols_ui[c - 1].markdown(
                f'<div class="seat-cell {css}" title="{tip}">{seat.seat_id}</div>',
                unsafe_allow_html=True,
            )

st.divider()

# ============================================================================
# METRICS STRIP
# ============================================================================

snaps = txn_manager.all_snapshots()
def _cnt(s): return sum(1 for x in snaps if x["state"] == s)
committed = _cnt("COMMITTED")
aborted   = _cnt("ABORTED")
active    = _cnt("ACTIVE")
waiting   = _cnt("WAITING")
deadlocks = len(engine.deadlock_manager.cycle_history())
avg_wait  = round(
    sum(s["wait_s"] for s in snaps) / max(1, len(snaps)),
    3,
)

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Committed", committed)
m2.metric("Aborted", aborted)
m3.metric("Active",  active)
m4.metric("Waiting", waiting)
m5.metric("Deadlocks", deadlocks)
m6.metric("Avg wait (s)", avg_wait)

# pending-cycle banner (deadlock forming, not yet resolved)
pending = engine.deadlock_manager.pending_cycle_info()
if pending:
    st.markdown(
        f'<div class="pending-cycle">'
        f'⚠️ Deadlock cycle observed: {" → ".join(pending["txns"])} '
        f'| resolving in {pending["will_resolve_in_s"]}s '
        f'(watched for {pending["elapsed_s"]}s)'
        f'</div>',
        unsafe_allow_html=True,
    )

st.divider()

# ============================================================================
# SCENARIO BUTTONS
# ============================================================================

st.markdown('<div class="stage-title">🎯 Demo scenarios</div>',
            unsafe_allow_html=True)

scen_col1, scen_col2 = st.columns([2, 3], gap="large")

with scen_col1:
    st.markdown("**Rush Hour** — flood the scheduler")
    rh_users = st.slider("How many users?", 3, 12, 6, key="rh_users")
    rh_hold  = st.slider("Each holds for (s)", 0.5, 5.0, 1.5, 0.5, key="rh_hold")
    if st.button("👥 Trigger Rush Hour",
                 use_container_width=True, disabled=any_thread_alive()):
        import random
        show = cinema.get_show(SHOW_ID)
        all_seats = show.all_seat_ids()
        # pick overlapping seats so contention actually happens
        hot_zone = ["A3", "A4", "A5", "B3", "B4", "B5"]
        txns = []
        for i in range(rh_users):
            st.session_state.guest_counter += 1
            u = f"RushUser{i+1}"
            seats = random.sample(hot_zone, k=random.choice([1, 1, 2]))
            p = random.randint(1, 8)
            t = txn_manager.create_transaction(u, SHOW_ID, seats, priority=p)
            scheduler.add(t)
            txns.append(t)
        batch = scheduler.next_batch(len(txns))
        threads = engine.run_all_threaded(
            batch, default_hold=float(rh_hold), wait=False,
        )
        st.session_state.running_threads.extend(threads)
        st.rerun()

with scen_col2:
    st.markdown("**Deadlock, Crash & Recovery**")
    d_col, c_col, r_col = st.columns(3)

    with d_col:
        dl_hold = st.slider("Deadlock hold (s)", 2.0, 10.0, 5.0, 0.5,
                            key="dl_hold_slider",
                            help="Longer = the wait-for graph & pending cycle "
                                 "are visible on screen for longer before resolution.")
        if st.button("⚠️ Trigger Deadlock",
                     use_container_width=True, disabled=any_thread_alive()):
            # Dave: [C1, C2], Eve: [C2, C1] --> circular wait on X-upgrade.
            dave = txn_manager.create_transaction("Dave", SHOW_ID,
                                                  ["C1", "C2"], priority=4)
            eve  = txn_manager.create_transaction("Eve",  SHOW_ID,
                                                  ["C2", "C1"], priority=4)
            scheduler.add(dave); scheduler.add(eve)
            batch = scheduler.next_batch(2)
            threads = engine.run_all_threaded(
                batch,
                hold_map={dave.txn_id: float(dl_hold),
                          eve.txn_id:  float(dl_hold)},
                wait=False,
            )
            st.session_state.running_threads.extend(threads)
            st.rerun()

    with c_col:
        crash_seat = st.text_input("Crash seat", value="E1",
                                   key="crash_seat_input",
                                   help="Simulates a dirty WRITE on this seat "
                                        "with no COMMIT logged.")
        if st.button("💥 Simulate Crash",
                     use_container_width=True, disabled=any_thread_alive()):
            recovery_manager.simulate_crash(cinema,
                                            crash_seat.strip().upper(), SHOW_ID)
            engine._log("CRASH",
                        f"dirty write injected on {crash_seat.upper()} "
                        f"(no COMMIT logged)", "T_CRASH")
            st.session_state.recovery_report = None
            st.rerun()

    with r_col:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        st.markdown("&nbsp;", unsafe_allow_html=True)
        if st.button("🛠️ Run Recovery",
                     use_container_width=True, disabled=any_thread_alive()):
            report = recovery_manager.recover(cinema)
            st.session_state.recovery_report = report
            engine._log("RECOVER",
                        f"UNDO={len(report['undone'])} REDO={len(report['redone'])}",
                        "RECOVERY")
            st.rerun()

if st.session_state.recovery_report is not None:
    r = st.session_state.recovery_report
    st.success(
        f"✅ Recovery complete — "
        f"UNDO applied to {len(r['undone'])} seat(s), "
        f"REDO applied to {len(r['redone'])} seat(s)."
    )
    with st.expander("View recovery report"):
        rc1, rc2 = st.columns(2)
        with rc1:
            st.markdown("**UNDO (uncommitted writes rolled back)**")
            if r["undone"]:
                st.dataframe(pd.DataFrame(r["undone"]),
                             use_container_width=True, hide_index=True)
            else:
                st.caption("Nothing to undo.")
        with rc2:
            st.markdown("**REDO (committed writes re-applied)**")
            if r["redone"]:
                st.dataframe(pd.DataFrame(r["redone"]),
                             use_container_width=True, hide_index=True)
            else:
                st.caption("Nothing to redo.")

st.divider()

# ============================================================================
# SCHEDULER: ready queue + run queue button + Gantt
# ============================================================================

st.markdown('<div class="stage-title">📊 Scheduler '
            '— algorithm chooses the order</div>',
            unsafe_allow_html=True)

sch_left, sch_right = st.columns([3, 2])

with sch_left:
    st.markdown(
        f"**Ready queue** (sorted by `{scheduler.algorithm.value}`) "
        f"— {len(scheduler.ready_queue)} transaction(s) waiting to run"
    )
    ready = scheduler.ready_snapshot()
    if ready:
        for r in ready:
            r["arrived_at"] = rel_time(r["arrived_at"])
        df = pd.DataFrame(ready).rename(columns={
            "order": "Run order", "txn_id": "Txn ID", "user": "User",
            "arrived_at": "Arrived (s)",
            "seats": "Seats",
            "burst_seats": "Burst (seats)",
            "priority": "Priority",
        })
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption(
            f"Sorted by **{scheduler.algorithm.value}** — "
            + {
                "FCFS": "lowest **Arrived (s)** first.",
                "SJF": "lowest **Burst (seats)** first "
                       "(ties broken by Arrived).",
                "PRIORITY": "lowest **Priority** value first "
                            "(ties broken by Arrived).",
                "ROUND_ROBIN": "arrival order (no preemption in this sim, so "
                               "visually identical to FCFS).",
            }.get(scheduler.algorithm.value, "")
        )

        rq_hold = st.slider("Hold (s) for each queued booking",
                            0.5, 10.0, 2.0, 0.5, key="rq_hold_slider")
        if st.button("▶ Run all queued (in algorithm order)",
                     use_container_width=True, type="primary"):
            n = len(scheduler.ready_queue)
            batch = scheduler.next_batch(n)
            threads = engine.run_all_threaded(
                batch, default_hold=float(rq_hold), wait=False,
            )
            st.session_state.running_threads.extend(threads)
            st.rerun()
    else:
        st.caption(
            "Ready queue is empty. Tick **Queue for scheduler** in the "
            "booking form above and submit several bookings to see the "
            "algorithm reorder them here."
        )

with sch_right:
    st.markdown("**Execution order** (Gantt)")
    order = scheduler.gantt_snapshot()
    if order:
        st.markdown(" → ".join(f"`{tid}`" for tid in order))
        st.caption(f"{len(order)} transaction(s) have been dispatched so far.")
    else:
        st.caption("Nothing dispatched yet.")

st.divider()

# ============================================================================
# WAITING QUEUE + LOCK TABLE + WAIT-FOR GRAPH + DEADLOCK HISTORY
# ============================================================================

st.markdown('<div class="stage-title">🔒 Locks & waiting</div>',
            unsafe_allow_html=True)

wl_left, wl_right = st.columns(2)

with wl_left:
    st.markdown("**⏳ Waiting queue (blocked transactions)**")
    wait_rows = lock_manager.waiting_queue_snapshot()
    if wait_rows:
        df = pd.DataFrame(wait_rows).rename(columns={
            "seat_id": "Seat", "waiting_txn": "Waiting Txn",
            "wants": "Wants", "blocked_by": "Blocked By",
        })
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No transactions are waiting right now.")

    st.markdown("**🕸️ Wait-for graph (edges: waiter → holder)**")
    edges = engine.deadlock_manager.wait_for_edges()
    if edges:
        df = pd.DataFrame(edges).rename(columns={
            "waiter": "Waiter", "holder": "Blocked on (holder)",
        })
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.caption("No wait-for edges right now.")

with wl_right:
    st.markdown("**🔒 Lock table** (who holds which seat)")
    lock_rows = lock_manager.lock_table_snapshot()
    if lock_rows:
        df = pd.DataFrame(lock_rows).rename(columns={
            "seat_id": "Seat", "lock_type": "Lock",
            "holders": "Holders", "num_holders": "# Holders",
        })
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No locks currently held.")

    st.markdown("**💥 Deadlock history**")
    cycles = engine.deadlock_manager.cycle_history()
    if cycles:
        for i, cyc in enumerate(cycles, 1):
            st.markdown(f"**Cycle #{i}:** " +
                        " → ".join(f"`{n}`" for n in cyc))
        st.caption(
            f"Victims rolled back so far: "
            f"{', '.join(f'`{v}`' for v in engine.deadlock_victims) or 'none'}"
        )
    else:
        st.caption("No deadlocks detected yet.")

st.divider()

# ============================================================================
# TRANSACTIONS TABLE
# ============================================================================

st.markdown('<div class="stage-title">📋 Transactions</div>',
            unsafe_allow_html=True)

if snaps:
    df = pd.DataFrame(snaps).rename(columns={
        "txn_id": "Txn ID", "user": "User", "seats": "Seats",
        "priority": "Priority", "state": "State", "waiting_on": "Waiting On",
        "wait_s": "Wait (s)", "turnaround_s": "Turnaround (s)",
        "ops": "Ops", "reason": "Reason",
    })

    def color_state(v):
        return {
            "COMMITTED": "background-color:#DCFCE7;color:#166534;font-weight:600;",
            "ABORTED":   "background-color:#FEE2E2;color:#991B1B;font-weight:600;",
            "WAITING":   "background-color:#FEF3C7;color:#92400E;font-weight:600;",
            "ACTIVE":    "background-color:#DBEAFE;color:#1E40AF;font-weight:600;",
            "CREATED":   "background-color:#E5E7EB;color:#374151;font-weight:600;",
        }.get(v, "")

    st.dataframe(df.style.map(color_state, subset=["State"]),
                 use_container_width=True, hide_index=True)
else:
    st.info("No transactions yet. Submit one from the form above, or trigger a scenario.")

st.divider()

# ============================================================================
# WAL LOG + EVENT LOG
# ============================================================================

st.markdown('<div class="stage-title">📜 Transaction log (WAL) '
            '— used by Recovery Manager for UNDO/REDO</div>',
            unsafe_allow_html=True)

log_rows = recovery_manager.log_snapshot()
if log_rows:
    for r in log_rows:
        r["ts"] = rel_time(r["ts"])
    df = pd.DataFrame(log_rows).rename(columns={
        "ts": "Time (s)", "txn_id": "Txn ID", "op": "Op",
        "seat_id": "Seat", "before": "Before", "after": "After",
    })

    def color_op(v):
        return {
            "WRITE":    "background-color:#E0E7FF;color:#3730A3;font-weight:600;",
            "COMMIT":   "background-color:#DCFCE7;color:#166534;font-weight:600;",
            "ROLLBACK": "background-color:#FEE2E2;color:#991B1B;font-weight:600;",
        }.get(v, "")

    st.dataframe(df.style.map(color_op, subset=["Op"]),
                 use_container_width=True, hide_index=True, height=260)
else:
    st.caption("WAL is empty.")

st.markdown('<div class="stage-title" style="margin-top:12px;">'
            '📡 Event log (last 40 events)</div>',
            unsafe_allow_html=True)

events = engine.get_event_log()[-40:]
if events:
    events = [{**e, "ts": rel_time(e["ts"])} for e in events]
    df = pd.DataFrame(events).rename(columns={
        "ts": "Time (s)", "event": "Event", "txn": "Txn", "message": "Message",
    })
    st.dataframe(df, use_container_width=True, hide_index=True, height=300)
else:
    st.caption("No events yet.")

# ============================================================================
# AUTO-REFRESH
# ============================================================================

if st.session_state.auto_refresh and any_thread_alive():
    time.sleep(0.5)
    st.rerun()
