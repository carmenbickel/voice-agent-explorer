# Voice Agent Explorer

A local learning and portfolio project that demonstrates how an AI
customer-support voice assistant works: conversation, knowledge retrieval,
business tools, deterministic rules, and observable outcomes.

**Principle: Use AI for ambiguity. Use deterministic software for certainty.**

The project is intended for developers learning these systems and reviewers
who want to see how a customer request passes through their architecture.

## Current status

Today the application is a **text-chat foundation with browser voice turns**.
It explains voice-AI concepts through a browser UI, FastAPI, and a local
Ollama model. It supports follow-up questions, a thinking indicator, error
messages, and retry feedback.

**Voice (issues B1):** the **Listen** control opens the browser microphone,
submits the recognized utterance to the session, and speaks the response
aloud. The status line shows **idle, listening, transcribing, thinking, and
speaking** states. Browsers without speech APIs, or denied microphone access,
keep full text chat and display an actionable status. A **Stop** control
cancels playback or listening; a newer turn cancels prior playback so late
responses never speak over the newer turn.

It currently uses **isolated server-side sessions**: each browser gets an
opaque HTTP-only session cookie (30-minute idle expiry, configurable via
`SESSION_IDLE_TIMEOUT_SECONDS`), its own conversation history, and an optional
binding to one seeded demo customer via the local demo selector. Switching the
demo customer or resetting the session clears conversation context and pending
action state only; restarting the backend clears all session state. Each chat
turn exposes a sanitized trace (stages, statuses, durations) in a panel below
the response, session-scoped via `GET /traces/{trace_id}`. Voice, shop
workflows, document RAG, a knowledge graph, and business storage are
**planned, not implemented — see the roadmap issues**. The existing assistant
has not yet been changed into a shopping assistant.

## Planned customer-support demo

The next phase uses an online shop to demonstrate four journeys. The demo shop
is **Stepwise Shoes**, a fictional footwear retailer. Shop fixtures (see below)
are draft demo data for the local demonstration; they contain no real shop or
customer information.

| Journey | Example | Planned result |
| --- | --- | --- |
| Buy | “Find waterproof hiking shoes in size 39 under €150.” | Grounded product advice, current stock lookup, and a confirmed simulated purchase |
| Return | “These shoes do not fit. I want to return them.” | Eligibility check and confirmed return request |
| Exchange | “Can I exchange size 39 for size 40?” | Replacement-stock check and confirmed exchange request |
| Cancel | “Please cancel my order.” | Ownership/fulfillment checks and cancellation when permitted |

The initial fixtures use six products, limited variants, two synthetic demo
customers, and seeded orders in the four fulfillment states. Payments,
shipping, refunds, and human support are simulated; no real merchant
integrations are planned for the first version.

## Wiki and retrieval (issues C1)

`backend/rag.py` ingests the Stepwise Shoes wiki (12 draft articles in
`knowledge/articles/` with validated front matter — id, version, language,
scope, effective dates) into heading-aware chunks with stable IDs and hashes.
Retrieval is bounded (max 4 chunks per question) and deterministic: the local
hashing embedder scores how much of the question's content words an article
chunk covers; unknown topics (e.g. wedding dresses) retrieve nothing instead
of weak evidence. Embedding model selection: `OLLAMA_EMBED_MODEL` can point to
a neural embedder on an embedding-capable Ollama server; the default is the
fully local, reproducible hashing embedder.

Chat behavior: questions that retrieve evidence ground the answer in the
retrieved passages, the model is instructed to cite `[src:<chunk_id>]`, and
answers may only cite retrieved passages (fabricated citation tags are
removed). Insufficient evidence must produce a clarifying question or an
explicit limitation — the model may never invent stock, prices, ownership, or
eligibility, which belong to the catalog/workflow issues. A fixed baseline
evaluation set (`backend/rag_eval.py`; 12 questions) is committed with a
repeatable report in `knowledge/eval/`; regenerate with
`python -m backend.rag_eval` (needs no Ollama).

## Commerce workflows (issue D1)

Chat answers may end with a typed `ACTION {…}` line proposing an allowlisted
tool (purchase proposals only in this issue). Argument schemas are validated;
unknown tools, malformed actions, and repeated actions are rejected without
harm. When a purchase applies, the response carries an `action_proposal`
showing exact items, quantities, total, and proposal ID — **nothing is
written** until you press Confirm purchase, which calls
`POST /actions/{proposal_id}/confirm`. That endpoint rechecks the quote
(current SQLite price), ownership (server session), quantity, and stock
inside one transaction, reserves inventory, and creates the processing order;
forgetting nothing, repeated confirmations resolve by operation ID without
duplicating the order (`GET /operations/{operation_id}` for
timeout/unknown outcomes). Expired or price-changed proposals require a new
confirmation. Anonymous sessions cannot buy. Concurrent buyers cannot
over-reserve the last available variant.

## Demo shop fixtures (SQLite)

`backend/shop.py` owns the Stepwise Shoes demo data. Business records live in a
local SQLite database (default `shop.db` in the repository root, path
override via the `SHOP_DB` environment variable; the file is gitignored):

```bash
python -m backend.shop seed      # seed OR reset: wipes and restores canonical data
```

**Reset behavior:** seeding deletes *all* business records (orders, order
lines, carts, cart lines) including inventory reservations, then re-inserts
the canonical fixtures — six products with 18 variants, inventory with explicit
`reserved` quantities (available = on-hand − reserved, never negative), two
demo customers, and eight orders covering processing, shipped, delivered, and
cancelled states. Order snapshots (item name, size, colour, unit price),
customer ownership, fulfillment state, delivery time, and the policy version
(`policy-2026-06-v1`) are stored with fixed timestamps, so repeated
initializations produce identical data. Conversation sessions and
assistant history are not touched by a shop reset.

Seeded products (prices in integer EUR cents, draft demo data, Subject to
review):

| ID | Product | Category | Sizes (example colour) | Price |
| --- | --- | --- | --- | --- |
| `prod_summit` | Summit Trail | trail-running | 38–40 (olive/coral) | €129.00 |
| `prod_fjell` | Fjell Trek | hiking | 38–40 (brown/grey) | €159.00 |
| `prod_storm` | Storm Step GTX | hiking | 36/38/40 (black/anthracite) | €189.00 |
| `prod_city` | City Walk | sneakers | 37/39/41 (white/navy) | €99.00 |
| `prod_swift` | Swift Run | running | 38/39/41 (blue/yellow) | €119.00 |
| `prod_sunny` | Sunny Slide | sandals | 36/39/41 (beige/sand/black) | €59.00 |

Demo customers reuse the session demo identity IDs `demo_maya` and `demo_leo`,
each owning four seeded orders (one per fulfillment state). Catalog reads
(`Catalog.list_products`, `Catalog.get_variant`, `Catalog.list_customer_orders`)
return stable IDs, current price, size, colour, and stock from SQLite — never
prompt text. Reservation of stock (on the purchase path) and shop-specific
chat behavior are future issues.

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
| `SESSION_IDLE_TIMEOUT_SECONDS` | `1800` (30 minutes) |
| `SHOP_DB` | `shop.db` in the repository root |

If selecting another model, pull it into Ollama first. The embedding model
mentioned in the target architecture is not used by the current application.

### Current API

Sessions are identified by an opaque, HTTP-only `session_id` cookie issued by
the server. Requests without an active session receive HTTP 403.

- `GET /health` → `{"status": "ok"}`
- `POST /sessions` creates a session and sets the cookie. Send
  `{"customer_id": "..."}` to bind a seeded demo customer (see
  `GET /sessions/customers`), or no body for an anonymous session.
- `POST /sessions/customer` switches this session's demo customer and clears
  its conversation context and pending state.
- `POST /sessions/reset` clears this session's history and pending state only;
  it keeps the customer binding.
- `POST /chat` accepts `{"message": "What is VAD?"}` and returns
  `{"response": "...", "turn_id": "...", "trace_id": "..."}` using this
  session's own history.
- `GET /traces/{trace_id}` returns the sanitized per-turn event trace (stages,
  statuses, durations, skipped stages). Access is authorized to the session
  that owns the trace only; other sessions receive a safe HTTP 404. Traces are
  kept for 24 hours with a size cap and contain no prompts or user content.
- FastAPI's interactive API documentation is at http://127.0.0.1:8000/docs.

Chat delegates each turn to the session's history when calling Ollama; turns of
one session are serialized while different sessions progress independently.
Ollama failures return an HTTP 503 response. Seeded demo customers are
simulated identity for the local demo only, not authentication.

## Checks

With the virtual environment activated:

```bash
python -m unittest discover -s tests -v
git diff --check
```

The automated tests mock Ollama, so a running model is not required for them.
They cover conversation context, failed-turn behavior, API validation/errors,
session isolation, shop fixtures/catalog reads, voice-logic decisions (state
machine, stale-turn protection, and fallback via `node
tests/voice_logic_test.js`), and serving the frontend page/assets. For a
manual browser check, exchange two messages, verify the thinking state and
response order, use the Listen control to speak a question and hear the
response, use Stop to cancel playback, and check retry behaviour when the
model service is unavailable.

## Project structure

```text
backend/           FastAPI, conversation agent, Ollama client, sessions, shop fixtures
frontend/          Browser HTML, JavaScript, voice logic, and CSS
knowledge/         Reserved for knowledge content; retrieval is not built yet
tests/             Current automated API/agent/shop/voice checks
ARCHITECTURE.md    Target design, Mermaid diagrams, and implementation milestones
docs/diagrams/     Editable SVG architecture illustrations
requirements.txt  Current Python dependencies
```
