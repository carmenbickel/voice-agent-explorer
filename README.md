# Voice Agent Explorer

A small practical project for learning and demonstrating the architecture of an AI voice bot.

The application will allow a user to speak with an AI assistant about topics such as:

- Speech-to-Text
- Text-to-Speech
- Voice Activity Detection
- Conversational agents
- Tool/API calling
- Conversation memory
- Latency
- Human handoff

The first version is intentionally simple and runs locally.

## Planned stack

- Python
- FastAPI
- Ollama
- Browser Speech Recognition
- Browser Speech Synthesis
- HTML / CSS / JavaScript

## Project structure

```text
backend/
frontend/
knowledge/
ARCHITECTURE.md
requirements.txt
```

## Run the backend

Install `requirements.txt`, start Ollama with the `llama3.2:3b` model
available, and run `uvicorn backend.main:app --reload` from the repository root.
`OLLAMA_BASE_URL` and `OLLAMA_MODEL` can override the defaults.

Open http://127.0.0.1:8000 in your browser to use the chat interface. Enter a
question and click Send or press Enter. A thinking indicator appears while the
assistant responds. If a request fails, the page shows an error and restores
your question so you can retry. FastAPI serves the frontend and API together;
no frontend build step or separate server is needed.

Reloading the page clears the visible transcript, but the backend keeps its
conversation memory until restarted.

`POST /chat` accepts `{"message": "What is VAD?"}` and returns
`{"response": "..."}`. Send `{"message": "Why do we need it?"}` next to
ask a follow-up using the previous turn.

FastAPI delegates to `ConversationAgent`, which sends its system prompt and
conversation history to Ollama. This local version has one shared, in-memory
session per backend process. Run with one worker; restarting the backend clears
history. All callers of that process share the same conversation.

Run automated checks with `python -m unittest discover -s tests -v`.
