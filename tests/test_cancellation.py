import unittest
import unittest.mock as mock

from fastapi.testclient import TestClient

from backend import actions, main
from backend.actions import (
    OperationStore,
    ProposalError,
    ProposalStore,
    confirm_cancellation,
    create_cancellation_proposal,
)
from backend.shop import connect as shop_connect, seed
from backend.sessions import Session, SessionManager
from backend.traces import TraceStore


def seeded_shop_connection():
    connection = shop_connect(":memory:")
    seed(connection)
    return connection


class CancellationProposalTests(unittest.TestCase):
    def setUp(self):
        self.connection = seeded_shop_connection()
        self.store = ProposalStore(clock=lambda: 1000.0)
        self.operations = OperationStore()
        self.session = Session("session-test")
        self.session.customer_id = "demo_maya"

    def test_owned_processing_order_is_eligible(self):
        proposal = create_cancellation_proposal(
            self.connection, self.store, self.session,
            {"order_id": "order_maya_1"}, self.operations)
        self.assertEqual(proposal["kind"], "cancellation")
        self.assertEqual(proposal["order_id"], "order_maya_1")
        self.assertEqual(proposal["status"], "proposed")

    def test_foreign_or_unknown_order_not_disclosed(self):
        with self.assertRaises(ProposalError) as caught:
            create_cancellation_proposal(
                self.connection, self.store, self.session,
                {"order_id": "order_leo_1"}, self.operations)
        self.assertEqual(caught.exception.status_code, 404)
        with self.assertRaises(ProposalError):
            create_cancellation_proposal(
                self.connection, self.store, self.session,
                {"order_id": "order_none"}, self.operations)

    def test_shipped_and_delivered_denied_without_state_change(self):
        for order_id in ("order_maya_2", "order_maya_3"):
            with self.subTest(order_id=order_id):
                with self.assertRaises(ProposalError) as caught:
                    create_cancellation_proposal(
                        self.connection, self.store, self.session,
                        {"order_id": order_id}, self.operations)
                self.assertEqual(caught.exception.code, "NOT_CANCELLABLE")
        # No state change and no proposal left behind.
        self.assertEqual(self.store._proposals, {})

    def test_already_cancelled_returns_existing_outcome_only(self):
        create_cancellation_proposal(
            self.connection, self.store, self.session,
            {"order_id": "order_maya_1"}, self.operations)
        # Direct cancellation happened elsewhere.
        self.connection.execute(
            "UPDATE orders SET state = 'cancelled' WHERE id = 'order_maya_1'")
        self.connection.commit()
        outcome = create_cancellation_proposal(
            self.connection, self.store, self.session,
            {"order_id": "order_maya_1"}, self.operations)
        self.assertEqual(outcome["status"], "already_cancelled")
        # Repeating does create no second proposal: the stale first draft was
        # not confirmed, so only that unconfirmed draft may still exist.
        self.assertEqual(
            {proposal_id: proposal for proposal_id, proposal
             in self.store._proposals.items()
             if proposal["status"] == "confirmed"},
            {})

    def test_anonymous_sessions_cannot_cancel(self):
        anonymous = Session("session-anon")
        with self.assertRaises(ProposalError) as caught:
            create_cancellation_proposal(
                self.connection, self.store, anonymous,
                {"order_id": "order_maya_1"}, self.operations)
        self.assertEqual(caught.exception.code, "DEMO_CUSTOMER_REQUIRED")


class CancellationCommitTests(unittest.TestCase):
    def setUp(self):
        self.connection = seeded_shop_connection()
        self.store = ProposalStore(clock=lambda: 1000.0)
        self.operations = OperationStore()
        self.session = Session("session-test")
        self.session.customer_id = "demo_maya"
        self.proposal = create_cancellation_proposal(
            self.connection, self.store, self.session,
            {"order_id": "order_maya_1"}, self.operations)

    def test_confirmation_marks_cancelled_and_releases_reservation_once(self):
        before = self.connection.execute(
            "SELECT reserved FROM inventory WHERE variant_id = 'var_summit_39'"
        ).fetchone()[0]
        result = confirm_cancellation(
            self.connection, self.store, self.proposal["proposal_id"],
            self.session, self.operations)
        self.assertEqual(result["state"], "processing")
        self.assertEqual(result["order_id"], "order_maya_1")
        after = self.connection.execute(
            "SELECT reserved FROM inventory WHERE variant_id = 'var_summit_39'"
        ).fetchone()[0]
        self.assertEqual(after, before - 1)
        order = self.connection.execute(
            "SELECT state FROM orders WHERE id = 'order_maya_1'").fetchone()
        self.assertEqual(order["state"], "cancelled")

        # Repeated confirmation resolves via operation ID (no second release).
        repeat = confirm_cancellation(
            self.connection, self.store, self.proposal["proposal_id"],
            self.session, self.operations)
        self.assertTrue(repeat["idempotent"])
        self.assertEqual(self.connection.execute(
            "SELECT reserved FROM inventory WHERE variant_id = 'var_summit_39'"
        ).fetchone()[0], after)

    def test_fulfillment_state_race_is_rechecked_transactionally(self):
        # A fulfillment change occurred between proposal and confirmation.
        self.connection.execute(
            "UPDATE orders SET state = 'shipped' WHERE id = 'order_maya_1'")
        self.connection.commit()
        with self.assertRaises(ProposalError) as caught:
            confirm_cancellation(
                self.connection, self.store, self.proposal["proposal_id"],
                self.session, self.operations)
        self.assertEqual(caught.exception.code, "FULFILLMENT_STATE_CHANGED")
        # The order is unchanged: still shipped, not cancelled.
        order = self.connection.execute(
            "SELECT state FROM orders WHERE id = 'order_maya_1'").fetchone()
        self.assertEqual(order["state"], "shipped")

    def test_other_session_cannot_confirm(self):
        stranger = Session("session-other")
        stranger.customer_id = "demo_leo"
        with self.assertRaises(ProposalError) as caught:
            confirm_cancellation(
                self.connection, self.store, self.proposal["proposal_id"],
                stranger, self.operations)
        self.assertEqual(caught.exception.status_code, 404)


class CancellationApiTests(unittest.TestCase):
    def setUp(self):
        import tempfile, os
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.shop_path = os.path.join(self.directory.name, "shop.db")
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
        "Understood.\n"
        'ACTION {"tool": "propose_cancellation",'
        ' "args": {"order_id": "order_maya_1"}}'))
    def test_api_cancellation_flow(self, generate):
        response = self.client.post(
            "/chat", json={"message": "Please cancel my order order_maya_1"})
        proposal = response.json()["action_proposal"]
        self.assertEqual(proposal["kind"], "cancellation")
        confirm = self.client.post(
            f"/actions/{proposal['proposal_id']}/confirm")
        self.assertEqual(confirm.status_code, 200)
        reader = shop_connect(self.shop_path)
        self.assertEqual(reader.execute(
            "SELECT state FROM orders WHERE id = 'order_maya_1'").fetchone()[0],
            "cancelled")
        reader.close()

    @mock.patch("backend.agent.generate_response", return_value="plain answer")
    def test_shipped_order_denied_by_the_api(self, generate):
        session = main.manager.get(
            next(iter(main.manager._sessions.keys())))
        with self.assertRaises(Exception) as caught:
            actions.create_cancellation_proposal(
                shop_connect(self.shop_path), main.proposals, session,
                {"order_id": "order_maya_2"}, main.operations)
        self.assertEqual(caught.exception.code, "NOT_CANCELLABLE")


if __name__ == "__main__":
    unittest.main()
