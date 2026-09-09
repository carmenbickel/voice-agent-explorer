from fastapi import FastAPI, HTTPException

from backend.models import ChatRequest, ChatResponse
from backend.ollama_client import OllamaError, generate_response


app = FastAPI(
    title="Voice Agent Explorer API",
    version="0.1.0",
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    try:
        response = generate_response(request.message)
        return ChatResponse(response=response)

    except OllamaError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc