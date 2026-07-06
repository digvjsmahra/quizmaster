import secrets
import string
import time
from dataclasses import dataclass
from typing import Literal

from quiz_loader import Question


def _generate_join_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(4))


@dataclass
class Player:
    id: str
    name: str
    connected: bool
    joined_at: float


@dataclass
class BuzzEntry:
    player_id: str
    received_at: float


class Game:
    def __init__(self, questions: dict[str, list[Question]]):
        self.questions: dict[str, list[Question]] = questions
        self._boards: list[str] = list(questions.keys())
        self._all_questions: dict[str, Question] = {
            q.id: q for qs in questions.values() for q in qs
        }

        self.phase: Literal["lobby", "live"] = "lobby"
        self.join_code: str = _generate_join_code()
        self.players: dict[str, Player] = {}
        self.roster: list[str] = []
        self.queue: list[BuzzEntry] = []
        self.queue_locked: bool = False
        self.scores: dict[str, dict[str, float]] = {}
        self.closed_questions: set[str] = set()

    # ------------------------------------------------------------------
    # Lobby
    # ------------------------------------------------------------------

    def player_join(self, name: str) -> tuple[str, str]:
        name = name.strip()
        if not name:
            raise ValueError("Name cannot be empty.")
        player_id = secrets.token_urlsafe(8)
        self.players[player_id] = Player(
            id=player_id, name=name, connected=True, joined_at=time.monotonic()
        )
        return player_id, self.phase

    def start_quiz(self) -> list[str]:
        if self.phase == "live":
            return self.roster
        self.phase = "live"
        self.roster = sorted(
            self.players.keys(), key=lambda pid: self.players[pid].joined_at
        )
        return self.roster

    # ------------------------------------------------------------------
    # Queue
    # ------------------------------------------------------------------

    def player_buzz(self, player_id: str) -> list[BuzzEntry] | None:
        if self.phase != "live":
            return None
        if self.queue_locked:
            return None
        if player_id not in self.players:
            return None
        if any(e.player_id == player_id for e in self.queue):
            return None
        self.queue.append(BuzzEntry(player_id=player_id, received_at=time.monotonic()))
        return list(self.queue)

    def queue_freeze(self) -> None:
        self.queue_locked = True

    def queue_reset(self) -> None:
        self.queue.clear()
        self.queue_locked = False

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def roster_add(self, name: str) -> str:
        name = name.strip()
        if not name:
            raise ValueError("Name cannot be empty.")
        player_id = secrets.token_urlsafe(8)
        self.players[player_id] = Player(
            id=player_id, name=name, connected=True, joined_at=time.monotonic()
        )
        self.roster.append(player_id)
        return player_id

    def question_exists(self, question_id: str) -> bool:
        return question_id in self._all_questions

    def question_submit(self, question_id: str, scores: dict[str, float]) -> None:
        # Clear prior entries for this question
        for pid in self.scores:
            self.scores[pid].pop(question_id, None)

        # Store new entries — roster members only, skip None/blank
        for pid, value in scores.items():
            if pid not in self.roster:
                continue
            if value is None:
                continue
            if pid not in self.scores:
                self.scores[pid] = {}
            self.scores[pid][question_id] = float(value)

        self.closed_questions.add(question_id)

    # ------------------------------------------------------------------
    # Derived state for broadcasts
    # ------------------------------------------------------------------

    def get_lobby_players(self) -> list[dict]:
        return [
            {"player_id": pid, "name": p.name}
            for pid, p in sorted(self.players.items(), key=lambda x: x[1].joined_at)
        ]

    def get_queue_payload(self) -> dict:
        first_at = self.queue[0].received_at if self.queue else None
        return {
            "queue": [
                {
                    "player_id": e.player_id,
                    "name": self.players[e.player_id].name,
                    "delta_ms": round((e.received_at - first_at) * 1000) if first_at is not None else 0,
                }
                for e in self.queue
                if e.player_id in self.players
            ],
            "locked": self.queue_locked,
        }

    def _cell_state(self, question_id: str) -> dict:
        q = self._all_questions[question_id]
        if question_id not in self.closed_questions:
            return {"state": "unplayed", "value": q.value, "entries": []}

        entries = [
            {
                "player_id": pid,
                "name": self.players[pid].name,
                "value": self.scores[pid][question_id],
            }
            for pid in self.roster
            if pid in self.scores and question_id in self.scores[pid]
        ]

        if entries:
            return {"state": "awarded", "value": q.value, "entries": entries}
        return {"state": "passed", "value": q.value, "entries": []}

    def get_scores_payload(self) -> dict:
        # Grid: board → category → str(value) → cell_state
        grid: dict[str, dict[str, dict[str, dict]]] = {}
        for board, questions in self.questions.items():
            grid[board] = {}
            for q in questions:
                grid[board].setdefault(q.category, {})
                grid[board][q.category][str(q.value)] = self._cell_state(q.id)

        # Per-board totals, sorted by board_total descending
        per_board_totals: dict[str, list[dict]] = {}
        for board, questions in self.questions.items():
            board_qids = {q.id for q in questions}
            rows = []
            for pid in self.roster:
                player_scores = self.scores.get(pid, {})
                board_total = sum(
                    v for qid, v in player_scores.items() if qid in board_qids
                )
                cumulative = sum(player_scores.values())
                rows.append(
                    {
                        "player_id": pid,
                        "name": self.players[pid].name,
                        "board_total": board_total,
                        "cumulative": cumulative,
                    }
                )
            rows.sort(key=lambda r: r["board_total"], reverse=True)
            per_board_totals[board] = rows

        roster_players = [
            {"player_id": pid, "name": self.players[pid].name} for pid in self.roster
        ]

        return {
            "grid": grid,
            "per_board_totals": per_board_totals,
            "closed": list(self.closed_questions),
            "roster": roster_players,
            "boards": self._boards,
        }

    def get_full_state(self) -> dict:
        return {
            "phase": self.phase,
            "join_code": self.join_code,
            "lobby_players": self.get_lobby_players(),
            "queue": self.get_queue_payload(),
            "scores": self.get_scores_payload(),
        }
