"""Typed tool orchestration with an allowlist and bounded loops (issue D1).

The model may propose tool calls; every call is validated against typed
argument schemas and executed against the authoritative SQLite catalog.
Unknown tools, malformed arguments, and excessive calls per turn are safely
rejected. Tools are reads plus proposal creation only: no LLM output ever
commits a write or provides identity.
"""

import json
import re

from backend.ollama_client import OllamaError

MAX_ACTIONS_PER_TURN = 1
ACTION_PREFIX = "ACTION "

class ToolError(Exception):
    """A safe rejection reason surfaced to the trace panel."""

    def __init__(self, code: str, detail: str = None):
        self.code = code
        super().__init__(detail or code)


_TOOL_SPEC = {
    "search_products": {"query": str},
    "get_variant_stock": {"variant_id": str},
    "propose_purchase": {"variant_id": str, "quantity": int},
    "propose_cancellation": {"order_id": str},
    "propose_return": {"order_id": str, "variant_id": str, "reason": str,
                       "condition": str},
    "propose_exchange": {"order_id": str, "variant_id": str,
                         "replacement_variant_id": str, "condition": str},
    "propose_handover": {"unresolved_issue": str},
}


def parse_action_line(response_text: str):
    """Extract and validate the single typed ACTION (JSON) from a reply.

    Malformed output yields no action; the reply stays plain text. More than
    one ACTION in one reply is treated as a runaway loop and rejected.
    """
    if not response_text:
        return None
    lines = [line for line in response_text.splitlines()
             if line.strip().startswith(ACTION_PREFIX)]
    if not lines:
        return None
    if len(lines) > 1:
        return {"tool": None, "args": None, "error_code": "TOOL_LOOP_EXCEEDED"}
    try:
        payload = json.loads(lines[0][len(ACTION_PREFIX):])
    except json.JSONDecodeError:
        return {"tool": None, "args": None, "error_code": "MALFORMED_ACTION"}
    if not isinstance(payload, dict) or not isinstance(payload.get("tool"), str):
        return {"tool": None, "args": None, "error_code": "MALFORMED_ACTION"}
    args = payload.get("args")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return {"tool": None, "args": None, "error_code": "MALFORMED_ACTION"}
    return {"tool": payload["tool"], "args": args, "error_code": None}


def validate_args(tool_name: str, args: dict, schema: dict = None):
    """Typed argument validation against the allowlisted tool spec."""
    spec = _TOOL_SPEC.get(tool_name)
    if spec is None:
        raise ToolError("UNKNOWN_TOOL", f"tool {tool_name!r} is not allowed")
    if set(args.keys()) != set(spec.keys()):
        raise ToolError("INVALID_ARGUMENTS",
                        f"{tool_name} expects {sorted(spec)}")
    for key, kind in spec.items():
        value = args[key]
        if spec[key] is int and (isinstance(value, bool) or not isinstance(value, int)):
            raise ToolError("INVALID_ARGUMENTS", f"{key} must be a positive number")
        if spec[key] is str and not isinstance(value, str):
            raise ToolError("INVALID_ARGUMENTS", f"{key} must be a string")
    if tool_name in ("propose_purchase",) and args.get("quantity", 0) <= 0:
        raise ToolError("INVALID_ARGUMENTS", "quantity must be positive")
    return args


def search_products(connection, query: str):
    """Filter products/variants by keyword against current SQLite rows."""
    tokens = [token for token in re.findall(r"[a-z0-9]+", (query or "").lower()) if token]
    if not tokens:
        return {"status": "clarify", "detail": "Type what product you are looking for."}
    rows = connection.execute(
        "SELECT p.id, p.name, p.category, v.id AS variant_id, v.size, v.colour,"
        " v.price_cents, i.on_hand, i.reserved"
        " FROM products p JOIN variants v ON v.product_id = p.id"
        " JOIN inventory i ON i.variant_id = v.id").fetchall()
    matches = []
    for row in rows:
        haystack = f"{row['name']} {row['category']} {row['size']} {row['colour']}".lower()
        if any(token in haystack for token in tokens):
            matches.append(dict(row))
    product_hits = {match["id"] for match in matches}
    if len(product_hits) > 1:
        names = sorted(next(r["name"] for r in matches if r["id"] == product_id)
                       for product_id in product_hits)
        return {"status": "clarify",
                "detail": "Several products match. Which one? " + ", ".join(names)}
    if not matches:
        return {"status": "clarify", "detail": "No matching product. Ask for another product."}
    return {"status": "ok", "products": [dict(match) for match in matches]}


def dispatch(tool_name: str, args: dict, connection):
    """Execute an allowlisted read against the authoritative SQLite catalog."""
    args = validate_args(tool_name, args)
    if tool_name == "search_products":
        return search_products(connection, args["query"])
    if tool_name == "get_variant_stock":
        row = connection.execute(
            "SELECT v.id, p.name, v.size, v.colour, v.price_cents,"
            " i.on_hand, i.reserved FROM variants v JOIN products p"
            " ON p.id = v.product_id JOIN inventory i ON i.variant_id = v.id"
            " WHERE v.id = ?", (args["variant_id"],)).fetchone()
        if row is None:
            return {"status": "error", "code": "VARIANT_NOT_FOUND"}
        available = row["on_hand"] - row["reserved"]
        return {"status": "ok", "variant": {"id": row["id"], "name": row["name"],
                "size": row["size"], "colour": row["colour"],
                "price_cents": row["price_cents"], "available": available}}
    raise ToolError("UNKNOWN_TOOL", f"tool {tool_name!r} is not allowed")


ACTION_INSTRUMENT = (
    "When the user wants to buy, return, exchange, or cancel, reply with one"
    " final line ACTION followed by JSON: ACTION {\"tool\": <name>,"
    " \"args\": {...}}. Only the listed tools are allowed."
)
