# Proposed Implementation Issues

This is a proposed breakdown of the work remaining after issue #11. It is a
planning document only. The issues below have not been created on GitHub, and
no application implementation is included here.

## Repository Baseline

The current application is a small, runnable text-chat foundation:

- `frontend/` contains plain HTML, CSS, and JavaScript chat UI.
- `backend/main.py` serves the UI and exposes `/health` and `POST /chat`.
- `backend/agent.py` maintains one locked, shared in-memory conversation.
- `backend/ollama_client.py` calls Ollama synchronously with a configured chat model.
- `tests/test_chat.py` covers current chat behavior, validation, failures, and static assets.
- `knowledge/` is empty apart from `.gitkeep`; there is no database, retrieval, graph, workflow, voice, or trace implementation.
- `README.md` and `ARCHITECTURE.md` describe the intended Stepwise Shoes demo and its 12 roadmap milestones.

The target remains a local fictional-shop demonstrator. Identity, payments,
orders, fulfillment, speech, support handover, and external integrations are
simulated. The shop name, language, currency, region, fixtures, and exact
policy terms must be approved before the corresponding feature issues are
implemented.

## Area 1: Sessions, Identity, and Demo Data

### Goal

Replace the process-wide shared conversation with isolated, server-owned demo
sessions and establish the SQLite catalog/order foundation needed by every
business workflow.

### Current State

All callers of one backend process share `ConversationAgent` history. There is
no session cookie, customer binding, persistence, catalog, inventory, order
data, reset endpoint, or ownership enforcement.

### Missing Functionality

- Opaque session cookies and per-session conversation state.
- Explicit local-only selection of a seeded demo customer.
- Session reset and inactive-session expiry.
- SQLite schema, migrations or initialization, deterministic seed/reset behavior.
- Six proposed products, limited variants, inventory, customers, and orders.
- Server-side ownership context for all private reads and future writes.
- Repository/service boundaries and UTC timestamps suitable for later transactions.

### Issues to Create

#### Issue A1: Add isolated sessions and demo identity

Implement server-issued opaque session state, per-session history, demo-customer
binding, customer switching, reset, expiry, and serialized turns per session.
Keep independent sessions able to progress concurrently. Never trust a customer
identifier supplied in user text or model output.

Dependencies: None.

Acceptance criteria:

- Two sessions have independent histories and cannot read or alter one another's context.
- A session receives an opaque cookie and can select only an available seeded demo customer through the local demo flow.
- Switching customers clears conversation context and pending action state without changing persisted business records.
- `POST /sessions/reset` clears the owning session's history and proposals only.
- Inactive sessions expire according to the documented timeout, and expired state is not returned to a later session.
- Private lookups use server-owned session identity; tests attempt cross-customer access and receive a safe non-disclosing result.
- Existing text chat behavior and error handling remain runnable and covered by tests.

#### Issue A2: Add SQLite shop fixtures and catalog services

Create the minimal SQLite-backed entities and deterministic seed/reset command
for Stepwise Shoes, including products, variants, inventory, customers, carts,
orders, and order lines. Keep prices in integer EUR cents and preserve order
price and policy snapshots.

Dependencies: A1. Shop and policy decisions must be approved first.

Acceptance criteria:

- A fresh initialization produces the same six products, supported variants, synthetic customers, and demo orders every time.
- Seed data includes processing, shipped, delivered, and cancelled order states.
- Catalog reads return stable product/variant IDs, current price, size, colour, and stock from SQLite rather than prompt text.
- Available inventory never becomes negative, and reserved quantities are represented explicitly.
- Orders retain immutable item/price snapshots, customer ownership, fulfillment state, delivery time, and policy version.
- Reset behavior is documented and tested for business records and inventory reservations.
- Repository tests cover valid reads, missing IDs, malformed quantities, and UTC timestamp handling.

## Area 2: Voice Experience and Observability

### Goal

Make the browser demonstrate text and voice turn handling while exposing real,
sanitized execution traces instead of simulated architecture animation.

### Current State

The UI supports text submission, a thinking status, errors, and retry. It has
no microphone, speech synthesis, turn IDs, cancellation of playback, trace
endpoint, or architecture panel.

### Missing Functionality

- Browser feature-detected speech recognition and speech synthesis.
- Listening, transcribing, thinking, speaking, and idle states.
- Stop-speaking and stale-turn protection.
- Trace IDs, correlation/turn IDs, stage status, durations, failures, and skipped stages.
- Session-scoped trace access, redaction, size limits, and retention cleanup.
- UI presentation of sources, policy/tool outcomes, and trace events.

### Issues to Create

#### Issue B1: Add browser voice turn loop

Add progressive-enhancement browser speech recognition and speech synthesis to
the existing UI. Text must remain usable when speech APIs are unsupported or
microphone permission is denied. Track turn IDs and prevent old responses from
starting speech over a newer turn.

Dependencies: A1 for session-aware turns; can be developed before other areas.

Acceptance criteria:

- In a supported browser, a user can start listening, submit a recognized utterance, receive a response, and hear it spoken.
- The UI visibly distinguishes idle, listening, transcribing, thinking, and speaking states.
- Unsupported speech APIs and microphone denial leave text chat fully usable and show an actionable status.
- A stop control cancels current speech without claiming to cancel a backend action.
- Starting a newer turn cancels prior playback, and a late response cannot speak over the newer turn.
- Recognition, synthesis, timeout, and API failures return the UI to a recoverable state.
- Browser/manual checks and automated logic tests cover fallback and stale-turn behavior.

#### Issue B2: Add structured traces and architecture panel

Emit sanitized per-turn events for request context, model calls, retrieval,
policy, tool results, response assembly, errors, and skipped stages. Add a
session-scoped trace endpoint and a UI panel that displays actual events and
durations.

Dependencies: A1; B1 is not required, but voice timing can be added later.

Acceptance criteria:

- Each turn has a trace ID, turn ID, stage statuses, durations, and final outcome.
- The panel distinguishes executed, skipped, failed, denied, and pending stages and does not imply that all stages ran.
- Trace retrieval is authorized to the owning session only; cross-session access is denied without data leakage.
- Traces exclude full prompts, private chain-of-thought, secrets, and unrelated customer data.
- Retrieval source IDs, graph paths, policy versions/reasons, tool/operation IDs, and model timing are shown when those stages exist.
- Retention and size limits are enforced and tested; trace failures do not break the customer response.

## Area 3: Knowledge and Grounded Answers

### Goal

Give the assistant a versioned, inspectable knowledge pipeline that answers
product and policy questions from cited evidence, then measure whether graph
assistance improves retrieval.

### Current State

There are no articles, metadata, chunks, embeddings, vector index, graph, or
retrieval calls. The LLM answers directly from its system prompt and chat
history.

### Missing Functionality

- Curated Markdown wiki articles with stable metadata and source IDs.
- Heading-aware chunking and versioned ingestion manifest.
- Local embedding generation and bounded cosine-similarity retrieval.
- Citation-safe grounded response assembly and abstention behavior.
- Validated JSON graph with provenance and bounded traversal.
- Document-only versus graph-assisted evaluation set and results.

### Issues to Create

#### Issue C1: Build the document wiki and RAG baseline

Add the initial product, sizing, care, FAQ, shipping, and policy articles;
validate metadata; build versioned chunks and a local embedding index; and
return only retrieved evidence with server-resolved citations.

Dependencies: A1 and A2 for product IDs and approved shop/policy fixtures; B2 for trace integration.

Acceptance criteria:

- The corpus contains approximately 10-15 curated articles with stable IDs, versions, language, scope, effective dates, and source paths.
- Ingestion is reproducible, records source hashes/chunking/embedder/index versions, and rejects duplicate IDs or invalid metadata.
- Retrieval is bounded, filters by language/scope/version, and returns at most the documented context limit.
- Answers cite only passages actually retrieved; invalid or fabricated citation IDs are rejected or removed.
- Unknown, conflicting, superseded, and insufficient evidence produce clarification or an explicit limitation rather than an invented answer.
- Business facts such as live stock, price, ownership, and eligibility are not supplied by RAG as authoritative facts.
- A fixed baseline evaluation set and repeatable result report are committed.

#### Issue C2: Add optional curated graph-assisted retrieval

Add validated graph nodes and edges for products, brands, categories, features,
guides, policies, and articles. Expand retrieval through bounded, provenance-
checked graph paths and compare it with the document-only baseline. This is an
optional enhancement to the document RAG baseline and does not block commerce
workflow implementation.

Dependencies: C1.

Acceptance criteria:

- Graph loading rejects duplicate nodes, unknown references, unsupported relations, and edges without article/version provenance.
- Customer identities and private orders are absent from the public graph.
- Traversal follows only allowlisted relationships, is limited to two hops and the documented candidate bound, and reports graph paths.
- Graph expansion adds article candidates without bypassing scope, language, or policy-version filters.
- Document-only and graph-assisted modes use the same corpus, models, answer budget, and evaluation questions for comparison.
- Results report evidence recall, scope/version correctness, citation support, abstention, latency, and expansion size, including cases where graph assistance does not improve quality.

## Area 4: Deterministic Commerce Workflows

### Goal

Turn the assistant into a safe shopping demonstrator where the model can
interpret intent and propose calls, but typed services, policies, transactions,
confirmation, and idempotency decide every state-changing result.

### Current State

The only agent behavior is explanatory chat. There is no dispatcher, typed tool
contract, proposal/confirmation lifecycle, SQLite transaction service, policy
engine, cart operation, or commerce action.

### Missing Functionality

- Allowlisted typed read/proposal tools and bounded model-tool loop.
- Purchase proposal and explicit confirmation endpoint.
- Atomic inventory reservation, quote validation, and idempotent operations.
- Cancellation, return, and exchange policy checks and persisted requests.
- Conflict, expiry, timeout, ownership, and unavailable-stock behavior.

### Issues to Create

#### Issue D1: Add typed orchestration, proposals, and confirmed purchase

Introduce the dispatcher, validated tool arguments, bounded orchestration, action
proposals, explicit confirmation API, operation records, and transactional
purchase commit. The LLM must never directly commit a write or provide identity.

Dependencies: A2 and C1. B2 is recommended for receiving the resulting events,
but is not blocking. C2 is optional and does not block this issue.

Acceptance criteria:

- Allowlisted tools accept typed arguments and server context; unknown tools, invalid schemas, excessive loops, and malformed model output are safely rejected.
- Product search uses current SQLite price/stock and asks for clarification when product or variant selection is ambiguous.
- A purchase proposal shows exact items, quantity, total, expiry, and proposal ID before any write.
- Only an explicit confirmation endpoint can commit a purchase, and it rechecks quote, ownership, quantity, and stock in one transaction.
- Accepted purchases create a processing order and reserve stock atomically.
- Expired or changed proposals require a new confirmation; repeated confirmation with the same operation ID does not duplicate the order.
- Concurrent buyers cannot over-reserve the last available variant; timeout/unknown outcomes can be resolved by operation ID without a new write.

#### Issue D2: Add cancellation workflow

Implement owned-order cancellation preparation and confirmed commit for
processing orders, including race handling and exact-once reservation release.

Dependencies: D1.

Acceptance criteria:

- Only an owned processing order can produce an eligible cancellation proposal.
- Shipped and delivered orders are denied with a safe explanation and no state change.
- Already-cancelled orders return the existing outcome without a duplicate operation.
- Confirmation rechecks ownership and fulfillment state transactionally.
- Successful cancellation marks the order cancelled and releases its reservation exactly once.
- Ownership, fulfillment-state races, idempotent retries, and unknown commit outcomes are covered by tests.

#### Issue D3: Add return workflow

Implement versioned return eligibility, preparation, confirmation, and persisted
return requests for one owned delivered order line and its full quantity.

Dependencies: D1 and C1.

Acceptance criteria:

- The service requires an owned delivered line, an accepted reason/condition, and the approved policy window.
- Boundary dates use an injectable UTC clock and the order's applicable policy version.
- A second active return or exchange for the same line is prevented.
- Commit revalidates eligibility and ownership in the same transaction as creating the return request.
- The response includes a stable return reference and clearly distinguishes a request from inspection, arrival, restock, or refund.
- Ineligible, inaccessible, expired, conflicting, repeated, and uncertain outcomes are safe and tested.

#### Issue D4: Add exchange workflow

Implement same-product, equal-price replacement validation, stock reservation,
confirmation, and persisted exchange requests.

Dependencies: D3 and C1.

Acceptance criteria:

- Exchange eligibility includes the owned original delivered line, policy window, condition, and absence of conflicting requests.
- Replacement must be the same product and equal price; unavailable, cross-product, and different-price cases are not silently substituted.
- A proposal identifies original and replacement variants, terms, expiry, and operation ID.
- Confirmation rechecks facts and atomically reserves replacement stock and creates the exchange request.
- Competing exchanges cannot over-reserve stock; repeated confirmation is idempotent.
- The response does not imply replacement shipment or original-item inspection has occurred, and all conflict/denial paths are tested.

## Area 5: Handover, Security, and Portfolio Validation

### Goal

Close the demo with a safe human-support fallback, integrated security and
failure behavior, and repeatable evidence that all four journeys and the
architecture are working as designed.

### Current State

Failures currently cover only basic Ollama/API errors and retry feedback. There
are no support tickets, operation-status resolution, CSRF/origin controls,
action audit boundaries, integrated scenario checks, or release/evaluation
report.

### Missing Functionality

- Confirmed local handover ticket with minimal context and real reference.
- Safe fallback from ambiguity, missing knowledge, denied actions, and unknown writes.
- Operation-status resolution without duplicate writes.
- Origin/CSRF and cookie protections for state-changing endpoints.
- End-to-end acceptance scenarios, security checks, performance observations, and documentation updates.

### Issues to Create

#### Issue E1: Add confirmed handover and integrated fallback

Add `prepare_handover`, explicit confirmation, a local support-ticket record,
operation-status lookup, and consistent fallback behavior across knowledge and
commerce journeys.

Dependencies: D2, D3, and D4; B2 for trace display.

Acceptance criteria:

- A customer request for a person or an unresolved automation path creates a proposal rather than an automatic ticket.
- Confirmation creates exactly one local demo ticket with a real reference, minimal request context, related order/operation IDs, unresolved issue, and attempted steps.
- Ambiguous requests ask targeted questions; missing/conflicting knowledge, denied actions, and unavailable variants do not fabricate success.
- Unknown write outcomes show pending status and resolve the same operation ID before retrying.
- All four journeys can reach handover, and ticket/operation data is session- and customer-scoped.
- No email, callback, live transfer, refund, or physical fulfillment is promised.

#### Issue E2: Apply cross-cutting security and operational hardening

Review and test the implemented API/UI boundaries: input validation, cookie
flags, same-origin/CSRF protection, safe rendering, allowlists, redaction,
retention, bounded retries, timeouts, and audit events.

Dependencies: A1, B2, C1, D1, and E1. Checks ship with feature work as well as this integration pass.

Acceptance criteria:

- State-changing cookie requests enforce an appropriate origin/CSRF policy and cookies use documented deployment-safe flags.
- User text, article content, model output, and tool strings cannot alter identity, permissions, available tools, or graph traversal.
- All public inputs have typed validation and bounded size/loop/retry/time limits.
- UI renders responses as text and source links resolve through trusted server source IDs.
- Traces and audit events contain action/operation outcomes but no secrets, raw private transcripts, hidden reasoning, or another customer's data.
- Retention cleanup, model failure, adapter timeout, and unknown-write tests verify recoverable behavior and no invented success.

#### Issue E3: Publish portfolio acceptance scenarios and evaluation report

Run the complete local demo as a repeatable validation suite and update the
README/architecture documentation with actual capabilities, limitations,
evaluation results, and manual browser checks.

Dependencies: B1, B2, C2, and E1-E2; all workflow issues D1-D4.

Acceptance criteria:

- Automated or scripted scenarios demonstrate buy, cancel, return, and exchange success and their principal denial/conflict paths.
- Scenarios show sources, graph paths, policy outcomes, tool results, operation IDs, and timings in the architecture panel where applicable.
- Voice works in the selected supported browser, while unsupported/denied voice falls back to text.
- Tests cover expired confirmations, two buyers competing for last stock, timeout after commit, repeated confirmation, and cross-customer access attempts.
- The retrieval report compares document-only and graph-assisted results on the fixed question set and records model/index versions.
- README and ARCHITECTURE.md accurately distinguish delivered behavior from remaining limitations, and all documented setup/test commands pass.

## Dependencies and Suggested Order

The recommended issue order keeps the application runnable after every issue:

1. **A1** Session isolation and demo identity.
2. **A2** SQLite shop fixtures and catalog services.
3. **B2** Structured traces and architecture panel.
4. **C1** Document RAG baseline.
5. **D1** Typed orchestration and confirmed purchase.
6. **D2** Cancellation workflow.
7. **D3** Return workflow.
8. **D4** Exchange workflow.
9. **B1** Browser voice loop.
10. **C2** Optional graph-assisted retrieval.
11. **E1** Handover and integrated fallback.
12. **E2** Security and operational hardening.
13. **E3** Portfolio acceptance and evaluation.

Cross-cutting tests, validation, timeouts, redaction, and trace events belong in
each feature issue; they must not be deferred entirely to E2. The open product,
policy, model, corpus, browser, retention, and latency decisions in
`ARCHITECTURE.md` should be resolved before their dependent issues begin.

## Follow-up: FUN SHOES Storefront and Order Identification

### Issue F1: Make FUN SHOES a shop experience with grounded support and verified order IDs

**Status:** Implementation tracked in [GitHub issue #39](https://github.com/carmenbickel/voice-agent-explorer/issues/39).

### Goal

Present the application as **FUN SHOES**, a fictional footwear shop with an
integrated text/voice shopping assistant, rather than a standalone chat about
AI voice bots. All shop advice must refer to FUN SHOES knowledge and business
records. Returns, exchanges, and cancellations must identify a real,
customer-owned order before an action can be proposed.

### Current implementation reviewed

The Repository Baseline and Current State sections above describe the earlier
planning snapshot, not the current code. As of this review:

- `backend/shop.py` provides a SQLite catalog with six products, variants,
  inventory, two demo customers, and eight seeded orders.
- `backend/sessions.py` and `backend/main.py` provide isolated sessions and
  server-owned demo identity; `frontend/` supports text and browser voice.
- `knowledge/articles/`, `backend/rag.py`, and `backend/graph_rag.py` provide
  articles, document retrieval, and optional graph-assisted retrieval.
- `backend/actions.py` already implements owned-order eligibility checks,
  proposals, explicit confirmation, and transactional commerce workflows.
- `backend/agent.py` still instructs the model to explain AI voice bots.
  `frontend/index.html` still uses Voice Agent Explorer branding and VAD
  examples. Shop fixtures and some knowledge/docs use Stepwise Shoes.
- `/chat` relies on model-generated ACTION arguments. There is no explicit
  collect-and-validate order-ID conversation flow. Existing journey tests
  inject complete action payloads, so they do not establish that a customer
  can start with “I want to return my shoes” and supply an ID on the next turn.
- Missing and inaccessible orders already receive a non-disclosing rejection
  in action services; the conversational flow needs a clear, recoverable
  customer-facing message before proposal creation.

### What to implement

1. **FUN SHOES storefront and assistant identity**
   - Update browser title, heading, welcome/reset messages, assistant label,
     input examples, and accessible labels to FUN SHOES shopping/support.
   - Add a simple shop landing/catalog area alongside the assistant. Show the
     existing six products with names, supported variants, prices, and current
     availability from the SQLite catalog through an appropriate read endpoint.
     Customers should be able to select a product to start a shopping question.
   - Keep text, voice, demo-customer selection, confirmations, and traces usable.
     Make the storefront the main customer experience and the assistant its
     shopping/support helper. Keep implementation in the existing plain frontend.
   - Replace the explainer system prompt with a FUN SHOES assistant role for
     product discovery, store policies, purchases, and order support.
   - Keep the local demo disclosure: this does not create real payment,
     fulfillment, refund, or external merchant integrations.

2. **FUN SHOES-specific knowledge and grounding**
   - Audit all articles, graph labels/provenance, fixtures, retrieval examples,
     evaluation questions, README, and architecture copy for obsolete branding.
     Use FUN SHOES consistently for the shop and its own-brand products.
   - Clearly scope shipping, sizing, care, returns, exchanges, cancellations,
     and support information to FUN SHOES. Preserve existing demo policy terms
     unless a change is necessary for consistency; do not invent new terms.
   - Retrieve only the intended shop's evidence. Never substitute Amazon or
     another retailer's policies or claim access to its orders. For such a
     request, explain that this assistant supports FUN SHOES orders only.
   - Use trusted catalog/order services for stock, price, ownership, and
     eligibility. Unknown store facts produce an explicit limitation or
     clarification, not general-retailer advice presented as FUN SHOES policy.
   - Rebuild/version affected retrieval artifacts and graph references and
     rerun the existing evaluation after corpus changes.

3. **Collect and validate the order ID in text and voice conversations**
   - For a request to return, exchange, or cancel without an explicitly supplied
     order ID, ask “What is your FUN SHOES order ID?” before creating a proposal.
     General policy questions do not require an ID.
   - Retain the pending intent in the owning session so an ID-only next turn
     continues the requested workflow. If the initial request contains an ID,
     validate it directly without asking the customer to repeat it.
   - Resolve IDs through SQLite using the server-owned customer identity. Do
     not infer an order from the customer's only/latest order or accept an ID
     invented by the model. Require demo-customer selection if unbound.
   - Handle surrounding whitespace and documented case normalization; for an
     ambiguous voice transcription, ask the customer to repeat or type the ID
     rather than guessing a near match.
   - For an unknown ID, say “I couldn't find a FUN SHOES order with that ID for
     your account. Please check the order ID and try again.” Use the same
     non-disclosing message for another customer's ID. Do not disclose its
     existence, owner, items, or status.
   - On a failed lookup, create no action proposal or business-record changes;
     preserve the pending intent so a corrected ID can continue immediately.
   - After a valid lookup, identify eligible lines from the order. Ask the
     customer to select an item when ambiguous, then collect missing reason,
     condition, or replacement size/variant as required by existing services.
     Customers must not need to know internal variant IDs or write ACTION JSON.
   - Keep deterministic eligibility checks and explicit confirmation as the
     only commit path. Present missing, ineligible, and confirmed results
     accurately in both displayed text and speech; never announce success for
     an unknown order or failed action.
   - Clear pending order context on session reset/customer switch/expiry and
     after completion; changing the target order must invalidate an obsolete
     proposal so it cannot accidentally act on the previous order.

4. **Three documented test orders**
   - Reuse these existing deterministic fixtures as FUN SHOES test orders;
     do not add duplicate records solely to demonstrate lookup. Document the
     owning customer, ID, items, state, and intended scenario in README and a
     clearly labelled demo-help area in the UI for the selected customer.

   | Customer | Order ID | State | Test scenario |
   | --- | --- | --- | --- |
   | `demo_maya` | `order_maya_1` | processing | Valid cancellation after confirmation |
   | `demo_maya` | `order_maya_3` | delivered | Valid return or same-product exchange after collecting required details |
   | `demo_maya` | `order_maya_2` | shipped | Order found, but cancellation denied by policy |

   - Use `order_missing_999` as an explicitly nonexistent test ID; never seed it.
   - Document fixture clock/window assumptions and the existing reset command.
     Return and exchange examples using the same line must run from separate
     fresh fixtures because active requests conflict. Do not weaken that rule.
   - Preserve other customer fixtures for isolation tests. Ordinary support
     conversations must never expose another customer's demo order list.

### Done / acceptance criteria

- [ ] The browser visibly presents FUN SHOES as a footwear shop with a catalog
  and integrated assistant; welcome/reset copy and voice greetings use that name.
- [ ] Catalog product/price/availability displays reflect SQLite data; product
  selection can start a relevant shopping conversation without breaking chat.
- [ ] The system prompt, active knowledge, graph, and customer-facing text use
  FUN SHOES consistently; no obsolete Stepwise branding or AI-bot tutorial
  greeting remains in the active customer experience.
- [ ] FUN SHOES policy questions use the shop's retrieved sources. Amazon-order
  requests are redirected appropriately; missing evidence never becomes an
  invented FUN SHOES policy or order result.
- [ ] Each of return, exchange, and cancel asks for an ID when omitted, then
  resumes correctly when the next message contains only the ID.
- [ ] An ID supplied in the initial request is validated directly; malformed
  or ambiguous spoken IDs prompt clarification without guessing.
- [ ] `order_missing_999` produces the clear not-found/retry message and no
  proposal or business mutation. A corrected valid ID resumes the same intent.
- [ ] Another customer's ID yields the same non-disclosing failure. Session
  reset, expiry, and customer switching cannot reuse stale order/action context.
- [ ] The three documented fixture IDs resolve for `demo_maya`; processing
  cancellation succeeds only after confirmation, delivered return/exchange
  follows existing rules, and shipped cancellation is denied without mutation.
- [ ] The user can select order items and replacement sizes conversationally;
  internal variant IDs and ACTION JSON are not required from the customer.
- [ ] Existing confirmation, ownership, eligibility, conflict, inventory, and
  idempotency behavior remains intact. Failed actions never produce success copy.
- [ ] Automated tests cover multi-turn missing/valid/unknown/corrected IDs for
  all three intents, initial-message IDs, cross-customer access, storefront
  data, knowledge scope, and context clearing. Run the full Python suite,
  voice JavaScript checks, retrieval evaluation, and `git diff --check`.
- [ ] Manual browser checks cover catalog layout, typed conversations, spoken
  ID clarification and response playback, invalid-ID recovery, and confirmation.
  Include a real-model conversation check: mocked complete ACTION payloads
  alone do not prove that the assistant can collect the required information.

### Scope and dependencies

Build on the existing session, catalog, retrieval, voice, and action services
(A1–E3). The user has selected **FUN SHOES** as the shop name. Keep existing
currency, region, products, and policy rules unless consistency requires a
specified adjustment. No new framework, general marketplace support, real
merchant integration, or production identity/payment system is required.
