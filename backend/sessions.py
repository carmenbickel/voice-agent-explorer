"""Server-owned demo sessions: opaque cookies, per-session state, expiry."""

import os
import secrets
import time
from threading import Lock


SESSION_COOKIE = "session_id"
DEFAULT_IDLE_TIMEOUT_SECONDS = 30 * 60

DEMO_CUSTOMERS = {
    "demo_maya": {"name": "Maya"},
    "demo_leo": {"name": "Leo"},
}


class DemoCustomerNotFoundError(LookupError):
    """The requested identifier is not an available seeded demo customer."""


class Session:
    """One isolated conversation owned by the server, keyed by an opaque ID."""

    def __init__(self, session_id: str, clock=time.time):
        self.id = session_id
        self.customer_id = None
        # History holds user/assistant turns; the system prompt is added per turn.
        self.history = []
        self.pending_proposal = None
        self.clock = clock
        self.last_activity = clock()
        self.lock = Lock()

    def touch(self) -> None:
        self.last_activity = self.clock()

    def clear_conversation(self) -> None:
        """Clear history and pending action state; keep the customer binding."""
        self.history = []
        self.pending_proposal = None


class SessionManager:
    """Create, resolve, and expire sessions; expires state is never handed out."""

    def __init__(self, idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
                 clock=time.time):
        self.idle_timeout_seconds = idle_timeout_seconds
        self.clock = clock
        self._sessions = {}
        self._lock = Lock()

    def create(self) -> Session:
        session = Session(secrets.token_urlsafe(32), clock=self.clock)
        self._sweep()
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str):
        """Return the live session for this opaque ID, or None if absent/expired."""
        with self._lock:
            self._expire(session_id)
            session = self._sessions.get(session_id)
            if session is not None and (
                session.last_activity + self.idle_timeout_seconds < self.clock()
            ):
                del self._sessions[session_id]
                return None
            if session is not None:
                session.touch()
            return session

    def reset(self, session: Session) -> None:
        with session.lock:
            session.clear_conversation()

    def switch_customer(self, session: Session, customer_id: str) -> dict:
        """Bind a seeded demo customer, clearing context and pending state."""
        profile = DEMO_CUSTOMERS.get(customer_id)
        if profile is None:
            raise DemoCustomerNotFoundError(customer_id)
        with session.lock:
            session.clear_conversation()
            session.customer_id = customer_id
            session.touch()
        return {"id": customer_id, "name": profile["name"]}

    def _sweep(self) -> None:
        for session_id, session in list(self._sessions.items()):
            self._expire(session_id)

    def _expire(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None and (
            session.last_activity + self.idle_timeout_seconds < self.clock()
        ):
            del self._sessions[session_id]


def idle_timeout_from_env() -> float:
    raw = os.environ.get("SESSION_IDLE_TIMEOUT_SECONDS")
    if raw is None:
        return DEFAULT_IDLE_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_IDLE_TIMEOUT_SECONDS
    if value <= 0:
        return DEFAULT_IDLE_TIMEOUT_SECONDS
    return value
