"""Stepwise Shoes demo: SQLite shop fixtures and catalog services.

Deterministic seed and reset are the same command: `python -m backend.shop seed`
wipes all business records and restores the canonical demo state, so repeated
runs always produce the same products, variants, inventory, customers, and
orders. Demo customer IDs deliberately match the session demo identity IDs
(demo_maya, demo_leo) so local demo sessions own shop records by ID.
"""

import os
import sqlite3
from pathlib import Path


POLICY_VERSION = "policy-2026-06-v1"

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "shop.db"

PRODUCTS = [
    # (id, name, brand, category)
    ("prod_summit", "Summit Trail", "Stepwise", "trail-running"),
    ("prod_fjell", "Fjell Trek", "Stepwise", "hiking"),
    ("prod_storm", "Storm Step GTX", "Stepwise", "hiking"),
    ("prod_city", "City Walk", "Stepwise", "sneakers"),
    ("prod_swift", "Swift Run", "Stepwise", "running"),
    ("prod_sunny", "Sunny Slide", "Stepwise", "sandals"),
]

VARIANTS = [
    # (id, product_id, size, colour, price_cents, on_hand, reserved)
    ("var_summit_38", "prod_summit", 38, "olive", 12900, 4, 0),
    ("var_summit_39", "prod_summit", 39, "olive", 12900, 2, 1),
    ("var_summit_40", "prod_summit", 40, "coral", 12900, 0, 0),
    ("var_fjell_38", "prod_fjell", 38, "brown", 15900, 5, 0),
    ("var_fjell_39", "prod_fjell", 39, "brown", 15900, 1, 1),
    ("var_fjell_40", "prod_fjell", 40, "grey", 15900, 3, 0),
    ("var_storm_36", "prod_storm", 36, "black", 18900, 2, 0),
    ("var_storm_38", "prod_storm", 38, "black", 18900, 6, 2),
    ("var_storm_40", "prod_storm", 40, "anthracite", 18900, 1, 0),
    ("var_city_37", "prod_city", 37, "white", 9900, 4, 1),
    ("var_city_39", "prod_city", 39, "navy", 9900, 5, 0),
    ("var_city_41", "prod_city", 41, "white", 9900, 0, 0),
    ("var_swift_38", "prod_swift", 38, "blue", 11900, 3, 0),
    ("var_swift_39", "prod_swift", 39, "yellow", 11900, 4, 0),
    ("var_swift_41", "prod_swift", 41, "blue", 11900, 2, 0),
    ("var_sunny_36", "prod_sunny", 36, "beige", 5900, 6, 0),
    ("var_sunny_39", "prod_sunny", 39, "sand", 5900, 3, 0),
    ("var_sunny_41", "prod_sunny", 41, "black", 5900, 4, 1),
]

CUSTOMERS = [
    # (id, name)
    ("demo_maya", "Maya"),
    ("demo_leo", "Leo"),
]

ORDERS = [
    # (id, customer_id, state, created_utc, delivery_utc) — one of each state
    # per customer, with fixed timestamps for a fully deterministic seed.
    ("order_maya_1", "demo_maya", "processing", "2026-06-01T10:00:00+00:00", None),
    ("order_maya_2", "demo_maya", "shipped", "2026-06-01T10:05:00+00:00", "2026-06-02T10:00:00+00:00"),
    ("order_maya_3", "demo_maya", "delivered", "2026-06-01T10:10:00+00:00", "2026-06-02T10:00:00+00:00"),
    ("order_maya_4", "demo_maya", "cancelled", "2026-06-01T10:15:00+00:00", None),
    ("order_leo_1", "demo_leo", "processing", "2026-06-01T11:00:00+00:00", None),
    ("order_leo_2", "demo_leo", "shipped", "2026-06-01T11:05:00+00:00", "2026-06-02T11:00:00+00:00"),
    ("order_leo_3", "demo_leo", "delivered", "2026-06-01T11:10:00+00:00", "2026-06-02T11:00:00+00:00"),
    ("order_leo_4", "demo_leo", "cancelled", "2026-06-01T11:15:00+00:00", None),
]

ORDER_LINES = [
    # (order_id, variant_id, quantity) — unit price and metadata are snapshotted.
    ("order_maya_1", "var_summit_39", 1),
    ("order_maya_2", "var_storm_38", 1),
    ("order_maya_3", "var_fjell_39", 2),
    ("order_maya_4", "var_city_37", 1),
    ("order_leo_1", "var_swift_39", 1),
    ("order_leo_2", "var_sunny_39", 1),
    ("order_leo_3", "var_storm_38", 1),
    ("order_leo_4", "var_fjell_40", 1),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    brand TEXT NOT NULL,
    category TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS variants (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products(id),
    size INTEGER NOT NULL CHECK (size > 0),
    colour TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    UNIQUE (product_id, size, colour)
);

CREATE TABLE IF NOT EXISTS inventory (
    variant_id TEXT PRIMARY KEY REFERENCES variants(id),
    on_hand INTEGER NOT NULL CHECK (on_hand >= 0),
    reserved INTEGER NOT NULL CHECK (reserved >= 0 AND reserved <= on_hand)
);

CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS carts (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    created_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cart_lines (
    cart_id TEXT NOT NULL REFERENCES carts(id),
    variant_id TEXT NOT NULL REFERENCES variants(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (cart_id, variant_id)
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    state TEXT NOT NULL CHECK (state IN ('processing', 'shipped', 'delivered', 'cancelled')),
    policy_version TEXT NOT NULL,
    delivery_utc TEXT,
    created_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_lines (
    order_id TEXT NOT NULL REFERENCES orders(id),
    variant_id TEXT NOT NULL,
    item_name TEXT NOT NULL,
    size INTEGER NOT NULL,
    colour TEXT NOT NULL,
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (order_id, variant_id)
);

CREATE TABLE IF NOT EXISTS returns (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    order_id TEXT NOT NULL REFERENCES orders(id),
    variant_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    condition TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'requested',
    created_utc TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_active_return
    ON returns (order_id, variant_id) WHERE state = 'requested';

CREATE TABLE IF NOT EXISTS exchanges (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    order_id TEXT NOT NULL,
    original_variant_id TEXT NOT NULL,
    replacement_variant_id TEXT NOT NULL,
    condition TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'requested',
    created_utc TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_active_exchange
    ON exchanges(order_id, original_variant_id) WHERE state = 'requested';
"""


def database_path() -> Path:
    return Path(os.environ.get("SHOP_DB", DEFAULT_DB_PATH))


def connect(path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    return connection


def seed(connection: sqlite3.Connection) -> None:
    """Restore the canonical deterministic demo state (also the reset command)."""
    connection.executescript("""
        DELETE FROM order_lines;
        DELETE FROM orders;
        DELETE FROM cart_lines;
        DELETE FROM carts;
        DELETE FROM inventory;
        DELETE FROM variants;
        DELETE FROM products;
        DELETE FROM customers;
    """)

    variants = [(v[0], v[1], v[2], v[3], v[4]) for v in VARIANTS]
    inventory = [(v[0], v[5], v[6]) for v in VARIANTS]

    connection.executemany(
        "INSERT INTO products (id, name, brand, category) VALUES (?, ?, ?, ?)",
        PRODUCTS)
    connection.executemany(
        "INSERT INTO variants (id, product_id, size, colour, price_cents)"
        " VALUES (?, ?, ?, ?, ?)",
        variants)
    connection.executemany(
        "INSERT INTO inventory (variant_id, on_hand, reserved) VALUES (?, ?, ?)",
        inventory)
    connection.executemany(
        "INSERT INTO customers (id, name) VALUES (?, ?)", CUSTOMERS)

    orders = [
        (identifier, customer, state, POLICY_VERSION, delivery, created)
        for identifier, customer, state, created, delivery in ORDERS
    ]
    shipped_by_variant = {v[0]: (v[1], v[2], v[3], v[4]) for v in VARIANTS}
    lines = []
    for order_id, variant_id, quantity in ORDER_LINES:
        product_id, size, colour, price = shipped_by_variant[variant_id]
        product = next(p for p in PRODUCTS if p[0] == product_id)
        lines.append(
            (order_id, variant_id, product[1], size, colour, price, quantity))
    connection.executemany(
        "INSERT INTO orders (id, customer_id, state, policy_version,"
        " delivery_utc, created_utc) VALUES (?, ?, ?, ?, ?, ?)",
        orders)
    connection.executemany(
        "INSERT INTO order_lines (order_id, variant_id, item_name, size,"
        " colour, unit_price_cents, quantity) VALUES (?, ?, ?, ?, ?, ?, ?)",
        lines)
    connection.commit()


class Catalog:
    """Read access to products, variants, stock, and owned demo records."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def list_products(self):
        products = self.connection.execute(
            "SELECT id, name, brand, category FROM products ORDER BY id"
        ).fetchall()
        return [dict(row) for row in products]

    def get_variant(self, variant_id):
        row = self.connection.execute(
            "SELECT v.id, v.product_id, v.size, v.colour, v.price_cents,"
            " p.name AS product_name, p.brand, p.category,"
            " i.on_hand, i.reserved"
            " FROM variants v"
            " JOIN products p ON p.id = v.product_id"
            " JOIN inventory i ON i.variant_id = v.id"
            " WHERE v.id = ?", (variant_id,)
        ).fetchone()
        if row is None:
            return None
        variant = dict(row)
        variant["available"] = variant["on_hand"] - variant["reserved"]
        return variant

    def get_customer(self, customer_id):
        row = self.connection.execute(
            "SELECT id, name FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_customer_orders(self, customer_id):
        rows = self.connection.execute(
            "SELECT id, state, policy_version, delivery_utc, created_utc"
            " FROM orders WHERE customer_id = ? ORDER BY id",
            (customer_id,)
        ).fetchall()
        orders = []
        for row in rows:
            order = dict(row)
            order["lines"] = [
                dict(line) for line in self.connection.execute(
                    "SELECT variant_id, item_name, size, colour,"
                    " unit_price_cents, quantity FROM order_lines"
                    " WHERE order_id = ? ORDER BY variant_id",
                    (order["id"],)
                ).fetchall()
            ]
            orders.append(order)
        return orders


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Seed or reset the Stepwise Shoes demo database.")
    parser.add_argument("command", choices=["seed", "reset"],
                        help="'reset' also restores the canonical state")
    arguments = parser.parse_args()
    with_seeding = database_path()
    connection = connect(with_seeding)
    seed(connection)
    connection.close()
    print(f"Shop database {with_seeding} reset to canonical demo fixtures.")


if __name__ == "__main__":
    main()
