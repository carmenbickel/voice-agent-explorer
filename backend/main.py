import os
import re
import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend import actions as actions_module
from backend import graph_rag, order_support
from backend.agent import run_chat_turn
from backend.models import ChatRequest, ChatResponse, DemoCustomerSelection, SwitchCustomerRequest
from backend.ollama_client import OllamaError
from backend.rag import (
    assemble_messages,
    build_index,
    build_evidence_context,
    load_articles,
    remove_invalid_citations,
    retrieve,
    validate_citations,
)
from backend.sessions import (
    DEMO_CUSTOMERS,
    SESSION_COOKIE,
    DemoCustomerNotFoundError,
    Session,
    SessionManager,
    idle_timeout_from_env,
)
from backend.tools import parse_action_line, ACTION_INSTRUMENT, ToolError, validate_args  # noqa: F401
from backend.traces import TraceStore


app = FastAPI(
    title="FUN SHOES API",
    version="0.1.0",
)

manager = SessionManager(idle_timeout_seconds=idle_timeout_from_env())
traces = TraceStore()
proposals = actions_module.ProposalStore(clock=time.time)
operations = actions_module.OperationStore()
knowledge_corpus = load_articles()
knowledge_index = build_index(corpus=knowledge_corpus)
knowledge_graph = graph_rag.load_graph()
RAG_MODE = os.environ.get("RAG_MODE", "document-only")
frontend_directory = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=frontend_directory), name="static")

SESSION_NOT_ACTIVE = "Session is not active."
CROSS_ORIGIN_REJECTED = "Cross-origin request rejected."
CORS_SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
# The demo pins its eligibility clock to the seeded fixture date
# (2026-06-02T13:20:00+00:00) so the 30-day return window stays testable.
DEMO_NOW_EPOCH = 1780394400.0


def assert_same_origin(http: Request) -> None:
    """Same-origin/CSRF policy for state-changing cookie requests.

    Requests without an Origin header (native clients, same-origin tests)
    are allowed; a cross-site Origin is rejected with a non-disclosing 403.
    """
    origin = http.headers.get("origin")
    if not origin:
        return
    host = http.headers.get("host")
    allowed = set()
    if host:
        allowed.add(f"http://{host}")
        allowed.add(f"https://{host}")
    if origin in allowed:
        return
    raise HTTPException(status_code=403, detail=CROSS_ORIGIN_REJECTED)
CUSTOMER_NOT_AVAILABLE = "Requested demo customer is not available."
TRACE_NOT_AVAILABLE = "Trace is not available."
PROPOSAL_NOT_AVAILABLE = "Proposal is not available."
OPERATION_NOT_AVAILABLE = "Operation is not available."
BUY_FAILURE_DETAIL = {
    "DEMO_CUSTOMER_REQUIRED":
        "Please select a demo customer first, then ask again to buy.",
    "OUT_OF_STOCK":
        "That item no longer has enough stock. Ask for another size or product.",
    "PROPOSAL_CHANGED":
        "The price or availability changed. Ask again for an updated proposal.",
    "PROPOSAL_NOT_AVAILABLE":
        "I could not prepare that purchase.",
}


def shop_connection():
    import sqlite3
    from backend import shop
    connection = shop.connect(shop.database_path())
    connection.row_factory = sqlite3.Row
    return connection


def resolve_session(request: Request) -> Session:
    """Resolve the session from the server-issued opaque cookie only."""
    session_id = request.cookies.get(SESSION_COOKIE)
    session = manager.get(session_id) if session_id else None
    if session is None:
        raise HTTPException(status_code=403, detail=SESSION_NOT_ACTIVE)
    return session


def customer_view(session: Session):
    if session.customer_id is None:
        return None
    return {"id": session.customer_id, "name": DEMO_CUSTOMERS[session.customer_id]["name"]}


def issue_session(response: Response, session: Session) -> None:
    response.set_cookie(SESSION_COOKIE, session.id, httponly=True, samesite="lax")



@app.get("/", include_in_schema=False)
def index():
    return FileResponse(frontend_directory / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


def _begin_trace(session: Session, turn_id: str):
    """Best-effort trace creation; failures never affect the chat response."""
    try:
        trace = traces.begin()
        trace["turn_id"] = turn_id
        session.traces.append(trace["trace_id"])
        traces.record(trace, "session resolved", "executed")
        return trace
    except Exception:
        return None


def _record_stage(trace, stage: str, status: str, **extra) -> None:
    if trace is None:
        return
    try:
        traces.record(trace, stage, status, **extra)
        if status in ("executed", "skipped", "failed"):
            if stage == "response assembly":
                traces.finish(trace, "error" if status == "failed" else "ok")
    except Exception:
        pass


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, http: Request):
    assert_same_origin(http)
    session = resolve_session(http)
    turn_id = secrets.token_urlsafe(12)
    trace = _begin_trace(session, turn_id)
    started = time.time()
    with session.lock:
        connection = shop_connection()
        try:
            support = order_support.handle(session, request.message, connection,
                                           proposals, operations, lambda: DEMO_NOW_EPOCH)
        finally:
            connection.close()
        if support is not None:
            response, proposal = support
            session.history += [{"role": "user", "content": request.message},
                                {"role": "assistant", "content": response}]
            _record_stage(trace, "order support", "executed")
            _record_stage(trace, "proposal", "executed" if proposal else "skipped")
            _record_stage(trace, "response assembly", "executed")
            return ChatResponse(response=response, action_proposal=proposal,
                                turn_id=turn_id, trace_id=trace["trace_id"] if trace else None)
    sources = []
    try:
        evidence_text = None
        retrieved = []
        try:
            if RAG_MODE == "graph":
                retrieved = graph_rag.graph_assisted_chunks(
                    knowledge_index, request.message, knowledge_graph)
                retrieved = [chunk for chunk in retrieved
                             if not chunk.get("graph_paths") or True]
            else:
                retrieved = retrieve(knowledge_index, request.message)
            if not retrieved:
                _record_stage(trace, "retrieval", "executed",
                              detail="no matching evidence")
        except Exception as failure:  # index build or embedding failure
            _record_stage(trace, "retrieval", "failed")
            retrieved = []
        if retrieved:
            sources = sorted(chunk["chunk_id"] for chunk in retrieved)
            evidence_text = assemble_messages(
                build_evidence_context(retrieved))[-1]["content"]
            evidence_text = evidence_text + "\n" + ACTION_INSTRUMENT
            _record_stage(trace, "retrieval", "executed",
                          detail=", ".join(sources))
        # Missing store evidence must not become an invented commercial promise.
        store_question = re.search(
            r"\b(fun shoes|policy|policies|warranty|guarantee|discount|shipping|delivery|"
            r"opening|hours|address|refund|payment|offer|sell)\b", request.message.lower())
        abstained = not retrieved and bool(store_question)
        if abstained:
            response = "I don't have FUN SHOES knowledge covering that question. I cannot confirm that policy or offer. Please ask about our documented products, shipping, returns, exchanges, or cancellations."
            with session.lock:
                session.history += [{"role": "user", "content": request.message},
                                    {"role": "assistant", "content": response}]
        else:
            response = run_chat_turn(session, request.message, evidence_text)
        sources = list(sources)
        if retrieved:
            response = remove_invalid_citations(response, retrieved)
        # Typed orchestration: the model may propose an action; proposal
        # creation is server-side, min one effective action per turn.
        action = parse_action_line(response)
        action_proposal = None
        if action and action.get("tool") in ("propose_return", "propose_exchange", "propose_cancellation"):
            # Order support must collect customer-supplied IDs, never model guesses.
            response = "For order support, please tell me whether you want to return, exchange, or cancel and provide your FUN SHOES order ID."
            action = None
        if action and action["tool"] is None:
            # Tool loop exceeded or malformed action: keep the response but
            # do not act on it. Trace records the rejection.
            _record_stage(trace, "proposal", "skipped",
                          detail=action["error_code"])
            response = response.split(ACTION_PREFIX)[0].strip()
        elif action and action["tool"] in ("propose_purchase",
                                           "propose_cancellation",
                                           "propose_return",
                                           "propose_exchange",
                                           "propose_handover"):
            try:
                if action["tool"] == "propose_purchase":
                    proposal = actions_module.create_purchase_proposal(
                        shop_connection(), proposals, session, action["args"])
                elif action["tool"] == "propose_return":
                    proposal = actions_module.create_return_proposal(
                        shop_connection(), proposals, session,
                        action["args"], clock=lambda: DEMO_NOW_EPOCH)
                elif action["tool"] == "propose_exchange":
                    proposal = actions_module.create_exchange_proposal(
                        shop_connection(), proposals, session,
                        action["args"], clock=lambda: DEMO_NOW_EPOCH)
                elif action["tool"] == "propose_handover":
                    proposal = actions_module.create_handover_proposal(
                        shop_connection(), proposals, session,
                        action["args"])
                else:
                    proposal = actions_module.create_cancellation_proposal(
                        shop_connection(), proposals, session,
                        action["args"], operations)
                if isinstance(proposal, dict) and proposal.get("kind") in (
                        "purchase", "cancellation", "return", "exchange",
                        "handover"):
                    action_proposal = proposal
                    _record_stage(trace, "proposal", "executed",
                                  detail=proposal["proposal_id"])
                    response = response.split("ACTION ")[0].strip()
                elif isinstance(proposal, dict) and \
                        proposal.get("status") == "already_cancelled":
                    outcome = proposal.get("operation")
                    reference = outcome["operation_id"] if outcome else \
                        "no ticket was recorded"
                    response = (
                        response.split("ACTION ")[0].strip()
                        + "\nThis order is already cancelled. Reference: "
                        + str(reference))
                    _record_stage(trace, "proposal", "executed",
                                  detail="already cancelled; existing outcome")
            except actions_module.ProposalError as failure:
                _record_stage(trace, "proposal", "failed",
                              detail=failure.code)
                response = response.split("ACTION ")[0].strip()
                response = (
                    response + "\n" + BUY_FAILURE_DETAIL.get(failure.code, "")
                ).strip()
        _record_stage(trace, "model", "skipped" if abstained else "executed",
                      duration_ms=(time.time() - started) * 1000)
        _record_stage(trace, "response assembly", "executed")
        return ChatResponse(
            response=response,
            turn_id=turn_id,
            trace_id=trace["trace_id"] if trace else None,
            sources=sources,
            action_proposal=action_proposal,
        )

    except OllamaError as exc:
        _record_stage(trace, "model", "failed")
        _record_stage(trace, "response assembly", "skipped",
                      detail="model error")
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    except Exception:
        # Any unexpected failure stays generic and recoverable; traces
        # record the failure without user content (hardening, issue E2).
        _record_stage(trace, "model", "failed")
        _record_stage(trace, "response assembly", "skipped",
                      detail="internal model error")
        raise HTTPException(status_code=503, detail="Model unavailable")


@app.get("/traces/{trace_id}")
def read_trace(trace_id: str, http: Request):
    """Session-scoped trace access; cross-session reads get a safe 404."""
    session = resolve_session(http)
    if trace_id not in session.traces:
        raise HTTPException(status_code=404, detail=TRACE_NOT_AVAILABLE)
    view = traces.view(trace_id)
    if view is None:
        raise HTTPException(status_code=404, detail=TRACE_NOT_AVAILABLE)
    return view


@app.post("/actions/{proposal_id}/confirm")
def confirm_action(proposal_id: str, http: Request):
    session = resolve_session(http)
    with session.lock:
        return _confirm_action(proposal_id, http)


def _confirm_action(proposal_id: str, http: Request):
    """The ONLY write path: rechecks quote, ownership, quantity, stock."""
    assert_same_origin(http)
    session = resolve_session(http)
    connection = shop_connection()
    try:
        proposal = proposals.view(proposal_id)
        if proposal is None or proposal["owner_session"] != session.id:
            result = actions_module.confirm_purchase(
                connection, proposals, proposal_id, session, operations)
            kind = "purchase"
        else:
            kind = proposal["kind"]
            if kind == "cancellation":
                result = actions_module.confirm_cancellation(
                    connection, proposals, proposal_id, session, operations)
            elif kind == "return":
                result = actions_module.confirm_return(
                    connection, proposals, proposal_id, session, operations,
                    clock=lambda: DEMO_NOW_EPOCH)
            elif kind == "exchange":
                result = actions_module.confirm_exchange(
                    connection, proposals, proposal_id, session, operations,
                    clock=lambda: DEMO_NOW_EPOCH)
            elif kind == "handover":
                result = actions_module.confirm_handover(
                    connection, proposals, proposal_id, session, operations)
            else:
                result = actions_module.confirm_purchase(
                    connection, proposals, proposal_id, session, operations)
    except actions_module.ProposalError as failure:
        connection.close()
        if failure.status_code == 404 or failure.code == "PROPOSAL_NOT_AVAILABLE":
            detail = PROPOSAL_NOT_AVAILABLE
        elif failure.code == "PROPOSAL_EXPIRED":
            detail = "Proposal expired. Ask again to get a new proposal."
        elif failure.code == "NOT_CANCELLABLE":
            detail = "Only processing orders can be cancelled."
        elif failure.code == "NOT_RETURNABLE_YET":
            detail = "Only delivered lines can be returned."
        elif failure.code == "RETURN_WINDOW_EXPIRED":
            detail = "The return window has expired for this order."
        elif failure.code == "REQUEST_ALREADY_ACTIVE":
            detail = ("An active return or exchange request already exists"
                      " for this line.")
        elif failure.code == "REASON_NOT_ACCEPTED":
            detail = ("The return reason is not accepted; allowed reasons:"
                      " does not fit, not as described, other.")
        elif failure.code == "CONDITION_NOT_ACCEPTED":
            detail = "Only unworn or worn-once conditions are accepted."
        else:
            detail = f"Proposal no longer valid ({failure.code})."
        raise HTTPException(status_code=failure.status_code, detail=detail)
    connection.close()
    session.order_context = None
    session.pending_proposal = None
    return result


@app.get("/operations/{operation_id}")
def read_operation(operation_id: str, http: Request):
    """Resolve timeout/unknown outcomes via operation ID (no new write)."""
    session = resolve_session(http)
    operation = operations.get(operation_id)
    if operation is None or operation.get("owner_customer") != session.customer_id:
        raise HTTPException(status_code=404, detail=OPERATION_NOT_AVAILABLE)
    view = dict(operation)
    view["idempotent"] = False
    return view


@app.post("/sessions")
def create_session(http: Request, response: Response, selection: Optional[DemoCustomerSelection] = Body(default=None)):
    assert_same_origin(http)
    """Establish an anonymous or explicit local demo-customer session."""
    requested = selection.customer_id if selection else None
    session = manager.create()
    if requested is not None:
        try:
            manager.switch_customer(session, requested)
        except DemoCustomerNotFoundError:
            raise HTTPException(status_code=404, detail=CUSTOMER_NOT_AVAILABLE)
    issue_session(response, session)
    return {"customer": customer_view(session)}


@app.get("/sessions/customers")
def list_demo_customers():
    """List seeded demo customers available to a local session."""
    return {"customers": [
        {"id": customer_id, "name": profile["name"]}
        for customer_id, profile in DEMO_CUSTOMERS.items()
    ]}


@app.post("/sessions/customer")
def switch_customer(selection: SwitchCustomerRequest, response: Response, http: Request):
    assert_same_origin(http)
    """Switch this session's demo customer, clearing context and pending state."""
    session = resolve_session(http)
    try:
        with session.lock:
            order_support.invalidate(session, proposals)
            profile = manager.switch_customer(session, selection.customer_id.strip())
    except DemoCustomerNotFoundError:
        raise HTTPException(status_code=404, detail=CUSTOMER_NOT_AVAILABLE)
    issue_session(response, session)
    return {"customer": profile}


@app.post("/sessions/reset")
def reset_session(http: Request):
    assert_same_origin(http)
    """Clear this session's history and pending proposals only."""
    session = resolve_session(http)
    with session.lock:
        order_support.invalidate(session, proposals)
        manager.reset(session)
    return {"status": "ok", "customer": customer_view(session)}


@app.get('/catalog')
def catalog():
    connection = shop_connection()
    try:
        rows = connection.execute(
            'SELECT p.id AS product_id, p.name, p.category, v.id, v.size, '
            'v.colour, v.price_cents, i.on_hand - i.reserved AS available '
            'FROM products p JOIN variants v ON v.product_id = p.id '
            'JOIN inventory i ON i.variant_id = v.id ORDER BY p.name, v.size').fetchall()
        return {'store': 'FUN SHOES', 'variants': [dict(row) for row in rows]}
    finally:
        connection.close()


@app.get('/demo/orders')
def demo_orders(http: Request):
    from backend.shop import Catalog
    session = resolve_session(http)
    connection = shop_connection()
    try:
        return {'orders': Catalog(connection).list_customer_orders(session.customer_id)
                if session.customer_id else []}
    finally:
        connection.close()
