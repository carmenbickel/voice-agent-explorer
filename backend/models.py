from typing import List, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str
    turn_id: Optional[str] = None
    trace_id: Optional[str] = None
    sources: Optional[List[str]] = None
    action_proposal: Optional[dict] = None


class DemoCustomerSelection(BaseModel):
    customer_id: Optional[str] = None


class SwitchCustomerRequest(BaseModel):
    customer_id: str