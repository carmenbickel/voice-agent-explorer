import tempfile
import unittest
import unittest.mock as mock

from fastapi.testclient import TestClient

from backend import actions, main
from backend.actions import (
    OperationStore,
    ProposalError,
    ProposalStore,
    confirm_handover,
    create_handover_proposal,
)
from backend.shop import connect as shop_connect, seed
from backend.sessions import Session, SessionManager
from backend.traces import TraceStore


def seeded_shop_connection():
    connection = shop_connect(":memory:")
    seed(connection)
    return connection


class HandoverTests(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as directory:
            pass
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.shop_path = self.directory.name + "/shop.db"
        fresh = shop_connect(self.shop_path)
        seed(fresh)
        self.connection = shop_connect(self.shop_path)
        self.store = ProposalStore(clock=lambda: 1000.0)
        self.operations = OperationStore()
        self.session = Session("session-test")
        self.session.customer_id = "demo_maya"

    def test_request_creates_proposal_not_automatic_ticket(self):
        proposal = create_handover_proposal(
            self.connection, self.store, self.session,
            {"unresolved_issue": "Delivery seems stuck for my shoes"})
        self.assertEqual(proposal["kind"], "handover")
        self.assertEqual(proposal["status"], "proposed")
        reader = shop_connect(self.shop_path)
        self.assertEqual(reader.execute(
            "SELECT COUNT(*) FROM support_tickets").fetchone()[0], 0)
        self.assertIn("no email", proposal["terms"].lower())

    def test_confirmation_creates_exactly_one_ticket_with_reference(self):
        proposal = create_handover_proposal(
            self.connection, self.store, self.session,
            {"unresolved_issue": "Complex refund dispute"})
        result = confirm_handover(
            self.connection, self.store, proposal["proposal_id"],
            self.session, self.operations)
        reference = result["ticket_reference"]
        self.assertTrue(reference.startswith("tkt_"))
        self.assertEqual(result["ticket_state"], "requested")
        reader = shop_connect(self.shop_path)
        count = reader.execute(
            "SELECT COUNT(*) FROM support_tickets").fetchone()[0]
        self.assertEqual(count, 1)
        repeat = confirm_handover(
            self.connection, self.store, proposal["proposal_id"],
            self.session, self.operations)
        self.assertTrue(repeat["idempotent"])
        self.assertEqual(repeat["ticket_reference"], reference)
        self.assertEqual(reader.execute(
            "SELECT COUNT(*) FROM support_tickets").fetchone()[0], 1)

    def test_other_session_cannot_confirm(self):
        proposal = create_handover_proposal(
            self.connection, self.store, self.session,
            {"unresolved_issue": "Dispute"})
        stranger = Session("session-other")
        with self.assertRaises(ProposalError) as caught:
            confirm_handover(
                self.connection, self.store, proposal["proposal_id"],
                stranger, self.operations)
        self.assertEqual(caught.exception.status_code, 404)

    def test_no_promises_in_ticket_copy(self):
        proposal = create_handover_proposal(
            self.connection, self.store, self.session,
            {"unresolved_issue": "Anything at all"})
        for forbidden in ("email", "callback", "live transfer", "refund",
                          "fulfillment"):
            self.assertNotIn(f"we will {forbidden}", proposal["terms"].lower())


class HandoverApiTests(unittest.TestCase):
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
        self.client = TestClient(main.app)
        self.client.post("/sessions", json={"customer_id": "demo_maya"})

    @mock.patch("backend.agent.generate_response", return_value=(
        "Let me hand this over.\n"
        'ACTION {"tool": "propose_handover",'
        ' "args": {"unresolved_issue": "Charge disagreement on a returned line"}}'))
    def test_api_handover_flow(self, generate):
        response = self.client.post(
            "/chat", json={"message": "I want to speak to a person about my order"})
        proposal = response.json()["action_proposal"]
        self.assertEqual(proposal["kind"], "handover")
        confirm = self.client.post(
            f"/actions/{proposal['proposal_id']}/confirm")
        self.assertEqual(confirm.status_code, 200)
        body = confirm.json()
        self.assertTrue(body["ticket_reference"].startswith("tkt_"))
        reader = shop_connect(self.shop_path)
        ticket = reader.execute(
            "SELECT customer_id, unresolved_issue FROM support_tickets"
            " WHERE id = ?", (body["ticket_reference"],)).fetchone()
        self.assertEqual(ticket["customer_id"], "demo_maya")
        self.assertIn("disagreement", ticket["unresolved_issue"].lower())

    @mock.patch("backend.agent.generate_response", return_value="plain answer")
    def test_ticket_lookup_is_scoped(self, generate):
        main.operations.record("op_x", {
            "owner_customer": "demo_leo", "kind": "handover",
            "payload": {}}, "issue-for-leo")
        response = self.client.get("/operations/op_x")
        # A different customer cannot reach another customer's ticket data.
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
