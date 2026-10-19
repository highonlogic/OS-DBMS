# Author: Divyanshi Vatsa
# Seat Inventory: Cinema, Screen, Show, Seat data models

import threading
from enum import Enum
from dataclasses import dataclass
from typing import Dict, List, Optional


class SeatState(Enum):
    AVAILABLE = "AVAILABLE"
    LOCKED = "LOCKED"
    BOOKED = "BOOKED"


@dataclass
class Seat:
    seat_id: str
    state: SeatState = SeatState.AVAILABLE
    locked_by: Optional[str] = None
    booked_by: Optional[str] = None

    def snapshot(self) -> dict:
        return {
            "seat_id": self.seat_id,
            "state": self.state.value,
            "locked_by": self.locked_by,
            "booked_by": self.booked_by,
        }


class Show:
    def __init__(self, show_id: str, movie: str, time: str, rows: int = 5, cols: int = 6):
        self.show_id = show_id
        self.movie = movie
        self.time = time
        self.rows = rows
        self.cols = cols
        self._lock = threading.Lock()
        self.seats: Dict[str, Seat] = {}
        for r in range(rows):
            row_letter = chr(ord("A") + r)
            for c in range(1, cols + 1):
                seat_id = f"{row_letter}{c}"
                self.seats[seat_id] = Seat(seat_id=seat_id)

    def get_seat(self, seat_id: str) -> Optional[Seat]:
        with self._lock:
            return self.seats.get(seat_id)

    def all_seat_ids(self) -> List[str]:
        return list(self.seats.keys())


class Screen:
    def __init__(self, screen_id: str):
        self.screen_id = screen_id
        self.shows: Dict[str, Show] = {}

    def add_show(self, show: Show):
        self.shows[show.show_id] = show

    def get_show(self, show_id: str) -> Optional[Show]:
        return self.shows.get(show_id)


class Cinema:
    def __init__(self):
        self.screens: Dict[str, Screen] = {}

    def add_screen(self, screen: Screen):
        self.screens[screen.screen_id] = screen

    def get_show(self, show_id: str) -> Optional[Show]:
        for screen in self.screens.values():
            s = screen.get_show(show_id)
            if s:
                return s
        return None

    @classmethod
    def seed_demo_data(cls) -> "Cinema":
        cinema = cls()
        screen = Screen("Screen-1")
        show = Show(show_id="S101", movie="Inception", time="7:00 PM", rows=5, cols=6)
        screen.add_show(show)
        cinema.add_screen(screen)
        return cinema
