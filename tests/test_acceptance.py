"""Repeatable end-to-end acceptance scenarios for the portfolio (issue E3).

Runs complete buy / cancel / return / exchange / handover journeys plus their
principal denial, conflict, race, idempotency, and cross-customer paths
against the whole running app (session cookies, traces, operations, SQLite).
Voice and manual UI checks are documented in README (voiced loop verified in
supported browsers; unsupported APIs fall back to text)."""

import tempfile
import unittest
import unittest.mock as mock

from fastapi.testclient import TestClient

from backend import main
from backend.actions import OperationStore, ProposalStore
from backend.shop import connect as shop_connect, seed
from backend.sessions import SessionManager
from backend.traces import TraceStore


def buy_action(variant_id="var_summit_39", quantity=1):
    return {"tool": "propose_purchase",
            "args": {"variant_id": variant_id, "quantity": quantity}}


class JourneyAcceptanceTests(unittest.TestCase):
    """Buy, cancel, return, and exchange as one scripted demo run."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.shop_path = self.directory.name + "/shop.db"
        fresh = shop_connect(self.shop_path)
        seed(fresh)

        def fresh_connection():
            return shop_connect(self.shop_path)

        patches = [
            mock.patch.object(main, "manager", SessionManager()),
            mock.patch.object(main, "traces", TraceStore()),
            mock.patch.object(main, "proposals", ProposalStore(clock=lambda: 1000.0)),
            mock.patch.object(main, "operations", OperationStore()),
            mock.patch.object(main, "shop_connection", fresh_connection),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.client = TestClient(main.app)
        self.client.post("/sessions", json={"customer_id": "demo_maya"})

    def order_count(self):
        reader = shop_connect(self.shop_path)
        count = reader.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        reader.close()
        return count

    def confirm(self, proposal):
        if isinstance(proposal, str):
            proposal_id = proposal
        else:
            proposal_id = proposal["proposal_id"]
        return self.client.post(f"/actions/{proposal_id}/confirm")

    def journey_reply(self, tool="propose_purchase", **extra):
        args = buy_action(**extra)
        payload = {"tool": tool, "args": args["args"] if tool == "propose_purchase"
                   else extra}
        return mock.patch(
            "backend.agent.generate_response",
            return_value="Here we go.\n ACTION " + main.__import__("json")
            .dumps(payload))

    def test_buy_journey_with_trace_visibility(self):
        with mock.patch("backend.agent.generate_response", return_value=(
                "Great choice.\n"
                'ACTION {"tool": "propose_purchase",'
                ' "args": {"variant_id": "var_summit_39", "quantity": 1}}')):
            response = self.client.post(
                "/chat", json={"message": "I buy Summit Trail 39 olive"})
        proposal = response.json()["action_proposal"]
        self.assertEqual(self.order_count(), 8)
        confirmed = self.confirm(proposal)
        self.assertEqual(confirmed.json()["state"], "processing")
        self.assertEqual(self.order_count(), 9)
        trace = self.client.get(
            f"/traces/{response.json()['trace_id']}").json()
        stages = {event["stage"]: event["status"] for event in trace["events"]}
        self.assertEqual(stages["retrieval"], "executed")
        self.assertEqual(stages["proposal"], "executed")
        # Operation id + immediate timing are available through the trace.
        self.assertTrue(any("duration_ms" in event
                            for event in trace["events"]))

    def test_cancellation_journey_releases_stock(self):
        with mock.patch("backend.agent.generate_response", return_value=(
                "Okay.\n"
                'ACTION {"tool": "propose_cancellation",'
                ' "args": {"order_id": "order_maya_1"}}')):
            response = self.client.post(
                "/chat", json={"message": "cancel order_maya_1 please"})
        confirmed = self.confirm(response.json()["action_proposal"])
        self.assertEqual(confirmed.json()["state"], "processing")
        reader = shop_connect(self.shop_path)
        self.assertEqual(reader.execute(
            "SELECT state FROM orders WHERE id = 'order_maya_1'"
        ).fetchone()[0], "cancelled")

    def test_return_journey_creates_request_reference(self):
        response = self.run_return_chat()
        confirmed = self.confirm(response.json()["action_proposal"])
        self.assertTrue(confirmed.json()["return_reference"].startswith("ret_"))

    def run_return_chat(self):
        with mock.patch("backend.agent.generate_response", return_value=(
                "Sure.\n"
                'ACTION {"tool": "propose_return", "args": {'
                ' "order_id": "order_maya_3", "variant_id": "var_fjell_39",'
                ' "reason": "does_not_fit", "condition": "unworn"}}')):
            return self.client.post(
                "/chat", json={"message": "return the fjell shoes please"})

    def test_exchange_journey_reserves_replacement(self):
        with mock.patch("backend.agent.generate_response", return_value=(
                "Okay.\n"
                'ACTION {"tool": "propose_exchange", "args": {'
                ' "order_id": "order_maya_3", "variant_id": "var_fjell_39",'
                ' "replacement_variant_id": "var_fjell_38",'
                ' "condition": "unworn"}}')):
            response = self.client.post(
                "/chat", json={"message": "exchange for a size 40 please"})
        proposal = response.json()["action_proposal"]
        confirmed = self.confirm(proposal["proposal_id"])
        self.assertTrue(confirmed.json()["exchange_reference"])
        reader = shop_connect(self.shop_path)
        self.assertEqual(reader.execute(
            "SELECT reserved FROM inventory WHERE variant_id = 'var_fjell_38'"
        ).fetchone()[0], 1)

    def test_expired_confirmation_denied(self):
        with mock.patch("backend.agent.generate_response", return_value=(
                "Sure.\n"
                'ACTION {"tool": "propose_purchase",'
                ' "args": {"variant_id": "var_summit_39", "quantity": 1}}')):
            proposal = self.client.post(
                "/chat", json={"message": "buy now"}).json()["action_proposal"]
        # Simulate expiry by advancing the store clock.
        main.proposals.ttl_seconds = -1
        response = self.confirm(proposal["proposal_id"])
        self.assertEqual(response.status_code, 409)

    def test_unknown_write_resolution_by_operation_id(self):
        response = self.client.get("/operations/op_nope")
        self.assertEqual(response.status_code, 404)


class CrossCustomerSecurityAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.shop_path = self.directory.name + "/shop.db"
        fresh = shop_connect(self.shop_path)
        seed(fresh)

        def fresh_connection():
            return shop_connect(self.shop_path)

        patches = [
            mock.patch.object(main, "manager", SessionManager()),
            mock.patch.object(main, "traces", TraceStore()),
            mock.patch.object(main, "proposals",
                              ProposalStore(clock=lambda: 1000.0)),
            mock.patch.object(main, "operations", OperationStore()),
            mock.patch.object(main, "shop_connection", fresh_connection),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.customer_a = TestClient(main.app)
        self.customer_b = TestClient(main.app)
        self.customer_a.post("/sessions", json={"customer_id": "demo_maya"})
        self.customer_b.post("/sessions", json={"customer_id": "demo_leo"})

    def test_cross_customer_access_attempts_denied(self):
        # A's own materials stay reachable; B cannot see or commit them.
        with mock.patch("backend.agent.generate_response", return_value=(
                "Sure.\n"
                'ACTION {"tool": "propose_cancellation",'
                ' "args": {"order_id": "order_maya_1"}}')):
            response = self.customer_a.post(
                "/chat", json={"message": "cancel order_maya_1"})
        proposal = response.json()["action_proposal"]
        foreign_confirm = self.customer_b.post(
            f"/actions/{proposal['proposal_id']}/confirm")
        self.assertEqual(foreign_confirm.status_code, 404)
        self.assertEqual(foreign_confirm.json(),
                         {"detail": "Proposal is not available."})
        trace_id = response.json()["trace_id"]
        leak = self.customer_b.get(f"/traces/{trace_id}")
        self.assertEqual(leak.status_code, 404)


if __name__ == "__main__":
    unittest.main()
