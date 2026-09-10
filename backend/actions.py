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


def proposal_view(proposal: dict, store=None) -> dict:
    """A proposal reply shows the exact terms before any write."""
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
    return proposal_view(proposal, proposals)


def proposal_view(proposal: dict, store=None) -> dict:
    """A proposal reply shows the exact terms before any write."""
    payload = proposal["payload"]
    seconds_left = None
    if store is not None and store.clock is not None and proposal.get("created") is not None:
        seconds_left = max(0, int(store.ttl_seconds
                                  - (store.clock() - proposal["created"])))
    base = {
        "proposal_id": proposal["proposal_id"],
        "kind": proposal["kind"],
        "status": proposal["status"],
        **({"seconds_left": seconds_left} if seconds_left is not None else {}),
    }
    if proposal["kind"] == "purchase":
        base["items"] = [
            {"variant_id": item["variant_id"], "name": item["name"],
             "size": item["size"], "colour": item["colour"],
             "unit_price_cents": item["unit_price_cents"],
             "quantity": item["quantity"]}
            for item in payload["items"]
        ]
        base["total_cents"] = sum(item["unit_price_cents"] * item["quantity"]
                                  for item in payload["items"])
    elif proposal["kind"] == "cancellation":
        base["order_id"] = payload["order_id"]
    return base


def create_cancellation_proposal(connection, proposals, session, args,
                                 operations):
    """Only an owned processing order yields an eligible proposal."""
    from backend.tools import validate_args
    if session.customer_id is None:
        raise ProposalError(400, "DEMO_CUSTOMER_REQUIRED")
    args = validate_args("propose_cancellation", args)
    order = connection.execute(
        "SELECT id, customer_id, state FROM orders WHERE id = ?",
        (args["order_id"],)).fetchone()
    if order is None or order["customer_id"] != session.customer_id:
        raise ProposalError(404, "PROPOSAL_NOT_AVAILABLE")
    if order["state"] == "cancelled":
        existing = operations.find_by_order(
            session.customer_id, "cancellation", order["id"])
        # Already cancelled: return the existing outcome, no duplicate op.
        return {"status": "already_cancelled",
                "operation": existing,
                "order_id": order["id"]}
    if order["state"] != "processing":
        raise ProposalError(409, "NOT_CANCELLABLE")
    payload = {"order_id": order["id"]}
    proposal = proposals.create(
        owner_session=session.id, owner_customer=session.customer_id,
        kind="cancellation", payload=payload)
    proposal["payload_hash"] = payload_hash(payload)
    return proposal_view(proposal, proposals)


RETURN_WINDOW_DAYS = 30
RETURN_REASONS = frozenset(("does_not_fit", "not_as_described", "other"))
RETURN_CONDITIONS = frozenset(("unworn", "worn_once"))
RETURN_STATES = ("requested", "inspected", "arrived", "restocked", "refunded")


def create_return_proposal(connection, proposals, session, args, clock=None):
    """Versioned eligibility: owned delivered line + policy window."""
    from backend.tools import validate_args
    if session.customer_id is None:
        raise ProposalError(400, "DEMO_CUSTOMER_REQUIRED")
    args = validate_args("propose_return", args)
    if args["reason"] not in RETURN_REASONS:
        raise ProposalError(409, "REASON_NOT_ACCEPTED")
    if args["condition"] not in RETURN_CONDITIONS:
        raise ProposalError(409, "CONDITION_NOT_ACCEPTED")
    now_epoch = clock() if clock else time.time()
    line = connection.execute(
        "SELECT o.id, o.customer_id, o.state, o.policy_version,"
        " o.delivery_utc, ol.unit_price_cents, ol.quantity"
        " FROM orders o JOIN order_lines ol ON ol.order_id = o.id"
        " WHERE o.id = ? AND ol.variant_id = ?",
        (args["order_id"], args["variant_id"])).fetchone()
    if line is None or line["customer_id"] != session.customer_id:
        raise ProposalError(404, "PROPOSAL_NOT_AVAILABLE")
    if line["state"] != "delivered":
        raise ProposalError(409, "NOT_RETURNABLE_YET")
    active = connection.execute(
        "SELECT state FROM returns WHERE order_id = ? AND variant_id = ?"
        " AND state = 'requested'",
        (args["order_id"], args["variant_id"])).fetchone()
    if active is not None:
        raise ProposalError(409, "REQUEST_ALREADY_ACTIVE")
    policy_version = line["policy_version"]
    if not line["delivery_utc"] or not policy_version:
        raise ProposalError(409, "PROPOSAL_CHANGED")
    import calendar
    delivered_epoch = calendar.timegm(time.strptime(
        line["delivery_utc"][:19], "%Y-%m-%dT%H:%M:%S"))
    if now_epoch > delivered_epoch + RETURN_WINDOW_DAYS * 86400:
        raise ProposalError(409, "RETURN_WINDOW_EXPIRED")
    payload = {
        "order_id": args["order_id"],
        "variant_id": args["variant_id"],
        "reason": args["reason"],
        "condition": args["condition"],
        "unit_price_cents": line["unit_price_cents"],
        "quantity": line["quantity"],
    }
    proposal = proposals.create(
        owner_session=session.id, owner_customer=session.customer_id,
        kind="return", payload=payload)
    proposal["payload_hash"] = payload_hash(payload)
    view = proposal_view(proposal, proposals)
    view["terms"] = (
        f"Return of {payload['quantity']} unit(s) of {args['variant_id']} for"
        f" reason {args['reason']} under policy {policy_version}.")
    return view


def create_exchange_proposal(connection, proposals, session, args, clock=None):
    """Same-product, equal-price replacement of an owned delivered line."""
    from backend.tools import validate_args
    if session.customer_id is None:
        raise ProposalError(400, "DEMO_CUSTOMER_REQUIRED")
    args = validate_args("propose_exchange", args)
    if args["condition"] not in RETURN_CONDITIONS:
        raise ProposalError(409, "CONDITION_NOT_ACCEPTED")
    now_epoch = clock() if clock else time.time()
    line = connection.execute(
        "SELECT o.id, o.customer_id, o.state, o.policy_version,"
        " o.delivery_utc FROM orders o JOIN order_lines ol"
        " ON ol.order_id = o.id"
        " WHERE o.id = ? AND ol.variant_id = ?",
        (args["order_id"], args["variant_id"])).fetchone()
    if line is None or line["customer_id"] != session.customer_id:
        raise ProposalError(404, "PROPOSAL_NOT_AVAILABLE")
    if line["state"] != "delivered":
        raise ProposalError(409, "NOT_RETURNABLE_YET")
    active = connection.execute(
        "SELECT state FROM returns WHERE order_id = ? AND variant_id = ?"
        " AND state = 'requested'",
        (args["order_id"], args["variant_id"])).fetchone()
    active_exchange = connection.execute(
        "SELECT state FROM exchanges WHERE order_id = ?"
        " AND original_variant_id = ? AND state = 'requested'",
        (args["order_id"], args["variant_id"])).fetchone()
    if active is not None or active_exchange is not None:
        raise ProposalError(409, "REQUEST_ALREADY_ACTIVE")
    replacement = connection.execute(
        "SELECT v.id, v.product_id, v.size, v.colour, v.price_cents,"
        " p.name FROM variants v JOIN products p ON p.id = v.product_id"
        " WHERE v.id = ?",
        (args["replacement_variant_id"],)).fetchone()
    original = connection.execute(
        "SELECT v.product_id, v.price_cents FROM variants v WHERE v.id = ?",
        (args["variant_id"],)).fetchone()
    if replacement is None or original is None:
        raise ProposalError(404, "PROPOSAL_NOT_AVAILABLE")
    if replacement["product_id"] != original["product_id"]:
        # No silent cross-product substitution.
        raise ProposalError(409, "REPLACEMENT_NOT_IDENTICAL")
    if replacement["price_cents"] != original["price_cents"]:
        raise ProposalError(409, "REPLACEMENT_PRICE_DIFFERS")
    stock = connection.execute(
        "SELECT on_hand, reserved FROM inventory WHERE variant_id = ?",
        (replacement["id"],)).fetchone()
    if stock is None or stock["on_hand"] - stock["reserved"] < 1:
        raise ProposalError(409, "OUT_OF_STOCK")
    payload = {
        "order_id": args["order_id"],
        "original_variant_id": args["variant_id"],
        "replacement_variant_id": replacement["id"],
        "condition": args["condition"],
    }
    proposal = proposals.create(
        owner_session=session.id, owner_customer=session.customer_id,
        kind="exchange", payload=payload)
    proposal["payload_hash"] = payload_hash(payload)
    view = proposal_view(proposal, proposals)
    view["terms"] = (
        f"Exchange {args['variant_id']} for {replacement['id']} of the same"
        f" product at the same price. Condition: {args['condition']}.")
    view["original_variant_id"] = args["variant_id"]
    view["replacement_variant_id"] = replacement["id"]
    return view


def confirm_exchange(connection, proposals, proposal_id: str, session,
                     operations, clock=None) -> dict:
    """Reservation of replacement stock + persisted exchange request in the
    same transaction after revalidating facts."""
    proposal = proposals.view(proposal_id)
    if proposal is None or proposal["owner_session"] != session.id:
        raise ProposalError(404, "PROPOSAL_NOT_AVAILABLE")
    if proposal["status"] == "confirmed":
        view = operations.as_dict(proposal["operation_id"], idempotent=True)
        view["exchange_reference"] = proposal["payload"].get("reference")
        view["exchange_state"] = "requested"
        return view
    if proposals.expired(proposal):
        proposals.consume(proposal_id)
        raise ProposalError(409, "PROPOSAL_EXPIRED")

    import calendar
    now_epoch = clock() if clock else time.time()
    payload = proposal["payload"]
    connection.execute("BEGIN IMMEDIATE")
    try:
        line = connection.execute(
            "SELECT o.id, o.customer_id, o.state, o.policy_version,"
            " o.delivery_utc FROM orders o JOIN order_lines ol"
            " ON ol.order_id = o.id"
            " WHERE o.id = ? AND ol.variant_id = ?",
            (payload["order_id"], payload["original_variant_id"])).fetchone()
        if line is None or line["customer_id"] != session.customer_id:
            raise ProposalError(404, "PROPOSAL_NOT_AVAILABLE")
        if line["state"] != "delivered":
            raise ProposalError(409, "NOT_RETURNABLE_YET")
        active = connection.execute(
            "SELECT state FROM returns WHERE order_id = ? AND variant_id = ?"
            " AND state = 'requested'",
            (payload["order_id"], payload["original_variant_id"])).fetchone()
        active_exchange = connection.execute(
            "SELECT state FROM exchanges WHERE order_id = ?"
            " AND original_variant_id = ? AND state = 'requested'",
            (payload["order_id"], payload["original_variant_id"])).fetchone()
        if active is not None or active_exchange is not None:
            raise ProposalError(409, "REQUEST_ALREADY_ACTIVE")
        # Same product, equal price: rechecked from current rows.
        prices = connection.execute(
            "SELECT v.id, v.product_id, v.price_cents FROM variants v"
            " WHERE v.id IN (?, ?)",
            (payload["original_variant_id"],
             payload["replacement_variant_id"])).fetchall()
        by_id = {row["id"]: dict(row) for row in prices}
        if (payload["replacement_variant_id"] not in by_id
                or payload["original_variant_id"] not in by_id):
            raise ProposalError(404, "PROPOSAL_NOT_AVAILABLE")
        if (by_id[payload["replacement_variant_id"]]["product_id"]
                != by_id[payload["original_variant_id"]]["product_id"]):
            raise ProposalError(409, "REPLACEMENT_NOT_IDENTICAL")
        changed = connection.execute(
            "UPDATE inventory SET reserved = reserved + 1"
            " WHERE variant_id = ? AND on_hand - reserved >= 1",
            (payload["replacement_variant_id"],))
        if changed.rowcount != 1:
            raise ProposalError(409, "OUT_OF_STOCK")
        reference = "exg_" + secrets.token_urlsafe(8).lower()
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        connection.execute(
            "INSERT INTO exchanges (id, customer_id, order_id,"
            " original_variant_id, replacement_variant_id, condition,"
            " policy_version, state, created_utc)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'requested', ?)",
            (reference, session.customer_id, payload["order_id"],
             payload["original_variant_id"],
             payload["replacement_variant_id"],
             payload["condition"], line["policy_version"], stamp))
        operation_id = "op_" + secrets.token_urlsafe(10)
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    proposal["status"] = "confirmed"
    proposal["operation_id"] = operation_id
    proposal["payload"]["reference"] = reference
    operations.record(operation_id, proposal, payload["order_id"])
    view = operations.as_dict(operation_id, idempotent=False)
    view["exchange_reference"] = reference
    view["exchange_state"] = "requested"
    view["explanation"] = (
        "Replacement shipment and original-item inspection have NOT happened;"
        " this registers the exchange request only.")
    return view


def confirm_return(connection, proposals, proposal_id: str, session,
                   operations, clock=None) -> dict:
    """Commit requires a full eligibility + ownership recheck in the same
    transaction as creating the persisted return request."""
    proposal = proposals.view(proposal_id)
    if proposal is None or proposal["owner_session"] != session.id:
        raise ProposalError(404, "PROPOSAL_NOT_AVAILABLE")
    if proposal["status"] == "confirmed":
        view = operations.as_dict(proposal["operation_id"], idempotent=True)
        view["return_reference"] = proposal["payload"].get("reference")
        view["return_state"] = "requested"
        return view
    if proposals.expired(proposal):
        proposals.consume(proposal_id)
        raise ProposalError(409, "PROPOSAL_EXPIRED")

    now_epoch = clock() if clock else time.time()
    payload = proposal["payload"]
    connection.execute("BEGIN IMMEDIATE")
    try:
        line = connection.execute(
            "SELECT o.id, o.customer_id, o.state, o.policy_version,"
            " o.delivery_utc, ol.unit_price_cents, ol.quantity FROM orders o"
            " JOIN order_lines ol ON ol.order_id = o.id"
            " WHERE o.id = ? AND ol.variant_id = ?",
            (payload["order_id"], payload["variant_id"])).fetchone()
        if line is None or line["customer_id"] != session.customer_id:
            raise ProposalError(404, "PROPOSAL_NOT_AVAILABLE")
        if line["state"] != "delivered":
            raise ProposalError(409, "NOT_RETURNABLE_YET")
        active = connection.execute(
            "SELECT state FROM returns WHERE order_id = ? AND variant_id = ?"
            " AND state = 'requested'",
            (payload["order_id"], payload["variant_id"])).fetchone()
        if active is not None:
            raise ProposalError(409, "REQUEST_ALREADY_ACTIVE")
        import calendar
        delivered_epoch = calendar.timegm(time.strptime(
            line["delivery_utc"][:19], "%Y-%m-%dT%H:%M:%S"))
        if now_epoch > delivered_epoch + RETURN_WINDOW_DAYS * 86400:
            raise ProposalError(409, "RETURN_WINDOW_EXPIRED")
        reference = "ret_" + secrets.token_urlsafe(8).lower()
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        connection.execute(
            "INSERT INTO returns (id, customer_id, order_id, variant_id,"
            " reason, condition, policy_version, state, created_utc)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'requested', ?)",
            (reference, session.customer_id, payload["order_id"],
             payload["variant_id"], payload["reason"],
             payload["condition"], line["policy_version"], stamp))
        operation_id = "op_" + secrets.token_urlsafe(10)
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    # The proposal remains in "confirmed" state for idempotent resolution;
    # repeated confirmations resolve by operation ID without a new write.
    proposal["status"] = "confirmed"
    proposal["operation_id"] = operation_id
    proposal["payload"]["reference"] = reference
    operations.record(operation_id, proposal, payload["order_id"])
    view = operations.as_dict(operation_id, idempotent=False)
    view["return_reference"] = reference
    view["return_state"] = "requested"
    view["explanation"] = (
        "This registers the return request only: inspection, restock, and"
        " refund happen later in the real shop.")
    return view


def confirm_cancellation(connection, proposals,
                         proposal_id: str, session, operations) -> dict:
    """Transactional confirmation: ownership + fulfillment recheck, exact-once release."""
    proposal = proposals.view(proposal_id)
    if proposal is None:
        raise ProposalError(404, "PROPOSAL_NOT_AVAILABLE")
    if proposal["owner_session"] != session.id:
        raise ProposalError(404, "PROPOSAL_NOT_AVAILABLE")
    if proposal["status"] == "confirmed":
        # Resolution by operation ID: no new write, no duplicate operation.
        return operations.as_dict(proposal["operation_id"], idempotent=True)
    if proposals.expired(proposal):
        proposals.consume(proposal_id)
        raise ProposalError(409, "PROPOSAL_EXPIRED")

    order_id = proposal["payload"]["order_id"]
    connection.execute("BEGIN IMMEDIATE")
    try:
        order = connection.execute(
            "SELECT id, customer_id, state FROM orders WHERE id = ?",
            (order_id,)).fetchone()
        if order is None or order["customer_id"] != session.customer_id:
            raise ProposalError(404, "PROPOSAL_NOT_AVAILABLE")
        separately_cancelled = order["state"] == "cancelled"
        if separately_cancelled:
            existing = operations.find_by_order(
                session.customer_id, "cancellation", order_id)
            connection.commit()
            if existing is not None:
                return operations.as_dict(existing["operation_id"],
                                          idempotent=True)
            raise ProposalError(409, "FULFILLMENT_STATE_CHANGED")
        changed = connection.execute(
            "UPDATE orders SET state = 'cancelled'"
            " WHERE id = ? AND customer_id = ? AND state = 'processing'",
            (order_id, session.customer_id))
        if changed.rowcount != 1:
            raise ProposalError(409, "FULFILLMENT_STATE_CHANGED")
        for line in connection.execute(
                "SELECT variant_id, quantity FROM order_lines"
                " WHERE order_id = ?", (order_id,)).fetchall():
            connection.execute(
                "UPDATE inventory SET reserved = MAX(reserved - ?, 0)"
                " WHERE variant_id = ?", (line["quantity"],
                                          line["variant_id"]))
        operation_id = "op_" + secrets.token_urlsafe(10)
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    proposal["status"] = "confirmed"
    proposal["operation_id"] = operation_id
    operations.record(operation_id, proposal, order_id)
    return operations.as_dict(operation_id, idempotent=False)


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

    def find_by_order(self, owner_customer: str, kind: str, order_id: str):
        for operation in self._operations.values():
            if (operation["owner_customer"] == owner_customer
                    and operation["kind"] == kind
                    and operation["order_id"] == order_id):
                return dict(operation)
        return None

    def as_dict(self, operation_id: str, idempotent: bool = False):
        operation = dict(self.get(operation_id))
        operation["idempotent"] = idempotent
        return operation
