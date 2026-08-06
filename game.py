import secrets
import string
import time
from dataclasses import dataclass, field
from typing import Literal

from bundle_loader import BundleQuestion


def _generate_join_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(4))


@dataclass
class Player:
    id: str
    name: str
    connected: bool
    joined_at: float
    virtual: bool = False  # True for host-added scorecard entries; never buzzes, never shown to players
    # Long-lived, non-rotating — lets a real player's browser silently resume
    # this same identity after any reconnect (reload, screen-lock, etc.) for
    # as long as the room exists. Virtual entries get one too (harmless,
    # simpler than conditionally generating it in two constructors) but
    # never hand it to a client since they have no socket.
    rejoin_token: str = field(default_factory=lambda: secrets.token_urlsafe(24))


@dataclass
class BuzzEntry:
    player_id: str
    received_at: float


class Game:
    def __init__(self, questions: dict[str, list[BundleQuestion]] | None = None):
        self.questions: dict[str, list[BundleQuestion]] = {}
        self._boards: list[str] = []
        self._all_questions: dict[str, BundleQuestion] = {}
        self.load_questions(questions or {})

        self.phase: Literal["lobby", "live"] = "lobby"
        self.join_code: str = _generate_join_code()
        self.players: dict[str, Player] = {}
        self.roster: list[str] = []
        self.queue: list[BuzzEntry] = []
        self.queue_locked: bool = False
        self.scores: dict[str, dict[str, float]] = {}
        self.closed_questions: set[str] = set()
        self.live_question: dict | None = None
        self.current_board_index: int = 0
        self.media_dir: str | None = None

    def load_questions(self, questions: dict[str, list[BundleQuestion]]) -> None:
        self.questions = questions
        self._boards = list(questions.keys())
        self._all_questions = {q.id: q for qs in questions.values() for q in qs}

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

    def remove_player(self, player_id: str) -> None:
        if self.phase != "lobby":
            raise ValueError("Cannot remove a player after the quiz has started.")
        if player_id not in self.players:
            raise ValueError("Unknown player.")
        del self.players[player_id]

    def player_rejoin(self, token: str) -> tuple[str, str] | None:
        if not token:
            return None
        for pid, p in self.players.items():
            if p.virtual or p.rejoin_token != token:
                continue
            p.connected = True
            return pid, self.phase
        return None

    def start_quiz(self) -> list[str]:
        if self.phase == "live":
            return self.roster
        if not self.questions:
            raise ValueError("Cannot start: no quiz content uploaded.")
        self.phase = "live"
        self.roster = sorted(
            [pid for pid, p in self.players.items() if not p.virtual],
            key=lambda pid: self.players[pid].joined_at,
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
    # Reveal / answer / cancel (SPEC V3.md §4)
    # ------------------------------------------------------------------

    def question_reveal(self, question_id: str) -> None:
        if question_id not in self._all_questions:
            raise ValueError("Unknown question.")
        if self.live_question and self.live_question["question_id"] != question_id:
            raise ValueError("Another question is already live.")
        reviewing = question_id in self.closed_questions
        self.live_question = {
            "question_id": question_id,
            "status": "answer_shown" if reviewing else "revealed",
            "reviewing": reviewing,
        }

    def answer_reveal(self) -> None:
        if not self.live_question:
            raise ValueError("No question is currently revealed.")
        if self.live_question["status"] != "revealed":
            raise ValueError("Answer can only be revealed from an active question reveal.")
        self.live_question["status"] = "answer_shown"

    def question_cancel(self) -> None:
        if not self.live_question:
            raise ValueError("No question is currently live.")
        self.live_question = None
        self.queue_reset()

    def select_board(self, index: int) -> None:
        if self.live_question:
            raise ValueError("Cannot change boards while a question is live.")
        if not (0 <= index < len(self._boards)):
            raise ValueError("Unknown board.")
        self.current_board_index = index

    def get_live_question_payload(self) -> dict:
        if not self.live_question:
            return {"live_question": None}
        q = self._all_questions[self.live_question["question_id"]]
        return {
            "live_question": {
                "question_id": q.id,
                "board": q.board,
                "category": q.category,
                "value": q.value,
                "question": q.question,
                "answer": q.answer,
                "question_media": q.question_media,
                "answer_media": q.answer_media,
                "status": self.live_question["status"],
                "reviewing": self.live_question["reviewing"],
            }
        }

    def get_presentation_payload(self) -> dict:
        # Redacted for the shared/screen-shared presentation room: no
        # question/answer/media per board cell (only the live_question
        # entry, gated on phase, carries those) — see SPEC V3.md §1.
        if self.live_question:
            board_name = self._all_questions[self.live_question["question_id"]].board
        elif self._boards:
            board_name = self._boards[self.current_board_index]
        else:
            board_name = None

        board_grid: dict[str, dict[str, dict]] = {}
        totals: list[dict] = []
        if board_name and board_name in self.questions:
            for q in self.questions[board_name]:
                cell = self._cell_state(q.id)
                board_grid.setdefault(q.category, {})
                board_grid[q.category][str(q.value)] = {
                    "value": cell["value"],
                    "state": cell["state"],
                    "entries": cell["entries"],
                    "negative_only": cell.get("negative_only", False),
                }
            totals = self._board_totals(board_name)

        live = None
        if self.live_question:
            q = self._all_questions[self.live_question["question_id"]]
            status = self.live_question["status"]
            live = {
                "question_id": q.id,
                "question": q.question,
                "question_media": q.question_media,
                "status": status,
                "reviewing": self.live_question["reviewing"],
            }
            if status == "answer_shown":
                live["answer"] = q.answer
                live["answer_media"] = q.answer_media

        return {
            "board_name": board_name,
            "board_index": self._boards.index(board_name) if board_name in self._boards else 0,
            "board_count": len(self._boards),
            "board": board_grid,
            "totals": totals,
            "live_question": live,
        }

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def roster_add(self, name: str) -> str:
        name = name.strip()
        if not name:
            raise ValueError("Name cannot be empty.")
        player_id = secrets.token_urlsafe(8)
        self.players[player_id] = Player(
            id=player_id, name=name, connected=True, joined_at=time.monotonic(), virtual=True
        )
        self.roster.append(player_id)
        return player_id

    def remove_from_roster(self, player_id: str) -> None:
        if self.phase != "live":
            raise ValueError("No roster to remove from before Start.")
        if player_id not in self.roster:
            raise ValueError("Unknown roster member.")
        self.roster.remove(player_id)
        self.scores.pop(player_id, None)
        self.players.pop(player_id, None)

    def question_exists(self, question_id: str) -> bool:
        return question_id in self._all_questions

    def question_submit(self, question_id: str, scores: dict[str, float]) -> None:
        if not (
            self.live_question
            and self.live_question["question_id"] == question_id
            and self.live_question["status"] == "answer_shown"
        ):
            raise ValueError("Question must be revealed and its answer shown before scoring.")

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
        self.live_question = None  # always true here — the gate above already confirmed the match
        self.queue_reset()

    # ------------------------------------------------------------------
    # Derived state for broadcasts
    # ------------------------------------------------------------------

    def get_lobby_players(self) -> list[dict]:
        return [
            {"player_id": pid, "name": p.name}
            for pid, p in sorted(self.players.items(), key=lambda x: x[1].joined_at)
        ]

    def get_active_players(self) -> list[dict]:
        return [
            {"player_id": pid, "name": p.name}
            for pid, p in sorted(self.players.items(), key=lambda x: x[1].joined_at)
            if p.connected and not p.virtual
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
        # Host-only payload (state:scores never reaches a player socket) —
        # question/answer/media are safe here per SPEC V3.md §1's widened
        # invariant, and let the control center show a read-only Q&A peek
        # before Start (SPEC V3.md §2).
        base = {
            "value": q.value,
            "question": q.question,
            "answer": q.answer,
            "question_media": q.question_media,
            "answer_media": q.answer_media,
        }

        if question_id not in self.closed_questions:
            return {**base, "state": "unplayed", "entries": []}

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
            # Red only when nobody got it: a genuine negative (penalty) entry
            # exists and no positive entry does. An explicit 0 counts as
            # neither positive nor negative (SPEC V8.md) — a cell that's all
            # zeros stays green/neutral rather than reading as a miss.
            values = [e["value"] for e in entries]
            negative_only = any(v < 0 for v in values) and not any(v > 0 for v in values)
            return {**base, "state": "awarded", "entries": entries, "negative_only": negative_only}
        return {**base, "state": "passed", "entries": []}

    def _board_totals(self, board: str) -> list[dict]:
        board_qids = {q.id for q in self.questions.get(board, [])}
        rows = []
        for pid in self.roster:
            player_scores = self.scores.get(pid, {})
            rows.append(
                {
                    "player_id": pid,
                    "name": self.players[pid].name,
                    "board_total": sum(v for qid, v in player_scores.items() if qid in board_qids),
                    "cumulative": sum(player_scores.values()),
                }
            )
        rows.sort(key=lambda r: r["board_total"], reverse=True)
        return rows

    def get_scores_payload(self) -> dict:
        # Grid: board → category → str(value) → cell_state
        grid: dict[str, dict[str, dict[str, dict]]] = {}
        for board, questions in self.questions.items():
            grid[board] = {}
            for q in questions:
                grid[board].setdefault(q.category, {})
                grid[board][q.category][str(q.value)] = self._cell_state(q.id)

        # Per-board totals, sorted by board_total descending
        per_board_totals: dict[str, list[dict]] = {
            board: self._board_totals(board) for board in self.questions
        }

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
            **self.get_live_question_payload(),
        }
