import tempfile
import os
import threading
import unittest
import unittest.mock as mock

from fastapi.testclient import TestClient

from backend import actions, main
from backend.actions import (
    OperationStore,
    ProposalError,
    ProposalStore,
    confirm_exchange,
    create_exchange_proposal,
)
from backend.shop import connect as shop_connect, seed
from backend.sessions import Session, SessionManager
from backend.traces import TraceStore


def seeded_shop_connection():
    connection = shop_connect(":memory:")
    seed(connection)
    return connection


class ExchangeProposalTests(unittest.TestCase):
    def setUp(self):
        self.connection = seeded_shop_connection()
        self.store = ProposalStore(clock=lambda: 1000.0)
        self.operations = OperationStore()
        self.session = Session("session-test")
        self.session.customer_id = "demo_maya"
        # Same product (Summit Trail): olive -> coral, same price.
        self.valid_call = {"order_id": "order_maya_3",
                           "variant_id": "var_fjell_39",
                           "replacement_variant_id": "var_fjell_40",
                           "condition": "unworn"}

    def test_same_product_equal_price_is_eligible(self):
        proposal = create_exchange_proposal(
            self.connection, self.store, self.session, dict(self.valid_call),
            clock=lambda: 1780394400.0)
        self.assertEqual(proposal["kind"], "exchange")
        self.assertEqual(proposal["original_variant_id"], "var_fjell_39")
        self.assertEqual(proposal["replacement_variant_id"], "var_fjell_40")

    def test_cross_product_replacement_not_silently_substituted(self):
        # Fjell line -> Summit variant: different product.
        with self.assertRaises(ProposalError) as caught:
            create_exchange_proposal(
                self.connection, self.store, self.session,
                {**self.valid_call,
                 "replacement_variant_id": "var_summit_40"},
                clock=lambda: 1780394400.0)
        self.assertEqual(caught.exception.code, "REPLACEMENT_NOT_IDENTICAL")

    def test_foreign_or_unknown_line_not_disclosed(self):
        with self.assertRaises(ProposalError) as caught:
            create_exchange_proposal(
                self.connection, self.store, self.session,
                {**self.valid_call, "order_id": "order_leo_3"},
                clock=lambda: 1780394400.0)
        self.assertEqual(caught.exception.status_code, 404)
        with self.assertRaises(ProposalError) as caught:
            create_exchange_proposal(
                self.connection, self.store, self.session,
                {**self.valid_call, "variant_id": "var_unknown"},
                clock=lambda: 1780394400.0)
        self.assertEqual(caught.exception.status_code, 404)

    def test_unavailable_replacement_rejected(self):
        # Add a same-product variant with no stock at all.
        self.connection.execute(
            "INSERT INTO variants (id, product_id, size, colour, price_cents)"
            " VALUES ('var_fjell_36', 'prod_fjell', 36, 'grey', 15900)")
        self.connection.execute(
            "INSERT INTO inventory (variant_id, on_hand, reserved)"
            " VALUES ('var_fjell_36', 0, 0)")
        self.connection.commit()
        with self.assertRaises(ProposalError) as caught:
            create_exchange_proposal(
                self.connection, self.store, self.session,
                {"order_id": "order_maya_3", "variant_id": "var_fjell_39",
                 "replacement_variant_id": "var_fjell_36",
                 "condition": "unworn"},
                clock=lambda: 1780394400.0)
        self.assertEqual(caught.exception.code, "OUT_OF_STOCK")

    def test_conflicting_active_request_denied(self):
        self.connection.execute(
            "INSERT INTO returns (id, customer_id, order_id, variant_id,"
            " reason, condition, policy_version, state, created_utc)"
            " VALUES ('ret_active', 'demo_maya', 'order_maya_3',"
            " 'var_fjell_39', 'other', 'unworn', 'policy-2026-06-v1',"
            " 'requested', '2026-06-03T00:00:00+00:00')")
        self.connection.commit()
        with self.assertRaises(ProposalError) as caught:
            create_exchange_proposal(
                self.connection, self.store, self.session,
                dict(self.valid_call), clock=lambda: 1780394400.0)
        self.assertEqual(caught.exception.code, "REQUEST_ALREADY_ACTIVE")


class ExchangeConfirmTests(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as directory:
            pass  # keep semantics simple: file-based shared DB per test
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.shop_path = os.path.join(self.directory.name, "shop.db")
        fresh = shop_connect(self.shop_path)
        seed(fresh)
        self.connection = self.file_connection()
        self.store = ProposalStore(clock=lambda: 1000.0)
        self.operations = OperationStore()
        self.session = Session("session-maya")
        self.session.customer_id = "demo_maya"
        self.proposal = create_exchange_proposal(
            self.connection, self.store, self.session,
            {"order_id": "order_maya_3", "variant_id": "var_fjell_39",
             "replacement_variant_id": "var_fjell_40",
             "condition": "unworn"},
            clock=lambda: 1780394400.0)

    def file_connection(self):
        return shop_connect(self.shop_path)

    def test_confirm_reserves_replacement_and_persists_request(self):
        result = confirm_exchange(
            self.connection, self.store, self.proposal["proposal_id"],
            self.session, self.operations, clock=lambda: 1780394500.0)
        self.assertTrue(result["exchange_reference"].startswith("exg_"))
        self.assertEqual(result["exchange_state"], "requested")
        self.assertIn("have NOT happened", result["explanation"])
        reader = shop_connect(self.shop_path)
        stock = reader.execute(
            "SELECT reserved FROM inventory WHERE variant_id = 'var_fjell_40'"
        ).fetchone()[0]
        self.assertEqual(stock, 1)  # seed 0 reserved + exchange 1
        request = reader.execute(
            "SELECT state FROM exchanges WHERE id = ?",
            (result["exchange_reference"],)).fetchone()
        self.assertEqual(request["state"], "requested")

    def test_repeat_confirmation_is_idempotent(self):
        first = confirm_exchange(
            self.connection, self.store, self.proposal["proposal_id"],
            self.session, self.operations, clock=lambda: 1780394500.0)
        repeat = confirm_exchange(
            self.connection, self.store, self.proposal["proposal_id"],
            self.session, self.operations, clock=lambda: 1780394600.0)
        self.assertTrue(repeat["idempotent"])
        self.assertEqual(repeat["exchange_reference"],
                         first["exchange_reference"])
        reader = shop_connect(self.shop_path)
        self.assertEqual(reader.execute(
            "SELECT reserved FROM inventory WHERE variant_id = 'var_fjell_40'"
        ).fetchone()[0], 1)  # no double reservation

    def test_competing_exchanges_cannot_over_reserve(self):
        # Only 1 replacement unit available: two confirmations race.
        self.connection.execute(
            "UPDATE inventory SET on_hand = 1 WHERE variant_id = 'var_fjell_40'")
        self.connection.commit()
        second_store = ProposalStore(clock=lambda: 1000.0)
        second_session = Session("session-leo")
        second_session.customer_id = "demo_leo"
        # Give leo a delivered order line for the same product variant.
        self.connection.execute(
            "UPDATE orders SET customer_id = 'demo_leo', state = 'delivered'"
            " WHERE id = 'order_maya_4'")
        self.connection.commit()
        self.connection.execute(
            "UPDATE order_lines SET variant_id = 'var_fjell_39'"
            " WHERE order_id = 'order_maya_4'")
        self.connection.commit()
        second_proposal = create_exchange_proposal(
            self.connection, second_store, second_session,
            {"order_id": "order_maya_4", "variant_id": "var_fjell_39",
             "replacement_variant_id": "var_fjell_40",
             "condition": "unworn"},
            clock=lambda: 1780394400.0)
        results = []
        barrier = threading.Barrier(2)

        def worker(store, proposal_id, session):
            connection = self.file_connection()
            barrier.wait()
            try:
                result = confirm_exchange(
                    connection, store, proposal_id, session, self.operations)
                results.append(("accepted", result["exchange_reference"]))
            except ProposalError as failure:
                results.append(("rejected", failure.code))
            finally:
                connection.close()

        threads = [
            threading.Thread(target=worker, args=(
                self.store, self.proposal["proposal_id"], self.session)),
            threading.Thread(target=worker, args=(
                second_store, second_proposal["proposal_id"],
                second_session)),
        ]
        for worker_thread in threads:
            worker_thread.start()
        for worker_thread in threads:
            worker_thread.join(10)
        accepted = [entry for entry in results if entry[0] == "accepted"]
        rejected = [entry for entry in results if entry[0] == "rejected"]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0][1], "OUT_OF_STOCK")
        reader = shop_connect(self.shop_path)
        self.assertEqual(reader.execute(
            "SELECT reserved FROM inventory WHERE variant_id = 'var_fjell_40'"
        ).fetchone()[0], 1)
        reader.close()


if __name__ == "__main__":
    unittest.main()
