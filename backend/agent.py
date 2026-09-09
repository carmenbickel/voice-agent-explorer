from threading import Lock

from backend.ollama_client import generate_response


SYSTEM_PROMPT = (
    "You are an assistant specialized in explaining AI voice bots. "
    "Give clear, practical answers. Ask for clarification when required. "
    "Use the conversation history to understand follow-up questions."
)


class ConversationAgent:
    """Keep one in-memory conversation for the lifetime of this agent."""

    def __init__(self):
        self._messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._lock = Lock()

    def chat(self, message: str) -> str:
        # FastAPI can handle simultaneous requests in different threads.
        with self._lock:
            messages = self._messages + [{"role": "user", "content": message}]
            response = generate_response(messages)
            # Failed requests must not leave an unanswered turn in history.
            self._messages = messages + [{"role": "assistant", "content": response}]
            return response
