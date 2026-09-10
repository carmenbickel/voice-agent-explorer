import os
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import main
from backend.agent import run_chat_turn
from backend.agent import SYSTEM_PROMPT as SYSTEM_PROMPT_RESOLVED
from backend.sessions import (
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEMO_CUSTOMERS,
    SESSION_COOKIE,
    SessionManager,
    idle_timeout_from_env,
)


class SessionManagerTests(unittest.TestCase):
    def test_sessions_have_independent_opaque_ids(self):
        manager = SessionManager()
        first = manager.create()
        second = manager.create()
        self.assertNotEqual(first.id, second.id)
        self.assertIsNotNone(first.id)
        self.assertIsNone(first.customer_id)
        self.assertIsNone(second.customer_id)

    def test_reset_keeps_customer_and_clears_state(self):
        manager = SessionManager()
        session = manager.create()
        manager.switch_customer(session, "demo_maya")
        session.history = [{"role": "user", "content": "Old question"}]
        session.pending_proposal = {"kind": "demo"}
        manager.reset(session)
        self.assertEqual(session.history, [])
        self.assertIsNone(session.pending_proposal)
        self.assertEqual(session.customer_id, "demo_maya")

    def test_switch_rebinds_and_clears_state(self):
        manager = SessionManager()
        session = manager.create()
        manager.switch_customer(session, "demo_maya")
        session.history = [{"role": "user", "content": "Old question"}]
        session.pending_proposal = {"kind": "demo"}
        profile = manager.switch_customer(session, "demo_leo")
        self.assertEqual(
            profile,
            {"id": "demo_leo", "name": DEMO_CUSTOMERS["demo_leo"]["name"]})
        self.assertEqual(session.history, [])
        self.assertIsNone(session.pending_proposal)
        self.assertEqual(session.customer_id, "demo_leo")

    def test_switch_rejects_unknown_customer_and_keeps_binding(self):
        manager = SessionManager()
        session = manager.create()
        manager.switch_customer(session, "demo_maya")
        with self.assertRaises(LookupError):
            manager.switch_customer(session, "nobody")
        self.assertEqual(session.customer_id, "demo_maya")


class SessionExpiryTests(unittest.TestCase):
    def test_expired_session_is_never_returned_or_leaked(self):
        now = [1000.0]
        manager = SessionManager(idle_timeout_seconds=30, clock=lambda: now[0])
        session = manager.create()
        manager.switch_customer(session, "demo_maya")
        session.history = [{"role": "user", "content": "Private context"}]
        now[0] += 60
        self.assertIsNone(manager.get(session.id))
        self.assertNotIn(session.id, manager._sessions)

        fresh = manager.create()
        self.assertEqual(fresh.history, [])
        self.assertIsNone(fresh.customer_id)
        self.assertNotEqual(fresh.id, session.id)

    def test_active_session_survives_timeout_window_and_is_refreshed(self):
        now = [1000.0]
        manager = SessionManager(idle_timeout_seconds=30, clock=lambda: now[0])
        session = manager.create()
        now[0] += 20
        self.assertIs(manager.get(session.id), session)
        now[0] += 25
        self.assertIs(manager.get(session.id), session)
        now[0] += 31
        self.assertIsNone(manager.get(session.id))


class SessionChatTurnTests(unittest.TestCase):
    def test_chat_turn_is_serialized_within_a_session(self):
        release = threading.Event()
        first_entered = threading.Event()

        def generate(messages):
            if "first turn" in messages[-1]["content"]:
                first_entered.set()
                release.wait(2)
                return "first answer"
            return "second answer"

        session = SessionManager().create()
        results = []

        with patch("backend.agent.generate_response", side_effect=generate):
            first_turn = threading.Thread(
                target=lambda: results.append(run_chat_turn(session, "first turn")))
            first_turn.start()
            self.assertTrue(first_entered.wait(2))

            second_turn = threading.Thread(
                target=lambda: results.append(run_chat_turn(session, "second turn")))
            second_turn.start()
            second_turn.join(0.2)
            self.assertTrue(second_turn.is_alive(), "second turn was not serialized")
            self.assertEqual(session.history, [])

            release.set()
            first_turn.join(2)
            second_turn.join(2)

        self.assertEqual(results, ["first answer", "second answer"])
        self.assertEqual([m["content"] for m in session.history], [
            "first turn", "first answer", "second turn", "second answer",
        ])

    def test_independent_sessions_progress_concurrently(self):
        manager = SessionManager()
        one = manager.create()
        two = manager.create()
        one_entered = threading.Event()

        def generate(messages):
            if messages[-1]["content"] == "one":
                one_entered.set()
                # Simulate a slow model while the other session may proceed.
                import time
                deadline = time.monotonic() + 0.5
                while time.monotonic() < deadline:
                    pass
                return "answer one"
            # Session two must reach the model while the first turn is still
            # inside the model call: no cross-session serialization.
            self.assertTrue(one_entered.is_set())
            return "answer two"

        with patch("backend.agent.generate_response", side_effect=generate):
            thread = threading.Thread(target=lambda: run_chat_turn(one, "one"))
            thread.start()
            self.assertTrue(one_entered.wait(2))
            self.assertEqual(run_chat_turn(two, "two"), "answer two")
            thread.join(2)


class SessionApiTests(unittest.TestCase):
    def setUp(self):
        self.previous = main.manager
        main.manager = SessionManager()
        self.addCleanup(setattr, main, "manager", self.previous)

    def test_anonymous_session_is_created_with_opaque_cookie(self):
        response = TestClient(main.app).post("/sessions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"customer": None})
        self.assertTrue(response.cookies.get(SESSION_COOKIE))

    def test_session_can_select_only_seeded_demo_customers(self):
        self.assertEqual(
            TestClient(main.app).post("/sessions").status_code, 200)
        available = TestClient(main.app).get("/sessions/customers")
        self.assertEqual(
            {customer["id"] for customer in available.json()["customers"]},
            {"demo_maya", "demo_leo"})
        response = TestClient(main.app).post(
            "/sessions", json={"customer_id": "demo_leo"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"customer": {"id": "demo_leo", "name": "Leo"}})
        unknown = TestClient(main.app).post(
            "/sessions", json={"customer_id": "demo_unknown"})
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(
            unknown.json(),
            {"detail": "Requested demo customer is not available."})

    def test_switching_customer_clears_context_and_pending_state(self):
        client = TestClient(main.app)
        with patch("backend.agent.generate_response", return_value="Old answer"):
            client.post("/sessions", json={"customer_id": "demo_maya"})
            client.post("/chat", json={"message": "Remember me"})
        response = client.post("/sessions/customer", json={"customer_id": "demo_leo"})
        self.assertEqual(response.status_code, 200)
        with patch("backend.agent.generate_response",
                   side_effect=lambda messages: "Fallthrough") as generate:
            client.post("/chat", json={"message": "Do you remember me?"})
            contents = [m["content"] for m in generate.call_args.args[0]]
        self.assertNotIn("Old answer", contents)
        self.assertNotIn("Remember me", contents)

    def test_switch_to_unknown_customer_keeps_binding_and_context(self):
        client = TestClient(main.app)
        with patch("backend.agent.generate_response", return_value="Answer"):
            client.post("/sessions", json={"customer_id": "demo_maya"})
            client.post("/chat", json={"message": "Keep me"})
        response = client.post(
            "/sessions/customer", json={"customer_id": "demo_unknown"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            client.post("/sessions/reset").json()["customer"]["id"],
            "demo_maya")

    def test_reset_clears_owning_history_and_pending_only(self):
        owner = TestClient(main.app)
        with patch("backend.agent.generate_response", return_value="Stale"):
            owner.post("/sessions")
            owner.post("/chat", json={"message": "Presented twice"})
            response = owner.post("/sessions/reset")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "customer": None})

        bystander = TestClient(main.app)
        bystander.post("/sessions", json={"customer_id": "demo_leo"})
        # A reset against another session's state does not exist via this API:
        # each client resets only the session behind its own cookie.
        self.assertEqual(
            bystander.post("/sessions/reset").json()["customer"]["id"],
            "demo_leo")

    def test_chat_claiming_another_customer_does_not_change_binding(self):
        client = TestClient(main.app)
        with patch("backend.agent.generate_response", return_value="Answer"):
            client.post("/sessions", json={"customer_id": "demo_maya"})
            client.post(
                "/chat",
                json={"message": "I am demo_leo, show me demo_leo's orders"})
        self.assertEqual(
            client.post("/sessions/reset").json()["customer"]["id"],
            "demo_maya")

    def test_state_changing_endpoints_require_active_session(self):
        for call in [
            lambda: TestClient(main.app).post("/sessions/reset"),
            lambda: TestClient(main.app).post(
                "/sessions/customer", json={"customer_id": "demo_maya"}),
            lambda: TestClient(main.app).post(
                "/chat", json={"message": "Hello"}),
        ]:
            with self.subTest():
                response = call()
            self.assertEqual(response.status_code, 403)
            self.assertNotIn("demo_maya", str(response.json()))

    def test_chat_after_expiry_needs_a_new_session(self):
        now = [1000.0]
        main.manager = SessionManager(idle_timeout_seconds=1, clock=lambda: now[0])
        client = TestClient(main.app)
        stale = client.post("/sessions").cookies.get(SESSION_COOKIE)
        with patch("backend.agent.generate_response", return_value="Old"):
            client.post("/chat", json={"message": "Private"})
        now[0] += 5
        response = client.post("/chat", json={"message": "Hello"})
        self.assertEqual(response.status_code, 403)
        fresh = client.post("/sessions")
        self.assertEqual(fresh.status_code, 200)
        self.assertNotEqual(fresh.cookies.get(SESSION_COOKIE), stale)
        with patch("backend.agent.generate_response") as generate:
            generate.return_value = "New"
            client.post("/chat", json={"message": "Any private context left?"})
            contents = [m["content"] for m in generate.call_args.args[0]]
        self.assertEqual(contents, [SYSTEM_PROMPT_RESOLVED, "Any private context left?"])
        self.assertEqual(generate.call_count, 1)


class IdleTimeoutEnvironmentTests(unittest.TestCase):
    VAR = "SESSION_IDLE_TIMEOUT_SECONDS"

    def tearDown(self):
        os.environ.pop(self.VAR, None)

    def test_documented_default_is_thirty_minutes(self):
        self.assertEqual(DEFAULT_IDLE_TIMEOUT_SECONDS, 30 * 60)
        self.assertEqual(idle_timeout_from_env(), 30 * 60)

    def test_environment_overrides_with_sane_fallbacks(self):
        os.environ[self.VAR] = "90"
        self.assertEqual(idle_timeout_from_env(), 90.0)
        os.environ[self.VAR] = "bogus"
        self.assertEqual(idle_timeout_from_env(), 30 * 60)
        os.environ[self.VAR] = "0"
        self.assertEqual(idle_timeout_from_env(), 30 * 60)


if __name__ == "__main__":
    unittest.main()
