import os
import tempfile
import threading
import unittest
import unittest.mock as mock

from fastapi.testclient import TestClient

from backend import actions, main
from backend.actions import (
    OperationStore,
    ProposalError,
    ProposalStore,
    confirm_purchase,
    create_purchase_proposal,
)
from backend.shop import connect as shop_connect, seed
from backend.sessions import Session, SessionManager
from backend.tools import ToolError, parse_action_line, validate_args
from backend.traces import TraceStore


def seeded_shop_connection():
    connection = shop_connect(":memory:")
    seed(connection)
    return connection


class ToolValidationTests(unittest.TestCase):
    def setUp(self):
        self.connection = seeded_shop_connection()

    def test_unknown_tool_rejected(self):
        with self.assertRaises(ToolError):
            validate_args("delete_everything", {})

    def test_unknown_tool_dispatch_rejected(self):
        from backend.tools import dispatch
        with self.assertRaises(ToolError):
            dispatch("run_arbitrary_sql", {}, self.connection)

    def test_invalid_typed_arguments_rejected(self):
        with self.assertRaises(ToolError):
            validate_args("propose_purchase",
                          {"variant_id": "var_summit_39", "quantity": "2"})
        with self.assertRaises(ToolError):
            validate_args("propose_purchase", {"variant_id": "var_summit_39"})
        with self.assertRaises(ToolError):
            validate_args("propose_purchase",
                          {"variant_id": "var_summit_39", "quantity": -1})

    def test_stock_lookup_uses_sqlite_rows(self):
        from backend.tools import dispatch
        result = dispatch("get_variant_stock",
                         {"variant_id": "var_summit_39"}, self.connection)
        self.assertEqual(result["variant"]["name"], "Summit Trail")
        self.assertEqual(result["variant"]["available"], 1)

    def test_search_asks_clarification_when_ambiguous(self):
        from backend.tools import search_products
        result = search_products(self.connection, "shoes")
        self.assertEqual(result["status"], "clarify")
        result = search_products(self.connection, "nonexistent wedding dress")
        self.assertEqual(result["status"], "clarify")

    def test_search_reads_current_sqlite_rows(self):
        from backend.tools import search_products
        result = search_products(self.connection, "summit olive")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(all(
            row["name"] == "Summit Trail" for row in result["products"]))


class ActionParsingTests(unittest.TestCase):
    def test_malformed_output_yields_no_action(self):
        self.assertIsNone(parse_action_line("Just a normal answer"))
        malformed = parse_action_line("ACTION {broken json")
        self.assertEqual(malformed["error_code"], "MALFORMED_ACTION")
        action = parse_action_line('ACTION {"tool": 42, "args": {}}')
        self.assertEqual(action["error_code"], "MALFORMED_ACTION")
    def test_multiple_actions_rejected_as_loop(self):
        action = parse_action_line(
            'ACTION {"tool": "get_variant_stock", "args": {"variant_id": "a"}}\n'
            'ACTION {"tool": "get_variant_stock", "args": {"variant_id": "b"}}')
        self.assertEqual(action["error_code"], "TOOL_LOOP_EXCEEDED")

    def test_valid_single_action_parses(self):
        action = parse_action_line(
            'Sure.\nACTION {"tool": "propose_purchase",'
            ' "args": {"variant_id": "var_summit_39", "quantity": 1}}')
        self.assertEqual(action["error_code"], None)
        self.assertEqual(action["args"]["quantity"], 1)


class ProposalCreationTests(unittest.TestCase):
    def setUp(self):
        self.connection = seeded_shop_connection()
        self.store = ProposalStore(clock=lambda: 1000.0)
        self.session = Session("session-test")
        self.session.customer_id = "demo_maya"

    def test_anonymous_sessions_cannot_buy(self):
        anonymous = Session("session-anon")
        with self.assertRaises(ProposalError) as caught:
            create_purchase_proposal(
                self.connection, self.store, anonymous,
                {"variant_id": "var_summit_39", "quantity": 1})
        self.assertEqual(caught.exception.code, "DEMO_CUSTOMER_REQUIRED")

    def test_proposal_shows_exact_quote_before_any_write(self):
        self.session.customer_id = "demo_maya"
        proposal = create_purchase_proposal(
            self.connection, self.store, self.session,
            {"variant_id": "var_summit_39", "quantity": 1})
        self.assertEqual(proposal["total_cents"], 12900)
        self.assertEqual(proposal["items"][0]["unit_price_cents"], 12900)
        self.assertEqual(proposal["items"][0]["name"], "Summit Trail")
        self.assertEqual(proposal["status"], "proposed")
        # No order lines or reservations yet.
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM order_lines").fetchone()[0], 8)
        reserved = self.connection.execute(
            "SELECT reserved FROM inventory WHERE variant_id = 'var_summit_39'"
        ).fetchone()[0]
        self.assertEqual(reserved, 1)

    def test_unknown_variant_rejected(self):
        self.session.customer_id = "demo_maya"
        with self.assertRaises(ProposalError) as caught:
            create_purchase_proposal(
                self.connection, self.store, self.session,
                {"variant_id": "var_unknown", "quantity": 1})
        self.assertEqual(caught.exception.code, "PROPOSAL_NOT_AVAILABLE")

    def test_out_of_stock_proposal_rejected(self):
        self.session.customer_id = "demo_maya"
        with self.assertRaises(ProposalError) as caught:
            create_purchase_proposal(
                self.connection, self.store, self.session,
                {"variant_id": "var_city_41", "quantity": 2})  # 0 on hand
        self.assertEqual(caught.exception.code, "OUT_OF_STOCK")


class PurchaseCommitTests(unittest.TestCase):
    def setUp(self):
        self.connection = seeded_shop_connection()
        self.store = ProposalStore(clock=lambda: 1000.0)
        self.operations = OperationStore()
        self.session = Session("session-test")
        self.session.customer_id = "demo_maya"
        self.proposal = create_purchase_proposal(
            self.connection, self.store, self.session,
            {"variant_id": "var_summit_39", "quantity": 1})

    def test_confirm_creates_processing_order_and_reserves_stock(self):
        result = confirm_purchase(
            self.connection, self.store, self.proposal["proposal_id"],
            self.session, self.operations)
        self.assertEqual(result["state"], "processing")
        order = self.connection.execute(
            "SELECT state FROM orders WHERE id = ?", (result["order_id"],)).fetchone()
        self.assertEqual(order["state"], "processing")
        reserved = self.connection.execute(
            "SELECT reserved FROM inventory WHERE variant_id = 'var_summit_39'"
        ).fetchone()[0]
        self.assertEqual(reserved, 2)  # seed 1 reserved + purchase 1

        # Repeated confirmation with the same proposal (operation resolved
        # by id) does not duplicate the order.
        client_repeat = confirm_purchase(
            self.connection, self.store, self.proposal["proposal_id"],
            self.session, self.operations)
        self.assertTrue(client_repeat["idempotent"])
        self.assertEqual(client_repeat["order_id"], result["order_id"])
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM orders").fetchone()[0], 9)

    def test_expired_proposal_requires_new_confirmation(self):
        store = ProposalStore(clock=lambda: 1000.0)
        proposal = create_purchase_proposal(
            self.connection, store, self.session,
            {"variant_id": "var_fjell_40", "quantity": 1})
        # Advance the clock past the proposal TTL before confirming.
        store.clock = lambda: 1000.0 + store.ttl_seconds + 1
        with self.assertRaises(ProposalError) as caught:
            confirm_purchase(
                self.connection, store, proposal["proposal_id"],
                self.session, self.operations)
        self.assertEqual(caught.exception.code, "PROPOSAL_EXPIRED")

    def test_price_change_requires_new_proposal(self):
        self.connection.execute(
            "UPDATE variants SET price_cents = 9999 WHERE id = 'var_summit_39'")
        propagated = self.connection.commit()
        with self.assertRaises(ProposalError) as caught:
            confirm_purchase(
                self.connection, self.store, self.proposal["proposal_id"],
                self.session, self.operations)
        self.assertEqual(caught.exception.code, "PROPOSAL_CHANGED")
        # Nothing was reserved and no order was created.
        self.assertEqual(self.connection.execute(
            "SELECT reserved FROM inventory WHERE variant_id = 'var_summit_39'"
        ).fetchone()[0], 1)
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM orders").fetchone()[0], 8)

    def test_other_session_cannot_confirm(self):
        stranger = Session("session-other")
        stranger.customer_id = "demo_leo"
        with self.assertRaises(ProposalError) as caught:
            confirm_purchase(
                self.connection, self.store, self.proposal["proposal_id"],
                stranger, self.operations)
        self.assertEqual(caught.exception.status_code, 404)

    def test_concurrent_buyers_cannot_over_reserve_last_variant(self):
        # var_summit_39 has 2 on hand, 1 reserved => exactly 1 available.
        # Threads must share ONE database (temp file) so the row-level
        # reserve and BEGIN IMMEDIATE serialize the reservation.
        with tempfile.TemporaryDirectory() as directory:
            shared = os.path.join(directory, "shop.db")
            connection = shop_connect(shared)
            seed(connection)
            second_session = Session("session-second")
            second_session.customer_id = "demo_leo"
            second_proposal = create_purchase_proposal(
                connection, self.store, second_session,
                {"variant_id": "var_summit_39", "quantity": 1})
            results = []
            barrier = threading.Barrier(2)

            def confirm(proposal_id, session):
                thread_connection = shop_connect(shared)
                barrier.wait()
                try:
                    result = confirm_purchase(
                        thread_connection, self.store, proposal_id,
                        session, self.operations)
                    results.append(("accepted", result["order_id"]))
                except ProposalError as failure:
                    results.append(("rejected", failure.code))

            threads = [
                threading.Thread(
                    target=confirm,
                    args=(self.proposal["proposal_id"], self.session)),
                threading.Thread(
                    target=confirm,
                    args=(second_proposal["proposal_id"], second_session)),
            ]
            for worker in threads:
                worker.start()
            for worker in threads:
                worker.join(10)
            reserved = connection.execute(
                "SELECT reserved FROM inventory WHERE variant_id = 'var_summit_39'"
            ).fetchone()[0]
            connection.close()
        accepted = [entry for entry in results if entry[0] == "accepted"]
        rejected = [entry for entry in results if entry[0] == "rejected"]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0][1], "OUT_OF_STOCK")
        self.assertEqual(reserved, 2)


class PurchaseApiTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.shop_path = os.path.join(self.directory.name, "shop.db")
        connection = shop_connect(self.shop_path)
        seed(connection)

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

    def count_orders(self):
        reader = shop_connect(self.shop_path)
        count = reader.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        reader.close()
        return count

    def bind_customer(self, client):
        client.post("/sessions", json={"customer_id": "demo_maya"})

    @mock.patch("backend.agent.generate_response", return_value=(
        "Great choice.\n"
        'ACTION {"tool": "propose_purchase",'
        ' "args": {"variant_id": "var_summit_39", "quantity": 1}}'))
    def test_chat_creates_proposal_only_via_confirmation(self, generate):
        self.bind_customer(self.client)
        response = self.client.post(
            "/chat",
            json={"message": "I want to buy Summit Trail in olive size 39"})
        body = response.json()
        self.assertNotIn("ACTION", body["response"])
        proposal = body["action_proposal"]
        self.assertEqual(proposal["total_cents"], 12900)
        self.assertTrue(proposal["proposal_id"])
        self.assertEqual(proposal["status"], "proposed")
        self.assertEqual(self.count_orders(), 8)
        confirmed = self.client.post(
            f"/actions/{proposal['proposal_id']}/confirm")
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["state"], "processing")
        self.assertEqual(self.count_orders(), 9)
    @mock.patch("backend.agent.generate_response", return_value="plain answer")
    def test_confirmation_of_unknown_proposal_is_safe(self, generate):
        self.bind_customer(self.client)
        response = self.client.post("/actions/unknown-proposal/confirm")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(),
                         {"detail": "Proposal is not available."})

    @mock.patch("backend.agent.generate_response", return_value="plain answer")
    def test_operation_resolution_endpoint(self, generate):
        self.bind_customer(self.client)
        reader = shop_connect(self.shop_path)
        store = ProposalStore(clock=lambda: 1000.0)
        session = main.manager.get(
            next(iter(main.manager._sessions.keys())))
        proposal = store.create(
            owner_session=session.id, owner_customer=session.customer_id,
            kind="purchase", payload={"items": [{
                "variant_id": "var_summit_39", "name": "Summit Trail",
                "size": 39, "colour": "olive", "unit_price_cents": 12900,
                "quantity": 1}]})
        operation = OperationStore()
        operation.record("op_test", proposal, "order_x")
        main.operations._operations.update(operation._operations)
        reader.close()
        found = self.client.get("/operations/op_test")
        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.json()["order_id"], "order_x")
        missing = self.client.get("/operations/op_missing")
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
