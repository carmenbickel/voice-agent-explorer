import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend import main, ollama_client
from backend.agent import SYSTEM_PROMPT
from backend.sessions import SessionManager
from backend.ollama_client import OllamaError


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.manager_patch = patch.object(main, "manager", SessionManager())
        self.manager_patch.start()
        self.addCleanup(self.manager_patch.stop)
        self.client = TestClient(main.app)

    def new_customer_session(self):
        return self.client.post("/sessions")

    @patch("backend.ollama_client.httpx.post")
    def test_api_preserves_context_and_system_prompt(self, post):
        session = self.new_customer_session()
        self.assertEqual(session.status_code, 200)
        post.side_effect = [
            httpx.Response(200, json={"message": {"content": reply}},
                           request=httpx.Request("POST", "http://ollama/api/chat"))
            for reply in ["VAD means Voice Activity Detection.", "It detects speech."]
        ]
        first = self.client.post("/chat", json={"message": "What is VAD?"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), {"response": "VAD means Voice Activity Detection."})
        second = self.client.post("/chat", json={"message": "Why do we need it?"})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json(), {"response": "It detects speech."})
        self.assertEqual(post.call_args.args, (f"{ollama_client.OLLAMA_BASE_URL}/api/chat",))
        self.assertEqual(post.call_args.kwargs["json"], {
            "model": ollama_client.OLLAMA_MODEL,
            "stream": False,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "What is VAD?"},
                {"role": "assistant", "content": first.json()["response"]},
                {"role": "user", "content": "Why do we need it?"},
            ],
        })

    @patch("backend.agent.generate_response")
    def test_failed_turn_does_not_pollute_history(self, generate):
        self.new_customer_session()
        generate.side_effect = ["VAD detects speech.", OllamaError("Unavailable"), "Answer"]
        self.client.post("/chat", json={"message": "What is VAD?"})
        failed = self.client.post("/chat", json={"message": "Failed question"})
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(failed.json(), {"detail": "Unavailable"})
        self.client.post("/chat", json={"message": "Why do we need it?"})
        self.assertEqual([m["content"] for m in generate.call_args.args[0]], [
            SYSTEM_PROMPT, "What is VAD?", "VAD detects speech.", "Why do we need it?",
        ])

    @patch("backend.ollama_client.httpx.post")
    def test_sessions_have_independent_histories(self, post):
        def reply(content):
            return httpx.Response(200, json={"message": {"content": content}},
                                  request=httpx.Request("POST", "http://ollama/api/chat"))

        first = TestClient(main.app)
        second = TestClient(main.app)
        first.post("/sessions")
        second.post("/sessions")
        post.side_effect = [reply("Answer one"), reply("Answer two"), reply("Follow-up one")]
        self.assertEqual(
            first.post("/chat", json={"message": "Session one"}).json(),
            {"response": "Answer one"})
        self.assertEqual(
            second.post("/chat", json={"message": "Session two"}).json(),
            {"response": "Answer two"})
        self.assertEqual(
            first.post("/chat", json={"message": "Session one again"}).json(),
            {"response": "Follow-up one"})
        calls = [call.kwargs["json"]["messages"] for call in post.call_args_list]
        self.assertEqual(calls[1], [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Session two"},
        ])
        self.assertEqual(calls[2], [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Session one"},
            {"role": "assistant", "content": "Answer one"},
            {"role": "user", "content": "Session one again"},
        ])

    def test_health_and_request_validation(self):
        self.new_customer_session()
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        self.assertEqual(self.client.post("/chat", json={}).status_code, 422)

    def test_chat_requires_session(self):
        self.assertEqual(self.client.post("/chat", json={"message": "Hi"}).status_code, 403)

    def test_browser_page_and_assets_are_served(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers["content-type"])
        self.assertIn("demo-customer", page.text)
        for path, content_type in [("/static/app.js", "javascript"),
                                   ("/static/styles.css", "text/css")]:
            with self.subTest(path=path):
                self.assertIn(path, page.text)
                asset = self.client.get(path)
                self.assertEqual(asset.status_code, 200)
                self.assertIn(content_type, asset.headers["content-type"])

    @patch("backend.ollama_client.httpx.post")
    def test_ollama_errors(self, post):
        request = httpx.Request("POST", "http://ollama/api/chat")
        for response in [
            httpx.Response(500, request=request),
            httpx.Response(200, text="invalid json", request=request),
            *[httpx.Response(200, json=data, request=request) for data in
              [{}, {"message": None}, {"message": {"content": 42}}]],
        ]:
            with self.subTest(response=response):
                post.return_value = response
                with self.assertRaises(OllamaError):
                    ollama_client.generate_response([])
        post.side_effect = httpx.ConnectError("Unavailable")
        with self.assertRaises(OllamaError):
            ollama_client.generate_response([])


if __name__ == "__main__":
    unittest.main()
