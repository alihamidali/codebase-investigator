import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools import CodebaseTools


@dataclass
class Turn:
    question: str
    answer: str
    audit: dict[str, Any]


@dataclass
class Session:
    id: str
    repo_url: str
    repo_name: str
    repo_path: Path
    tools: CodebaseTools
    turns: list[Turn] = field(default_factory=list)
    claims_log: list[dict[str, Any]] = field(default_factory=list)

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    def get_investigator_messages(self, question: str) -> list[dict]:
        """Build message list for investigator — clean Q&A pairs from prior turns."""
        msgs: list[dict] = []
        for turn in self.turns:
            msgs.append({"role": "user", "content": turn.question})
            msgs.append({"role": "assistant", "content": turn.answer})
        msgs.append({"role": "user", "content": question})
        return msgs

    def get_claims_summary(self) -> str:
        if not self.claims_log:
            return "No prior claims established yet."
        lines = []
        for c in self.claims_log:
            turn = c.get("turn", "?")
            confidence = c.get("confidence", "?").upper()
            citation = c.get("citation") or "uncited"
            lines.append(f"[Turn {turn}, {confidence}] {c['claim']} — {citation}")
        return "\n".join(lines)

    def add_turn(self, question: str, answer: str, audit: dict[str, Any]) -> None:
        turn_num = self.turn_count + 1
        for claim in audit.get("new_claims", []):
            claim["turn"] = turn_num
            self.claims_log.append(claim)
        self.turns.append(Turn(question=question, answer=answer, audit=audit))


_sessions: dict[str, Session] = {}


def create_session(repo_url: str, repo_name: str, repo_path: Path) -> Session:
    session = Session(
        id=str(uuid.uuid4()),
        repo_url=repo_url,
        repo_name=repo_name,
        repo_path=repo_path,
        tools=CodebaseTools(repo_path),
    )
    _sessions[session.id] = session
    return session


def get_session(session_id: str) -> Session | None:
    return _sessions.get(session_id)
