import unittest

from backend import actions
from backend.actions import (
    OperationStore,
    ProposalError,
    ProposalStore,
    confirm_return,
    create_return_proposal,
)
from backend.sessions import Session
from backend.shop import connect as shop_connect, seed


def seeded_shop_connection():
    connection = shop_connect(":memory:")
    seed(connection)
    return connection


class ReturnProposalTests(unittest.TestCase):
    def setUp(self):
        self.connection = seeded_shop_connection()
        self.store = ProposalStore(clock=lambda: 1000.0)
        self.operations = OperationStore()
        self.session = Session("session-test")
        self.session.customer_id = "demo_maya"

    def valid_call(self, order_id="order_maya_3", variant_id="var_fjell_39"):
        return {"order_id": order_id, "variant_id": variant_id,
                "reason": "does_not_fit", "condition": "unworn"}

    def test_owned_delivered_line_is_eligible_with_policy(self):
        proposal = create_return_proposal(
            self.connection, self.store, self.session, self.valid_call(),
            clock=lambda: 1780394400.0)
        self.assertEqual(proposal["kind"], "return")
        self.assertIn("policy-2026-06-v1", proposal["terms"])

    def test_foreign_or_unknown_line_not_disclosed(self):
        bad_lines = [("order_leo_3", "var_fjell_39"),
                     ("order_none", "var_fjell_39"),
                     ("order_maya_3", "var_unknown")]
        for order_id, variant_id in bad_lines:
            with self.subTest(order_id=order_id, variant_id=variant_id):
                with self.assertRaises(ProposalError) as caught:
                    create_return_proposal(
                        self.connection, self.store, self.session,
                        self.valid_call(order_id, variant_id),
                        clock=lambda: 1780394400.0)
                self.assertEqual(caught.exception.status_code, 404)

    def test_reason_and_condition_whitelist_enforced(self):
        with self.assertRaises(ProposalError) as caught:
            create_return_proposal(
                self.connection, self.store, self.session,
                {**self.valid_call(), "reason": "spooky"},
                clock=lambda: 1780394400.0)
        self.assertEqual(caught.exception.code, "REASON_NOT_ACCEPTED")
        with self.assertRaises(ProposalError) as caught:
            create_return_proposal(
                self.connection, self.store, self.session,
                {**self.valid_call(), "condition": "shredded"},
                clock=lambda: 1780394400.0)
        self.assertEqual(caught.exception.code, "CONDITION_NOT_ACCEPTED")

    def test_processing_and_shipped_orders_not_returnable(self):
        with self.assertRaises(ProposalError) as caught:
            create_return_proposal(
                self.connection, self.store, self.session,
                self.valid_call(order_id="order_maya_1",
                                variant_id="var_summit_39"),
                clock=lambda: 1780394400.0)
        self.assertEqual(caught.exception.code, "NOT_RETURNABLE_YET")
        # No state change from the denial.
        self.assertEqual(self.connection.execute(
            "SELECT state FROM orders WHERE id = 'order_maya_1'"
        ).fetchone()[0], "processing")


class ReturnWindowTests(unittest.TestCase):
    def setUp(self):
        self.connection = seeded_shop_connection()
        self.store = ProposalStore(clock=lambda: 1000.0)
        self.session = Session("session-test")
        self.session.customer_id = "demo_maya"
        # order_maya_3 delivered 2026-06-02T10:00:00+00:00
        # Epoch for that stamp (UTC).
        import calendar
        import time
        self.delivered_epoch = calendar.timegm(
            time.strptime("2026-06-02T10:00:00", "%Y-%m-%dT%H:%M:%S"))

    def call(self):
        return {"order_id": "order_maya_3", "variant_id": "var_fjell_39",
                "reason": "does_not_fit", "condition": "unworn"}

    def test_boundary_inside_window_accepted(self):
        inside = self.delivered_epoch + 30 * 86400 - 1
        proposal = create_return_proposal(
            self.connection, self.store, self.session, self.call(),
            clock=lambda: inside)
        self.assertEqual(proposal["kind"], "return")

    def test_boundary_after_window_denied_with_nothing_changed(self):
        expired = self.delivered_epoch + 30 * 86400 + 1
        with self.assertRaises(ProposalError) as caught:
            create_return_proposal(
                self.connection, self.store, self.session, self.call(),
                clock=lambda: expired)
        self.assertEqual(caught.exception.code, "RETURN_WINDOW_EXPIRED")
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM returns").fetchone()[0], 0)


class ReturnConfirmTests(unittest.TestCase):
    def setUp(self):
        self.connection = seeded_shop_connection()
        self.store = ProposalStore(clock=lambda: 1000.0)
        self.operations = OperationStore()
        self.session = Session("session-test")
        self.session.customer_id = "demo_maya"
        self.clock = [1780394400.0]
        self.proposal = create_return_proposal(
            self.connection, self.store, self.session,
            {"order_id": "order_maya_3", "variant_id": "var_fjell_39",
             "reason": "does_not_fit", "condition": "unworn"},
            clock=lambda: self.clock[0])

    def test_confirm_creates_persisted_request_with_reference(self):
        result = confirm_return(
            self.connection, self.store, self.proposal["proposal_id"],
            self.session, self.operations,
            clock=lambda: self.clock[0] + 1)
        self.assertTrue(result["return_reference"].startswith("ret_"))
        self.assertEqual(result["return_state"], "requested")
        self.assertIn("inspection", result["explanation"])
        reader = self.connection.execute(
            "SELECT state, policy_version FROM returns WHERE id = ?",
            (result["return_reference"],)).fetchone()
        self.assertEqual(reader["state"], "requested")
        self.assertEqual(reader["policy_version"], "policy-2026-06-v1")

    def test_repeat_confirmation_is_idempotent(self):
        first = confirm_return(
            self.connection, self.store, self.proposal["proposal_id"],
            self.session, self.operations, clock=lambda: self.clock[0] + 1)
        repeat = confirm_return(
            self.connection, self.store, self.proposal["proposal_id"],
            self.session, self.operations,
            clock=lambda: self.clock[0] + 2)
        self.assertTrue(repeat["idempotent"])
        self.assertEqual(repeat["return_reference"],
                         first["return_reference"])
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM returns").fetchone()[0], 1)

    def test_commit_rechecks_state_in_transaction(self):
        # The order became non-delivered between proposal and confirmation.
        self.connection.execute(
            "UPDATE orders SET state = 'shipped' WHERE id = 'order_maya_3'")
        self.connection.commit()
        with self.assertRaises(ProposalError) as caught:
            confirm_return(
                self.connection, self.store, self.proposal["proposal_id"],
                self.session, self.operations,
                clock=lambda: self.clock[0] + 1)
        self.assertEqual(caught.exception.code, "NOT_RETURNABLE_YET")
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM returns").fetchone()[0], 0)

    def test_second_active_return_conflict(self):
        # One active request already exists for this line.
        self.connection.execute(
            "INSERT INTO returns (id, customer_id, order_id, variant_id,"
            " reason, condition, policy_version, state, created_utc)"
            " VALUES ('ret_existing', 'demo_maya', 'order_maya_3',"
            " 'var_fjell_39', 'other', 'unworn', 'policy-2026-06-v1',"
            " 'requested', '2026-06-03T00:00:00+00:00')")
        self.connection.commit()
        with self.assertRaises(ProposalError) as caught:
            create_return_proposal(
                self.connection, self.store, self.session,
                {"order_id": "order_maya_3", "variant_id": "var_fjell_39",
                 "reason": "other", "condition": "worn_once"},
                clock=lambda: self.clock[0])
        self.assertEqual(caught.exception.code, "REQUEST_ALREADY_ACTIVE")
        # Still exactly one active request, and no second proposal created.
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM returns WHERE state = 'requested'"
        ).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
