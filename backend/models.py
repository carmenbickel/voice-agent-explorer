from typing import List, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    response: str
    turn_id: Optional[str] = None
    trace_id: Optional[str] = None
    sources: Optional[List[str]] = None
    action_proposal: Optional[dict] = None


class DemoCustomerSelection(BaseModel):
    customer_id: Optional[str] = Field(default=None, max_length=64,
                                       pattern=r"^[A-Za-z0-9_.-]*$")


class SwitchCustomerRequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=64,
                             pattern=r"^[A-Za-z0-9_.-]+$")