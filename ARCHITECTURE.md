
Then put this in `ARCHITECTURE.md`:

```md
# Architecture

## Goal

Build a small, local AI voice bot that demonstrates the main components involved in a conversational voice system without unnecessary infrastructure.

## Initial target architecture

```text
User
  |
  | Voice
  v
Browser
  |
  | Speech-to-Text
  v
Frontend
  |
  | REST
  v
FastAPI Backend
  |
  v
Conversation Agent
  |
  +---- Conversation State
  |
  +---- Knowledge Retrieval
  |
  +---- Ollama
           |
           v
        Local LLM

Response
  |
  v
Browser Text-to-Speech
  |
  v
User