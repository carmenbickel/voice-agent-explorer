import os

import httpx


OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://localhost:11434",
)

OLLAMA_MODEL = os.getenv(
    "OLLAMA_MODEL",
    "llama3.2:3b",
)


class OllamaError(Exception):
    """Raised when communication with Ollama fails."""


def generate_response(messages: list[dict[str, str]]) -> str:
    try:
        response = httpx.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
            },
            timeout=60.0,
        )

        response.raise_for_status()

        data = response.json()
        content = data["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Ollama response content must be text.")
        return content

    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise OllamaError(
            "Unable to get a response from Ollama."
        ) from exc
