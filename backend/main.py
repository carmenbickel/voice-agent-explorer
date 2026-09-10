from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.agent import run_chat_turn
from backend.models import ChatRequest, ChatResponse, DemoCustomerSelection, SwitchCustomerRequest
from backend.ollama_client import OllamaError
from backend.sessions import (
    DEMO_CUSTOMERS,
    SESSION_COOKIE,
    DemoCustomerNotFoundError,
    Session,
    SessionManager,
    idle_timeout_from_env,
)


app = FastAPI(
    title="Voice Agent Explorer API",
    version="0.1.0",
)

manager = SessionManager(idle_timeout_seconds=idle_timeout_from_env())
frontend_directory = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=frontend_directory), name="static")

SESSION_NOT_ACTIVE = "Session is not active."
CUSTOMER_NOT_AVAILABLE = "Requested demo customer is not available."


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


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, http: Request):
    session = resolve_session(http)
    try:
        response = run_chat_turn(session, request.message)
        return ChatResponse(response=response)

    except OllamaError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc


@app.post("/sessions")
def create_session(response: Response, selection: Optional[DemoCustomerSelection] = Body(default=None)):
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
    """Switch this session's demo customer, clearing context and pending state."""
    session = resolve_session(http)
    try:
        profile = manager.switch_customer(session, selection.customer_id.strip())
    except DemoCustomerNotFoundError:
        raise HTTPException(status_code=404, detail=CUSTOMER_NOT_AVAILABLE)
    issue_session(response, session)
    return {"customer": profile}


@app.post("/sessions/reset")
def reset_session(http: Request):
    """Clear this session's history and pending proposals only."""
    session = resolve_session(http)
    manager.reset(session)
    return {"status": "ok", "customer": customer_view(session)}
