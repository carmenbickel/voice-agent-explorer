import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import main
from backend.sessions import SessionManager


FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
LOGIC_TEST = Path(__file__).resolve().parent / "voice_logic_test.js"


class VoiceLogicNodeTests(unittest.TestCase):
    """Automated logic tests for fallback and stale-turn behavior (B1)."""

    def test_voice_logic_module_passes_node_tests(self):
        if shutil.which("node") is None:
            self.skipTest("node is not installed; voice logic tests skipped")
        result = subprocess.run(
            ["node", str(LOGIC_TEST)], capture_output=True, text=True, check=False)
        with self.subTest(stdout=result.stdout[-800:]):
            self.assertEqual(result.returncode, 0, result.stderr)
        if result.stderr:
            self.fail(result.stderr)

    def test_voice_logic_is_loadable_by_node(self):
        if shutil.which("node") is None:
            self.skipTest("node is not installed; voice logic tests skipped")
        result = subprocess.run(
            ["node", "-e",
             "const l = require('%s'); "
             "if (l.VOICE_STATES.length !== 5) process.exit(2);"
             % (FRONTEND / "voice-logic.js")],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


class VoicePageTests(unittest.TestCase):
    def setUp(self):
        self.previous = main.manager
        self.addCleanup(setattr, main, "manager", self.previous)
        main.manager = SessionManager()
        self.client = TestClient(main.app)

    def test_page_presents_voice_controls_and_states(self):
        page = self.client.get("/")
        body = page.text
        for snippet in [
            'id="listen"',
            'id="stop"',
            "voice-logic.js",
        ]:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, body)
        app = self.client.get("/static/app.js").text
        for state in ["Listening", "Transcribing", "thinking", "Speaking", "idle"]:
            with self.subTest(state=state):
                self.assertIn(state, app)

    def test_voice_logic_script_is_served(self):
        script = self.client.get("/static/voice-logic.js")
        self.assertEqual(script.status_code, 200)
        self.assertIn("javascript", script.headers["content-type"])
        self.assertIn("resolveCapability", script.text)


if __name__ == "__main__":
    unittest.main()
