from backend.ollama_client import generate_response
from backend.sessions import Session


SYSTEM_PROMPT = (
    "You are the FUN SHOES footwear shop assistant. Help with our shoes, shopping, and order support. "
    "Give clear, practical answers. Ask for clarification when required. "
    "Use the conversation history to understand follow-up questions. Only use FUN SHOES evidence for store policies. Never apply other retailers’ policies or invent order IDs, stock, prices, or successful actions. If evidence is missing, say you do not have that information."
)


def run_chat_turn(session: Session, message: str, evidence_text: str = None) -> str:
    """Run one serialized turn for this session and record it on success.

    An optional evidence block grounds the answer in knowledge articles with
    citation instructions (issue C1)."""
    # Turns of one session are serialized; independent sessions progress in
    # parallel because each session owns its own lock.
    with session.lock:
        session.touch()
        messages = (
            [{"role": "system", "content": SYSTEM_PROMPT}]
            + session.history
            + [{"role": "user", "content": message}]
        )
        if evidence_text:
            # Evidence must sit directly with the current question; earlier
            # history stays untouched to avoid mixing customers' context.
            messages.append({"role": "system", "content": evidence_text})
        response = generate_response(messages)
        # Failed requests must not leave an unanswered turn in history.
        session.history = (
            session.history
            + [{"role": "user", "content": message},
               {"role": "assistant", "content": response}]
        )
        return response

