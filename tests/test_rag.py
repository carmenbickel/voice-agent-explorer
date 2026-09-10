import unittest
import unittest.mock as mock

from fastapi.testclient import TestClient

from backend import main
from backend.sessions import SessionManager
from backend.traces import TraceStore


def build_index_and_modules():
    import backend.rag as rag
    return rag


class RAGIngestionTests(unittest.TestCase):
    def setUp(self):
        self.rag = build_index_and_modules()
        self.corpus = self.rag.load_articles()

    def test_corpus_contains_ten_to_fifteen_articles(self):
        ids = sorted({chunk["article_id"] for chunk in self.corpus["chunks"]})
        self.assertGreaterEqual(len(ids), 10)
        self.assertLessEqual(len(ids), 15)
        self.assertEqual(len(ids), len(set(ids)))

    def test_manifest_records_versions_and_hashes(self):
        manifest = self.rag.ingest_manifest(self.corpus)
        self.assertEqual(manifest["article_count"], 12)
        self.assertEqual(all(
            len(article_hash) == 12
            for article_hash in manifest["article_hashes"].values()), True)
        self.assertEqual(manifest["chunking"], "heading-split-v1")
        self.assertIn("embedder", manifest)

    def test_duplicate_chunk_heading_rejected(self):
        with self.assertRaises(self.rag.IngestionError):
            self.rag.chunk_article(
                {"id": "article-x", "version": 1, "language": "en",
                 "scope": "public-shop", "effective_from": "2026-06-01",
                 "effective_until": None, "title": "Duplicate"},
                "## A\nsame text\n\n## A\nsame text again",
                "source.md")

    def test_thumbnail_invalid_metadata_rejected(self):
        self.assertIsNone(self.rag.parse_front_matter(
            "---\nid: article-broken\nbroken: yes\n---\nBody"))
        self.assertIsNone(self.rag.parse_front_matter(
            "---\nid: article-broken\nversion: notanint\nlanguage: en\n"
            "scope: s\neffective_from: 2026-06-01\ntitle: x\n---\nBody"))

    def test_chunks_carry_stable_metadata(self):
        chunk = self.corpus["chunks"][0]
        self.assertIn("::", chunk["chunk_id"])
        self.assertEqual(chunk["version"], 1)
        self.assertEqual(chunk["language"], "en")
        self.assertIn(".md", chunk["source_path"])
        self.assertEqual(len(chunk["hash"]), 12)


class RAGRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.rag = build_index_and_modules()
        self.index = self.rag.build_index(corpus=self.rag.load_articles())

    def test_bounded_retrieval_returns_at_most_the_limit(self):
        results = self.rag.retrieve(
            self.index, "How long do I have to return unworn shoes?")
        self.assertLessEqual(len(results), 4)
        self.assertTrue(results)

    def test_unknown_scope_random_question_abstains(self):
        results = self.rag.retrieve(
            self.index, "Turquoise tie-dye wedding shoes with 3-inch heels")
        self.assertEqual(results, [])

    def test_citation_filtering(self):
        chunks = [{"chunk_id": "article-returns-policy::eligibility"}]
        self.assertEqual(
            self.rag.remove_invalid_citations(
                "Within 30 days [src:article-returns-policy::eligibility]"
                " and [src:made-up-id]",
                chunks),
            "Within 30 days [src:article-returns-policy::eligibility] and ")

    def test_validate_citations_reports_fabrications(self):
        used, missing = self.rag.validate_citations(
            "a [src:article-returns-policy::eligibility]"
            " and [src:fabricated-id]",
            [{"chunk_id": "article-returns-policy::eligibility"}])
        self.assertEqual(used, ["article-returns-policy::eligibility"])
        self.assertEqual(missing, ["fabricated-id"])

    def test_evidence_block_carries_only_retrieved_text(self):
        chunks = [{"chunk_id": "article-returns-policy::eligibility",
                   "heading": "Eligibility",
                   "text": "Unworn shoes return within 30 days"}]
        context = self.rag.build_evidence_context(chunks)
        self.assertIn("[src:article-returns-policy::eligibility]", context)
        self.assertIn("Unworn shoes", context)


class RAGChatIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.rag = build_index_and_modules()
        patches = [
            mock.patch.object(main, "manager", SessionManager()),
            mock.patch.object(main, "traces", TraceStore()),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    @mock.patch("backend.agent.generate_response")
    def test_grounded_answer_filters_fabricated_citations(self, generate):
        client = TestClient(main.app)
        client.post("/sessions")
        generate.return_value = (
            "Return within 30 days of delivery"
            " [src:article-returns-policy::eligibility]"
            " and also [src:fabricated-id] regarding other rules.")
        response = client.post(
            "/chat",
            json={"message": "How long do I have to return unworn shoes?"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse("fabricated-id" in body["response"])
        self.assertTrue("article-returns-policy::eligibility" in body["response"])
        self.assertTrue(body["sources"])
        trace = client.get(f"/traces/{body['trace_id']}").json()
        retrieval_event = next(
            event for event in trace["events"] if event["stage"] == "retrieval")
        self.assertEqual(retrieval_event["status"], "executed")

    @mock.patch("backend.agent.generate_response")
    def test_chat_without_evidence_still_works(self, generate):
        client = TestClient(main.app)
        client.post("/sessions")
        generate.return_value = "It explains voice bots."
        response = client.post(
            "/chat", json={"message": "Tell me about telephone systems"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["response"], "It explains voice bots.")

    @mock.patch("backend.agent.generate_response")
    def test_invented_business_facts_instructed_against(self, generate):
        client = TestClient(main.app)
        client.post("/sessions")
        generate.return_value = "Sure! Sizes are EU 36 to 42"
        client.post("/chat", json={
            "message": "What sizes does the size guide article mention?"})
        messages = generate.call_args.args[0]
        self.assertTrue(any(
            "Never invent stock, prices, ownership" in message["content"]
            for message in messages))


class RAGEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.rag = build_index_and_modules()
        self.index = self.rag.build_index(corpus=self.rag.load_articles())

    def test_baseline_set_metrics(self):
        from backend.rag_eval import evaluate
        results, summary = evaluate(self.index)
        self.assertEqual(summary["questions"], 12)
        self.assertGreaterEqual(summary["mean_recall"], 0.8)
        self.assertTrue(summary["abstention_respected"])

    def test_repeatable_report(self):
        from backend.rag_eval import generate_report
        report = generate_report(self.index, write=False)
        self.assertEqual(report["generator"], "baseline-eval-v1")
        self.assertIn("summary", report)


if __name__ == "__main__":
    unittest.main()
