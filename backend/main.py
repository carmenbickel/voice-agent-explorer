from fastapi import FastAPI

from backend.models import ChatRequest, ChatResponse

app = FastAPI(
    title="Voice Agent Explorer API",
    version="0.1.0",
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    return ChatResponse(
        response=f"You said: {request.message}"
    )