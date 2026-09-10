from backend.ollama_client import generate_response
from backend.sessions import Session


SYSTEM_PROMPT = (
    "You are an assistant specialized in explaining AI voice bots. "
    "Give clear, practical answers. Ask for clarification when required. "
    "Use the conversation history to understand follow-up questions."
)


def run_chat_turn(session: Session, message: str) -> str:
    """Run one serialized turn for this session and record it on success."""
    # Turns of one session are serialized; independent sessions progress in
    # parallel because each session owns its own lock.
    with session.lock:
        session.touch()
        messages = (
            [{"role": "system", "content": SYSTEM_PROMPT}]
            + session.history
            + [{"role": "user", "content": message}]
        )
        response = generate_response(messages)
        # Failed requests must not leave an unanswered turn in history.
        session.history = (
            session.history
            + [{"role": "user", "content": message},
               {"role": "assistant", "content": response}]
        )
        return response
