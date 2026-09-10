import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import main
from backend.sessions import SessionManager
from backend.traces import TRACE_TTL_SECONDS, TraceStore


def fresh_client():
    return TestClient(main.app)


class TraceFlowTests(unittest.TestCase):
    def setUp(self):
        self.previous_manager, self.previous_traces = main.manager, main.traces
        main.manager = SessionManager()
        main.traces = TraceStore()
        self.addCleanup(setattr, main, "manager", self.previous_manager)
        self.addCleanup(setattr, main, "traces", self.previous_traces)

    def chat_turn(self, client, message="Hello"):
        return client.post("/chat", json={"message": message})

    @patch("backend.agent.generate_response", return_value="Answer for me")
    def test_turn_has_trace_and_turn_ids(self, generate):
        client = TestClient(main.app)
        client.post("/sessions")
        response = self.chat_turn(client)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("trace_id", body)
        self.assertIn("turn_id", body)
        trace = client.get(f"/traces/{body['trace_id']}").json()
        self.assertEqual(trace["turn_id"], body["turn_id"])
        stages = [e["stage"] for e in trace["events"]]
        self.assertEqual(stages, [
            "session resolved", "retrieval", "model", "response assembly"])
        statuses = {e["stage"]: e["status"] for e in trace["events"]}
        self.assertEqual(statuses["retrieval"], "skipped")
        self.assertEqual(statuses["model"], "executed")
        # The panel must show real durations for executed stages, never fake ones.
        self.assertIn("duration_ms", next(e for e in trace["events"] if e["stage"] == "model"))

    @patch("backend.agent.generate_response", return_value="Answer")
    def test_traces_are_session_scoped(self, generate):
        owner = TestClient(main.app)
        bystander = TestClient(main.app)
        for client in (owner, bystander):
            client.post("/sessions")
        body = self.chat_turn(owner).json()
        ok = owner.get(f"/traces/{body['trace_id']}")
        self.assertEqual(ok.status_code, 200)
        leak = bystander.get(f"/traces/{body['trace_id']}")
        # Cross-session access is denied without any data disclosure.
        self.assertEqual(leak.status_code, 404)
        self.assertEqual(leak.json(), {"detail": "Trace is not available."})

    @patch("backend.agent.generate_response", return_value="Answer")
    def test_traces_contain_no_prompt_content(self, generate):
        client = TestClient(main.app)
        client.post("/sessions")
        secret = "S3cret-Prompt-That-Must-Not-Leak"
        body = self.chat_turn(client, message=f"Tell me about {secret}").json()
        view = client.get(f"/traces/{body['trace_id']}").text
        self.assertNotIn(secret, view)

    @patch("backend.agent.generate_response", return_value="Answer")
    def test_trace_failure_does_not_break_the_customer_response(self, generate):
        client = TestClient(main.app)
        client.post("/sessions")
        # Trace recording is best-effort: even if recording raises, the chat
        # result must be unaffected and still served.
        with patch.object(main.traces, "record", side_effect=RuntimeError("boom")):
            response = self.chat_turn(client)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["response"], "Answer")
        self.assertIsNone(body["trace_id"])

    def test_expired_traces_are_not_served(self):
        now = [1000.0]
        store = TraceStore(clock=lambda: now[0])
        main.traces = store
        client = TestClient(main.app)
        client.post("/sessions")
        body = self.chat_turn(client).json()
        now[0] += TRACE_TTL_SECONDS + 1
        self.assertEqual(
            client.get(f"/traces/{body['trace_id']}").status_code, 404)

    @patch("backend.agent.generate_response", return_value="Answer")
    def test_retention_size_cap(self, generate):
        store = TraceStore(max_traces=2)
        main.traces = store
        client = TestClient(main.app)
        client.post("/sessions")
        first = self.chat_turn(client).json()["trace_id"]
        self.chat_turn(client)
        self.chat_turn(client)
        self.assertEqual(
            client.get(f"/traces/{first}").status_code, 404)

    def test_trace_endpoint_requires_session(self):
        self.assertEqual(TestClient(main.app).get("/traces/whatever").status_code, 403)


class TraceStoreUnitTests(unittest.TestCase):
    def test_view_is_a_copy(self):
        store = TraceStore()
        trace = store.begin()
        store.record(trace, "model", "executed", duration_ms=5.4)
        view = store.view(trace["trace_id"])
        view["events"].append({"stage": "evil", "status": "executed"})
        self.assertEqual(len(store.view(trace["trace_id"])["events"]), 1)
        self.assertEqual(
            store.view(trace["trace_id"])["events"][0]["duration_ms"], 5)


if __name__ == "__main__":
    unittest.main()
