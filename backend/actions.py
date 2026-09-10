"""Proposal lifecycle, operations, and the transactional purchase commit.

The model NEVER commits a write. A purchase only happens through the explicit
confirmation endpoint, which rechecks quote (current price), ownership,
quantity, and stock inside one BEGIN IMMEDIATE transaction, creates the
processing order + immutable line snapshots, and reserves inventory
atomically. Operations record idempotency keys so repeated confirmations and
timeout resolution never duplicate an order.
"""

import hashlib
import json
import secrets
import time
from threading import Lock


DEFAULT_PROPOSAL_TTL_SECONDS = 10 * 60
POLICY_VERSION = "policy-2026-06-v1"


class ProposalError(Exception):
    """Safe outcome surfaced by the confirm endpoint."""

    def __init__(self, status_code: int, code: str):
        self.status_code = status_code
        self.code = code
        super().__init__(code)


class ProposalStore:
    """In-memory proposals; business records live in SQLite only."""

    def __init__(self, ttl_seconds=DEFAULT_PROPOSAL_TTL_SECONDS, clock=None):
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._proposals = {}
        self._lock = Lock()

    def _now(self):
        return self.clock() if self.clock else 0.0

    def create(self, owner_session: str, owner_customer: str,
               kind: str, payload: dict) -> dict:
        """Server-side proposal creation from validated, typed data only."""
        proposal_id = secrets.token_urlsafe(16)
        proposal = {
            "proposal_id": proposal_id,
            "owner_session": owner_session,
            "owner_customer": owner_customer,
            "kind": kind,
            "payload": payload,
            "status": "proposed",
            "operation_id": None,
            "created": self.clock() if self.clock else None,
        }
        with self._lock:
            self._proposals[proposal_id] = proposal
        return proposal

    def view(self, proposal_id: str):
        with self._lock:
            return self._proposals.get(proposal_id)

    def consume(self, proposal_id: str) -> dict:
        with self._lock:
            return self._proposals.pop(proposal_id, None)

    def expired(self, proposal: dict) -> bool:
        if self.clock is None or proposal.get("created") is None:
            return False
        return proposal["created"] + self.ttl_seconds < self.clock()


def purchase_view(proposal: dict, store=None) -> dict:
    """A proposal reply shows exact items, quantities, total, expiry, id."""
    payload = proposal["payload"]
    seconds_left = None
    if store is not None and store.clock is not None and proposal.get("created"):
        seconds_left = max(0, int(store.ttl_seconds
                                  - (store.clock() - proposal["created"])))
    return {
        "proposal_id": proposal["proposal_id"],
        "kind": proposal["kind"],
        "items": [
            {"variant_id": item["variant_id"], "name": item["name"],
             "size": item["size"], "colour": item["colour"],
             "unit_price_cents": item["unit_price_cents"],
             "quantity": item["quantity"]}
            for item in payload["items"]
        ],
        "total_cents": sum(item["unit_price_cents"] * item["quantity"]
                           for item in payload["items"]),
        "status": proposal["status"],
        **({"seconds_left": seconds_left} if seconds_left is not None else {}),
    }


def payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


def create_purchase_proposal(connection, proposals, session, args):
    """Build a purchase proposal from authoritative catalog data only.

    The model proposes the tool call; prices, items, and variants come from
    SQLite. Anonymous sessions cannot buy: identity is server-resolved.
    """
    from backend.tools import validate_args
    if session.customer_id is None:
        raise ProposalError(400, "DEMO_CUSTOMER_REQUIRED")
    args = validate_args("propose_purchase", args)
    variant = connection.execute(
        "SELECT v.id, p.name, v.size, v.colour, v.price_cents,"
        " i.on_hand, i.reserved FROM variants v JOIN products p"
        " ON p.id = v.product_id JOIN inventory i ON i.variant_id = v.id"
        " WHERE v.id = ?", (args["variant_id"],)).fetchone()
    if variant is None:
        raise ProposalError(404, "PROPOSAL_NOT_AVAILABLE")
    available = variant["on_hand"] - variant["reserved"]
    if variant["price_cents"] is None:
        raise ProposalError(409, "PROPOSAL_CHANGED")
    if available < args["quantity"]:
        raise ProposalError(409, "OUT_OF_STOCK")
    payload = {"items": [{
        "variant_id": variant["id"],
        "name": variant["name"],
        "size": variant["size"],
        "colour": variant["colour"],
        "unit_price_cents": variant["price_cents"],
        "quantity": args["quantity"],
    }]}
    proposal = proposals.create(
        owner_session=session.id, owner_customer=session.customer_id,
        kind="purchase", payload=payload)
    proposal["payload_hash"] = payload_hash(payload)
    return purchase_view(proposal, proposals)


def confirm_purchase(connection, proposals, proposal_id: str,
                     session, operations) -> dict:
    """Commit only after a full recheck inside one immediate transaction."""
    proposal = proposals.view(proposal_id)
    if proposal is None:
        raise ProposalError(404, "PROPOSAL_NOT_AVAILABLE")
    if proposal["owner_session"] != session.id:
        raise ProposalError(404, "PROPOSAL_NOT_AVAILABLE")
    if proposal["status"] == "confirmed":
        # Resolution by operation ID: no new write, no duplicate order.
        return operations.as_dict(proposal["operation_id"], idempotent=True)
    if proposals.expired(proposal):
        proposals.consume(proposal_id)
        raise ProposalError(409, "PROPOSAL_EXPIRED")

    items = proposal["payload"]["items"]
    connection.execute("BEGIN IMMEDIATE")
    try:
        # Recheck quote (current DB price), ownership, quantity, and stock.
        for item in items:
            row = connection.execute(
                "SELECT v.price_cents FROM variants v WHERE v.id = ?",
                (item["variant_id"],)).fetchone()
            if row is None or row["price_cents"] != item["unit_price_cents"]:
                raise ProposalError(409, "PROPOSAL_CHANGED")
            changed = connection.execute(
                "UPDATE inventory SET reserved = reserved + ?"
                " WHERE variant_id = ? AND on_hand - reserved >= ?",
                (item["quantity"], item["variant_id"], item["quantity"]))
            if changed.rowcount != 1:
                raise ProposalError(409, "OUT_OF_STOCK")
        order_id = "order_" + secrets.token_urlsafe(8).lower()
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        connection.execute(
            "INSERT INTO orders (id, customer_id, state, policy_version,"
            " delivery_utc, created_utc) VALUES (?, ?, 'processing', ?,"
            " NULL, ?)",
            (order_id, proposal["owner_customer"], POLICY_VERSION, stamp))
        for item in items:
            variant = connection.execute(
                "SELECT p.name, v.size, v.colour FROM variants v"
                " JOIN products p ON p.id = v.product_id WHERE v.id = ?",
                (item["variant_id"],)).fetchone()
            connection.execute(
                "INSERT INTO order_lines (order_id, variant_id, item_name,"
                " size, colour, unit_price_cents, quantity)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (order_id, item["variant_id"], variant["name"],
                 variant["size"], variant["colour"],
                 item["unit_price_cents"], item["quantity"]))
        operation_id = "op_" + secrets.token_urlsafe(10)
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    # The proposal stays in "confirmed" state so repeated confirmations
    # resolve by operation ID without a new write or duplicate order.
    proposal["status"] = "confirmed"
    proposal["operation_id"] = operation_id
    operations.record(operation_id, proposal, order_id)
    return operations.as_dict(operation_id, idempotent=False)


class OperationStore:
    """Idempotency-keyed operation results for timeout/unknown resolution."""

    def __init__(self):
        self._operations = {}

    def record(self, operation_id: str, proposal: dict, order_id: str) -> None:
        self._operations[operation_id] = {
            "operation_id": operation_id,
            "owner_customer": proposal["owner_customer"],
            "kind": proposal["kind"],
            "payload_hash": payload_hash(proposal["payload"]),
            "state": "processing",
            "order_id": order_id,
        }

    def get(self, operation_id: str):
        return self._operations.get(operation_id)

    def as_dict(self, operation_id: str, idempotent: bool = False):
        operation = dict(self.get(operation_id))
        operation["idempotent"] = idempotent
        return operation
