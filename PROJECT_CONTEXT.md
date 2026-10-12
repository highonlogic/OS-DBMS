# PROJECT_CONTEXT.md — CoreLock

**Trigger keyword:** `OSDBMS` — use this file as full context whenever this keyword is invoked in AI coding tools (Cursor, Antigravity, etc.)

---

## 1. Project Identity

- **Title:** CoreLock: Simulating Transaction Scheduling and Concurrency Control
- **Team Name:** TriCoders
- **Project Code:** OSDBMS-V-2026-T235
- **Course:** BTech CSE, 5th Semester — Combined OS + DBMS Project
- **Team:** 3 members (team lead carries most of the implementation)

---

## 2. Real-Life Application (Mentor-Required Mapping)

Mentor rejected the original abstract plan for being too vague ("how will you implement it", "where's the data stored"). To fix this, the entire simulation is now grounded in a concrete, relatable real-life system:

### 🎬 Movie Ticket Booking System

This is the lens through which **every module below must be implemented and explained**.

| Concept in CoreLock | Maps to (Movie Booking) |
|---|---|
| Transaction | `User X books Seat [Row-Col] for Show [Movie, Time, Screen]` |
| S-Lock (Shared) | User selecting/browsing a seat, holding it in cart |
| X-Lock (Exclusive) | User confirming payment → final booking |
| Deadlock scenario | Two users try to lock the same seat(s) at the same instant → wait-for graph → victim selection → one booking rolled back |
| OS Scheduler | Decides the order in which booking requests are processed during a rush (e.g., popular show release) — FCFS by default, Priority for premium/member users |
| Crash & Recovery | Payment marked successful but seat-status update crashes mid-way → "Simulate Crash" button → Recovery Manager runs UNDO/REDO from transaction log to restore consistent state |
| Data model | `Screens → Shows → Seats` — seat grid (e.g. 10x10) per show, each seat has state: `Available / Locked / Booked` |

This solves both of the mentor's original objections:
- **"How will scheduling actually be implemented"** → concrete: incoming booking requests queue up, scheduler orders them.
- **"Where is data stored"** → seat inventory is a natural in-memory table/grid (Python dict/2D array), with file/log-based persistence for transaction logs — no external DB engine needed, but structurally equivalent to a real DB table.

---

## 3. Architecture / Workflow

```
Booking Request (Transaction Input)
   → Transaction Manager
   → OS Scheduler (FCFS / SJF / RR / Priority)
   → Concurrent Execution (Threads/Processes simulate multiple users booking at once)
   → Synchronization (Mutex/Semaphore)
   → Lock Manager (S-Lock/X-Lock on seats, 2PL / Strict 2PL)
   → splits into:
       (a) Deadlock Check → Rollback/Recovery
       (b) Seat/DB Access → Data Update (seat status change)
   → both feed into Transaction Logs
   → Recovery Manager (UNDO/REDO on crash)
   → Final Seat/Booking State
   → Dashboard (live view)
```

---

## 4. Modules (7 total)

1. **Transaction Manager** — booking request creation, READ/WRITE/COMMIT/ROLLBACK states
2. **OS Scheduler** — FCFS/SJF/RR/Priority; manages ready + waiting queue of booking requests; execution order
3. **Concurrent Execution Engine** — threads simulating multiple simultaneous users; mutex/semaphore; critical section; context-switch simulation
4. **Lock & Concurrency Manager** — S/X locks on seats; lock table; 2PL/Strict 2PL; compatibility matrix
5. **Deadlock Manager** — wait-for graph; cycle detection; victim selection; rollback/restart of losing transaction
6. **Recovery Manager** — transaction logs; checkpoints; UNDO/REDO; crash simulation ("Simulate Crash" + "Recover" buttons)
7. **Monitoring & Analytics Dashboard** — Gantt chart of scheduling, live seat map, transaction states, waiting/turnaround time, CPU utilization, lock status, deadlock count, execution history

---

## 5. Scope

**OS — must-have:** processes/threads, FCFS, SJF, Round Robin, Priority Scheduling, Mutex, Semaphore, Critical Section, Process States, Deadlock Detection
**OS — optional/advanced:** context switching visualization, IPC, resource allocation graph

**DBMS — must-have:** transactions, READ/WRITE, S/X locks, 2PL, Strict 2PL, serializability, deadlock, commit/rollback, ACID
**DBMS — advanced:** transaction logs, checkpointing, UNDO/REDO recovery, indexing, query performance analysis

---

## 6. Tech Stack

- **Core logic:** Python (`threading` module for concurrency simulation)
- **Dashboard:** Streamlit or HTML/JS + Chart.js (live view of seat grid, Gantt chart, lock status)
- **Data storage:** Fully in-memory (Python data structures — dict/2D array for seat grid) for working data; simple file/log-based storage for transaction logs and recovery (no external DB engine like MySQL/SQLite)
- **Version control:** Git/GitHub

---

## 7. Evaluation Metrics

- Transaction throughput (bookings processed per unit time)
- Average waiting time (per booking request)
- Deadlock frequency (how often seat-lock conflicts occur under load)

---

## 8. Known Challenges

- **Thread synchronization** — avoiding race conditions on shared seat data (mutex/semaphore)
- **Deadlock detection & recovery** — wait-for graph cycle detection + minimum-impact victim selection
- **OS Scheduler ↔ Lock Manager integration** — keeping request scheduling and seat-locking in sync
- **Real-time dashboard performance** — live Gantt chart + seat map updates without lag (esp. on Streamlit)

---

## 9. Suggested Code Structure

```
corelock/
├── transaction_manager.py     # booking request lifecycle
├── scheduler.py                # FCFS/SJF/RR/Priority queue logic
├── execution_engine.py         # thread simulation of concurrent users
├── lock_manager.py             # S/X locks, 2PL, lock table
├── deadlock_manager.py         # wait-for graph, detection, victim selection
├── recovery_manager.py         # logs, checkpoints, UNDO/REDO, crash sim
├── seat_inventory.py           # screens/shows/seats data model
├── dashboard/                  # Streamlit or HTML/JS+Chart.js UI
│   └── app.py
├── logs/                       # transaction log files
└── main.py                     # orchestration entry point
```

---

## 10. Milestones / Status Log

- Proposal (Phase 1) submitted with original workflow + 7 modules
- PPT (9 slides) created and presented to mentor
- Mentor rejected plan as too vague — required concrete implementation plan + real-life grounding
- **Movie Ticket Booking System** finalized as the real-life application (this update)
- Next: rebuild module explanations, seat-grid data model, and demo script around this use case before next mentor check-in
- Work paused at Module 7 (Monitoring & Analytics) — to resume after evaluation

---

## 11. Assumptions

- No external DB engine (MySQL/SQLite) used — in-memory + file logs stand in for persistent storage
- "Users" in the simulation are represented as threads/simulated processes, not real concurrent human traffic
- Seat grid size and number of shows/screens are configurable but kept small enough for live demo clarity (e.g., 1-2 screens, 10x10 seat grid)

---

## 12. References

- Standard OS scheduling algorithms (FCFS, SJF, Round Robin, Priority Scheduling)
- Standard DBMS concurrency control theory (2PL, Strict 2PL, S/X locks, serializability, ACID)
- Deadlock detection via wait-for graphs
- Write-ahead logging + UNDO/REDO recovery techniques
