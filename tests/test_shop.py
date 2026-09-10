import sqlite3
import unittest
from datetime import datetime, timezone

from backend.shop import Catalog, POLICY_VERSION, connect, seed


class ShopSeedTests(unittest.TestCase):
    def setUp(self):
        self.connection = connect(":memory:")
        seed(self.connection)
        self.catalog = Catalog(self.connection)

    def tearDown(self):
        self.connection.close()

    def test_canonical_identifiers(self):
        products = {p["id"] for p in self.catalog.list_products()}
        self.assertEqual(products, {
            "prod_summit", "prod_fjell", "prod_storm",
            "prod_city", "prod_swift", "prod_sunny",
        })
        variant = self.catalog.get_variant("var_summit_39")
        self.assertEqual(variant["product_id"], "prod_summit")
        self.assertEqual(variant["size"], 39)
        self.assertEqual(variant["colour"], "olive")
        self.assertEqual(variant["price_cents"], 12900)

    def test_seed_is_deterministic_across_resets(self):
        def dump(connection):
            tables = ["products", "variants", "inventory", "customers",
                      "orders", "order_lines"]
            return [
                [tuple(row) for row in connection.execute(f"SELECT * FROM {t}")]
                for t in tables
            ]

        first = dump(self.connection)
        seed(self.connection)
        self.assertEqual(dump(self.connection), first)

    def test_seed_includes_all_fulfillment_states(self):
        states = {order["state"] for order in self.catalog.list_customer_orders("demo_maya")}
        self.assertEqual(states, {"processing", "shipped", "delivered", "cancelled"})

    def test_orders_carry_snapshots_policy_and_delivery_time(self):
        order = self.catalog.list_customer_orders("demo_maya")[0]
        self.assertEqual(order["state"], "processing")
        self.assertEqual(order["policy_version"], POLICY_VERSION)
        self.assertEqual(order["lines"], [{
            "variant_id": "var_summit_39",
            "item_name": "Summit Trail",
            "size": 39,
            "colour": "olive",
            "unit_price_cents": 12900,
            "quantity": 1,
        }])

    def test_customers_match_demo_session_binding_ids(self):
        for customer_id in ["demo_maya", "demo_leo"]:
            with self.subTest(customer_id=customer_id):
                self.assertEqual(
                    self.catalog.get_customer(customer_id)["id"], customer_id)


class ShopCatalogReadTests(unittest.TestCase):
    def setUp(self):
        self.connection = connect(":memory:")
        seed(self.connection)
        self.catalog = Catalog(self.connection)
        self.addCleanup(self.connection.close)

    def test_stock_covers_on_hand_reserved_and_available(self):
        variant = self.catalog.get_variant("var_storm_38")
        self.assertEqual(variant["on_hand"], 6)
        self.assertEqual(variant["reserved"], 2)
        self.assertEqual(variant["available"], 4)

    def test_reads_come_from_database_not_const_data(self):
        # A direct DB mutation must be visible through the catalog, proving
        # reads resolve from SQLite rows.
        self.connection.execute(
            "UPDATE inventory SET on_hand = 99 WHERE variant_id = 'var_fjell_40'")
        variant = self.catalog.get_variant("var_fjell_40")
        self.assertEqual(variant["on_hand"], 99)

    def test_missing_ids_return_none(self):
        self.assertIsNone(self.catalog.get_variant("var_unknown"))
        self.assertIsNone(self.catalog.get_customer("demo_unknown"))
        self.assertEqual(self.catalog.list_customer_orders("demo_unknown"), [])

    def test_unknown_product_has_no_variant_rows(self):
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM variants WHERE product_id = 'prod_none'"
        ).fetchone()[0], 0)


class ShopInvariantTests(unittest.TestCase):
    def setUp(self):
        self.connection = connect(":memory:")
        seed(self.connection)
        self.addCleanup(self.connection.close)

    def test_inventory_never_becomes_negative(self):
        for statement, parameters in [
            ("UPDATE inventory SET on_hand = -1 WHERE variant_id = 'var_fjell_40';", ()),
            ("UPDATE inventory SET reserved = -1 WHERE variant_id = 'var_fjell_40';", ()),
            ("INSERT INTO inventory (variant_id, on_hand, reserved) VALUES ('var_x', -2, 0)", ()),
        ]:
            with self.subTest(statement=statement):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.connection.execute(statement)

    def test_reserved_cannot_exceed_on_hand(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE inventory SET reserved = 7 WHERE variant_id = 'var_fjell_40'")

    def test_malformed_quantities_are_rejected(self):
        for statement, parameters in [
            ("INSERT INTO order_lines (order_id, variant_id, item_name, size,"
             " colour, unit_price_cents, quantity)"
             " VALUES ('order_maya_1', 'var_summit_39', 'x', 39, 'olive', 1, 0)", ()),
            ("INSERT INTO variants (id, product_id, size, colour, price_cents)"
             " VALUES ('var_new', 'prod_summit', 38, 'olive', -100)", ()),
            ("INSERT INTO cart_lines (cart_id, variant_id, quantity)"
             " VALUES ('cart_x', 'var_summit_39', 0)", ()),
        ]:
            with self.subTest(statement=statement):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.connection.execute(statement)

    def test_utc_timestamps_parse_with_explicit_offset(self):
        rows = self.connection.execute(
            "SELECT created_utc, delivery_utc FROM orders").fetchall()
        for row in rows:
            parsed = datetime.fromisoformat(row["created_utc"])
            self.assertIsNotNone(parsed.tzinfo)
            if row["delivery_utc"] is not None:
                parsed = datetime.fromisoformat(row["delivery_utc"])
                self.assertIsNotNone(parsed.tzinfo)


class ShopResetTests(unittest.TestCase):
    def setUp(self):
        self.connection = connect(":memory:")
        seed(self.connection)
        self.addCleanup(self.connection.close)

    def test_reset_restores_business_records_and_reservations(self):
        self.connection.execute(
            "INSERT INTO orders (id, customer_id, state, policy_version,"
            " delivery_utc, created_utc)"
            " VALUES ('order_extra', 'demo_maya', 'processing',"
            " 'policy-2026-06-v1', NULL, '2026-06-03T09:00:00+00:00')")
        self.connection.execute(
            "INSERT INTO order_lines (order_id, variant_id, item_name, size,"
            " colour, unit_price_cents, quantity)"
            " VALUES ('order_extra', 'var_summit_39', 'Summit Trail', 39,"
            " 'olive', 12900, 5)")
        self.connection.execute(
            "UPDATE inventory SET reserved = 2 WHERE variant_id = 'var_summit_39'")
        self.connection.commit()

        seed(self.connection)

        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM orders WHERE id = 'order_extra'"
        ).fetchone()[0], 0)
        lines = self.connection.execute(
            "SELECT quantity FROM order_lines WHERE order_id = 'order_maya_1'"
        ).fetchall()
        self.assertEqual([line["quantity"] for line in lines], [1])
        reserved = self.connection.execute(
            "SELECT reserved FROM inventory WHERE variant_id = 'var_summit_39'"
        ).fetchone()["reserved"]
        self.assertEqual(reserved, 1)
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM carts").fetchone()[0], 0)

    def test_carts_are_emptied_by_reset(self):
        self.connection.execute(
            "INSERT INTO carts (id, customer_id, created_utc)"
            " VALUES ('cart_t1', 'demo_maya', '2026-06-01T12:00:00+00:00')")
        self.connection.execute(
            "INSERT INTO cart_lines (cart_id, variant_id, quantity)"
            " VALUES ('cart_t1', 'var_summit_39', 1)")
        self.connection.commit()
        seed(self.connection)
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM carts").fetchone()[0], 0)
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM cart_lines").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
