from fastapi import FastAPI, HTTPException

from backend.agent import ConversationAgent
from backend.models import ChatRequest, ChatResponse
from backend.ollama_client import OllamaError


app = FastAPI(
    title="Voice Agent Explorer API",
    version="0.1.0",
)

agent = ConversationAgent()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    try:
        response = agent.chat(request.message)
        return ChatResponse(response=response)

    except OllamaError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc
