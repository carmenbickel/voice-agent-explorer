from typing import Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str


class DemoCustomerSelection(BaseModel):
    customer_id: Optional[str] = None


class SwitchCustomerRequest(BaseModel):
    customer_id: str