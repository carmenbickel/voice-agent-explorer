# Voice Agent Explorer

A local learning and portfolio project that demonstrates how an AI
customer-support voice assistant works: conversation, knowledge retrieval,
business tools, deterministic rules, and observable outcomes.

**Principle: Use AI for ambiguity. Use deterministic software for certainty.**

The project is intended for developers learning these systems and reviewers
who want to see how a customer request passes through their architecture.

## Current status

Today the application is a **text-chat foundation**. It explains voice-AI
concepts through a browser UI, FastAPI, and a local Ollama model. It supports
follow-up questions, a thinking indicator, error messages, and retry feedback.

It currently has **one shared in-memory conversation** per backend process.
Reloading the page clears the visible transcript, not the agent's history.
Restarting the backend clears that history. Run locally with one worker.

Voice, isolated sessions, shop workflows, document RAG, a knowledge graph,
business storage, and an architecture panel are **planned, not implemented**.
The existing assistant has not yet been changed into a shopping assistant.

## Planned customer-support demo

The next phase uses an online shop to demonstrate four journeys. The proposed
shop is **Stepwise Shoes**, a fictional footwear retailer; the specific shop
choice still needs confirmation.

| Journey | Example | Planned result |
| --- | --- | --- |
| Buy | “Find waterproof hiking shoes in size 39 under €150.” | Grounded product advice, current stock lookup, and a confirmed simulated purchase |
| Return | “These shoes do not fit. I want to return them.” | Eligibility check and confirmed return request |
| Exchange | “Can I exchange size 39 for size 40?” | Replacement-stock check and confirmed exchange request |
| Cancel | “Please cancel my order.” | Ownership/fulfillment checks and cancellation when permitted |

The initial proposal uses six products, limited variants, fictional customers
and orders, and one currency/shipping region. Payments, shipping, refunds, and
human support are simulated; no real merchant integrations are planned for the
first version.

### Why RAG and a knowledge graph?

- **Document RAG** retrieves passages from product guides, FAQs, and policies
  so the assistant can explain its answers with sources.
- **A wiki/knowledge graph** links products, features, size guides, and policies
  to help retrieve the right evidence for the right product.
- **Business tools and Python rules** use current data to determine stock,
  ownership, eligibility, and actual transaction results.

The architecture panel will show the retrieval sources, graph paths, tool
results, policy outcomes, and timings involved in a turn. A fixed evaluation
set will compare document-only RAG with graph-assisted RAG.

## Architecture and roadmap

Read **[ARCHITECTURE.md](ARCHITECTURE.md)** for:

- Current and target architecture diagrams, rendered with Mermaid on GitHub.
- A layered SVG diagram with horizontal application tiers and vertical
  communication, LLMOps/operations, and security pillars; a team responsibility
  matrix; and a separate knowledge/AI improvement lifecycle.
- Sequence diagrams for all four customer journeys.
- RAG ingestion, graph relationships, and retrieval flow.
- Proposed data entities, API/tool contracts, confirmations, and policy rules.
- Voice, identity, handover, security, observability, and demo limitations.
- Ordered implementation milestones and testable acceptance criteria.

The roadmap proceeds through session isolation → browser voice → tracing →
catalog/wiki foundations → document RAG → graph-assisted RAG → confirmed
purchases → cancellations → returns → exchanges → handover → portfolio
evaluation. Detailed dependencies are in the architecture document. Future
features should be implemented through separately scoped GitHub issues.

Shared support components will be separated from shop-specific knowledge,
policies, and tools so a later business scenario can reuse the design.

## Current stack

- Python 3.10+ recommended
- FastAPI and Uvicorn
- HTTPX for the Ollama API
- Ollama with `llama3.2:3b`
- Plain HTML, CSS, and JavaScript; no frontend build step

The target additionally proposes SQLite, local embeddings/vector retrieval,
and a curated JSON graph. They are not required to run the current application.

## Run locally

Prerequisites: Python, an installed [Ollama](https://ollama.com/), and enough
local resources to run the selected model. Ensure Ollama is running: the desktop
application can manage its server, or start `ollama serve` in a separate terminal
if it is not already running.

Run these commands from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
ollama pull llama3.2:3b
```

Start the application in the activated environment:

```bash
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. Enter a question and click **Send** or press
**Enter**. FastAPI serves both the frontend and API. If a request fails, the
question is restored for retry and its previous message is labelled
“No response received.”

### Configuration

Set environment variables before starting Uvicorn to override the defaults:

| Variable | Default |
| --- | --- |
| `OLLAMA_BASE_URL` | `http://localhost:11434` |
| `OLLAMA_MODEL` | `llama3.2:3b` |

If selecting another model, pull it into Ollama first. The embedding model
mentioned in the target architecture is not used by the current application.

### Current API

- `GET /health` → `{"status": "ok"}`
- `POST /chat` accepts `{"message": "What is VAD?"}` and returns
  `{"response": "..."}`.
- Ask `{"message": "Why do we need it?"}` next to use the same shared history.
- FastAPI's interactive API documentation is at http://127.0.0.1:8000/docs.

Chat delegates to `ConversationAgent`, which sends its system prompt and
history to Ollama. Ollama failures return an HTTP 503 response. These are the
current contracts; session-aware APIs in the design are future changes.

## Checks

With the virtual environment activated:

```bash
python -m unittest discover -s tests -v
git diff --check
```

The automated tests mock Ollama, so a running model is not required for them.
They cover conversation context, failed-turn behavior, API validation/errors,
and serving the frontend page/assets. For a manual browser check, exchange two
messages, verify the thinking state and response order, and check retry behavior
when the model service is unavailable.

## Project structure

```text
backend/           FastAPI, conversation agent, Ollama client, API models
frontend/          Browser HTML, JavaScript, and CSS
knowledge/         Reserved for knowledge content; retrieval is not built yet
tests/             Current automated API/agent checks
ARCHITECTURE.md    Target design, Mermaid diagrams, and implementation milestones
docs/diagrams/     Editable SVG architecture illustrations
requirements.txt  Current Python dependencies
```
