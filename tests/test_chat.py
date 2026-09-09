import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend import main, ollama_client
from backend.agent import ConversationAgent, SYSTEM_PROMPT
from backend.ollama_client import OllamaError


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.agent_patch = patch.object(main, "agent", ConversationAgent())
        self.agent_patch.start()
        self.addCleanup(self.agent_patch.stop)
        self.client = TestClient(main.app)

    @patch("backend.ollama_client.httpx.post")
    def test_api_preserves_context_and_system_prompt(self, post):
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
        generate.side_effect = ["VAD detects speech.", OllamaError("Unavailable"), "Answer"]
        self.client.post("/chat", json={"message": "What is VAD?"})
        failed = self.client.post("/chat", json={"message": "Failed question"})
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(failed.json(), {"detail": "Unavailable"})
        self.client.post("/chat", json={"message": "Why do we need it?"})
        self.assertEqual([m["content"] for m in generate.call_args.args[0]], [
            SYSTEM_PROMPT, "What is VAD?", "VAD detects speech.", "Why do we need it?",
        ])

    @patch("backend.agent.generate_response", return_value="Answer")
    def test_new_agent_starts_fresh(self, generate):
        ConversationAgent().chat("First session")
        ConversationAgent().chat("New session")
        self.assertEqual(generate.call_args.args[0], [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "New session"},
        ])

    def test_health_and_request_validation(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        self.assertEqual(self.client.post("/chat", json={}).status_code, 422)

    def test_browser_page_and_assets_are_served(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers["content-type"])
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
