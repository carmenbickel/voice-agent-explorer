import unittest

from backend import graph_rag, main
from backend.graph_rag import (
    GraphError,
    expand_articles,
    graph_assisted_chunks,
    load_graph,
)
from backend.rag import build_index, load_articles


class GraphLoadingTests(unittest.TestCase):
    def test_curated_graph_loads(self):
        graph = load_graph()
        self.assertGreater(len(graph["nodes"]), 0)
        self.assertGreater(len(graph["edges"]), 0)

    def test_duplicate_nodes_rejected(self):
        with self.assertRaises(GraphError):
            _load({"nodes": [
                {"id": "n1", "type": "policy", "label": "A"},
                {"id": "n1", "type": "policy", "label": "A"},
            ], "edges": []})

    def test_unknown_references_rejected(self):
        with self.assertRaises(GraphError):
            _load({"nodes": [{"id": "n1", "type": "policy", "label": "A"}],
                   "edges": [{"from": "n1", "to": "ghost",
                              "relation": "guides", "article_id": "x",
                              "article_version": 1}]})

    def test_unsupported_relation_rejected(self):
        with self.assertRaises(GraphError):
            _load({"nodes": [{"id": "n1", "type": "policy", "label": "A"},
                             {"id": "n2", "type": "policy", "label": "B"}],
                   "edges": [{"from": "n1", "to": "n2",
                              "relation": "gossip",
                              "article_id": "a", "article_version": 1}]})

    def test_edges_without_provenance_rejected(self):
        with self.assertRaises(GraphError):
            _load({"nodes": [{"id": "n1", "type": "policy", "label": "A"},
                             {"id": "n2", "type": "policy", "label": "B"}],
                   "edges": [{"from": "n1", "to": "n2",
                              "relation": "guides"}]})

    def test_customer_and_order_nodes_rejected(self):
        with self.assertRaises(GraphError):
            _load({"nodes": [{"id": "customer_maya", "type": "policy",
                              "label": "x"}], "edges": []})

    def test_unsupported_node_type_rejected(self):
        with self.assertRaises(GraphError):
            _load({"nodes": [{"id": "n1", "type": "secret", "label": "x"}],
                   "edges": []})


def _load(data):
    import tempfile, json as json_module, os
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "graph.json")
        with open(path, "w") as handle:
            json_module.dump(data, handle)
        return graph_rag.load_graph(path)


class GraphExpansionTests(unittest.TestCase):
    def setUp(self):
        self.graph = load_graph()
        self.index = build_index(corpus=load_articles())

    def test_bounded_two_hop_traversal_reports_paths(self):
        article_ids, paths = expand_articles(
            self.graph, ["article-exchanges-policy"])
        self.assertIn("article-returns-policy", article_ids)
        self.assertTrue(paths)
        for path in paths:
            self.assertLessEqual(len(path["path"]) - 1,
                                 graph_rag.MAX_TRAVERSE_HOPS)
            self.assertTrue(path["relation"] in
                            graph_rag.ALLOWED_RELATIONS)

    def test_expansion_size_capped(self):
        article_ids, _ = expand_articles(
            self.graph,
            [article["id"] for article in load_articles()["articles"]])
        self.assertLessEqual(len(article_ids),
                             graph_rag.MAX_EXPANDED_ARTICLES)

    def test_no_customer_or_order_leakage_in_graph(self):
        text = str(self.graph).lower()
        for token in ("customer_maya", "customer_leo", "order_maya", "demo_leo"):
            self.assertNotIn(token, text)

    def test_expansion_does_not_bypass_scope_filters(self):
        results = graph_assisted_chunks(
            self.index, "Can I exchange size 39 for size 40?", self.graph)
        self.assertTrue(all(
            chunk["scope"] == "public-shop" and chunk["language"] == "en"
            for chunk in results))


class GraphComparisonTests(unittest.TestCase):
    def test_graph_mode_same_budget_and_questions(self):
        from backend.rag_eval import evaluate, evaluate_graph
        index = build_index(corpus=load_articles())
        graph = load_graph()
        doc_results, doc_summary = evaluate(index)
        graph_results, graph_summary, _ = evaluate_graph(index, graph)
        self.assertEqual(doc_summary["questions"],
                         graph_summary["graph_questions"])
        self.assertTrue(graph_summary["abstention_respected"])
        self.assertIn("mean_expansion_size", graph_summary)
        self.assertIn("non_improvements", graph_summary)


if __name__ == "__main__":
    unittest.main()
