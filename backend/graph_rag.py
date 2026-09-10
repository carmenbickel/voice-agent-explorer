"""Optional curated graph-assisted retrieval (issue C2).

Graph loading is strict: duplicate nodes, unknown edge references, unsupported
relations, and edges without article/version provenance are rejected. Customer
and order identities must not enter the public graph. Traversal follows only
allowlisted relations for a bounded two hops and a candidate bound, records
graph paths, and never bypasses scope/language/version filters. Both modes
(document-only and graph-assisted) share the corpus, embedder, context limit,
and evaluation set; the comparison is honest about cases where graph
assistance does not improve quality.
"""

import json
import os
from pathlib import Path

GRAPH_PATH = Path(__file__).resolve().parent.parent / "knowledge" / "graph.json"
ALLOWED_RELATIONS = frozenset((
    "guides", "care_guide", "related_policy"))
ALLOWED_NODE_TYPES = frozenset(("product", "feature", "policy",
                                "article-family"))
FORBIDDEN_NODE_PREFIXES = ("customer", "order", "op_", "ret_", "exg_")
MAX_TRAVERSE_HOPS = 2
MAX_EXPANDED_ARTICLES = 8


class GraphError(Exception):
    pass


def load_graph(path=GRAPH_PATH):
    path = Path(path)
    if not path.is_file() or os.environ.get("RAG_GRAPH_DISABLED") == "1":
        return {"nodes": {}, "edges": [], "node_order": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    node_ids = []
    nodes = {}
    for node in data.get("nodes", []):
        if not isinstance(node, dict) or "id" not in node or "type" not in node:
            raise GraphError("malformed node")
        if node["id"] in nodes:
            raise GraphError(f"duplicate node id: {node['id']}")
        if node["type"] not in ALLOWED_NODE_TYPES:
            raise GraphError(f"unsupported node type: {node['type']}")
        if any(node["id"].lower().startswith(prefix)
               for prefix in FORBIDDEN_NODE_PREFIXES):
            raise GraphError(
                f"private or business namespace forbidden in graph: {node['id']}")
        nodes[node["id"]] = node
        node_ids.append(node["id"])
    for edge in data.get("edges", []):
        if not isinstance(edge, dict):
            raise GraphError("malformed edge")
        if edge.get("from") not in nodes or edge.get("to") not in nodes:
            raise GraphError(
                f"edge references unknown node: {edge.get('from')}->{edge.get('to')}")
        if edge.get("relation") not in ALLOWED_RELATIONS:
            raise GraphError(f"unsupported relation: {edge.get('relation')}")
        article_id = edge.get("article_id")
        version = edge.get("article_version")
        if not article_id or version is None:
            raise GraphError(
                f"edge {edge.get('from')}->{edge.get('to')} lacks article/version provenance")
        if not str(version).isdigit():
            raise GraphError("edge version must be an integer")
    return {"nodes": nodes, "edges": data.get("edges", []),
            "node_order": node_ids}


def expand_articles(graph, seed_article_ids):
    """Bounded traversal over allowlisted relations (<=2 hops).

    Seeds are the nodes whose provenance edges point at the retrieved
    articles; returns (article_ids, paths).
    """
    edges_by_from = {}
    for edge in graph["edges"]:
        edges_by_from.setdefault(edge["from"], []).append(edge)

    seed_nodes = sorted({
        edge["from"]
        for edge in graph["edges"]
        if edge["article_id"] in set(seed_article_ids)
    })
    expanded = []
    paths = []
    frontier = [(node_id, [node_id], 0) for node_id in seed_nodes]
    visited = set(seed_nodes)
    while frontier and len(expanded) < MAX_EXPANDED_ARTICLES:
        node_id, path, hops = frontier.pop(0)
        if hops >= MAX_TRAVERSE_HOPS:
            continue
        for edge in edges_by_from.get(node_id, []):
            target = edge["to"]
            new_path = path + [target]
            next_hops = hops + 1
            expanded.append((edge["article_id"], edge["article_version"],
                             target, hops + 1))
            paths.append({
                "via_article": edge["article_id"],
                "article_version": edge["article_version"],
                "relation": edge["relation"],
                "path": new_path,
            })
            if target not in visited and len(expanded) < MAX_EXPANDED_ARTICLES:
                visited.add(target)
                frontier.append((target, new_path, hops + 1))
            if len(expanded) >= MAX_EXPANDED_ARTICLES:
                break
    return sorted({article_id for article_id, _, _, _ in expanded})[:MAX_EXPANDED_ARTICLES], \
        paths


def graph_assisted_chunks(index, query, graph, language="en",
                          scope="public-shop", effective_date=None,
                          seed_retriever=None, limit=4):
    """Retrieval with graph expansion; articles reach the candidate set
    without bypassing scope/language/version filters."""
    from backend.rag import retrieve
    seed_retrieved = retrieve(index, query, language=language, scope=scope,
                              effective_date=effective_date, limit=limit)
    if not seed_retrieved:
        return []
    seed_article_ids = sorted({chunk["article_id"] for chunk in seed_retrieved})
    expanded_article_ids, paths = expand_articles(graph, seed_article_ids)
    new_article_ids = [article for article in expanded_article_ids
                       if article not in seed_article_ids]
    candidate_chunks = [
        chunk for chunk in index.chunks
        if chunk["article_id"] in seed_article_ids + new_article_ids
        and chunk["language"] == language
        and chunk["scope"] == scope
        and (effective_date is None or chunk["effective_from"] <= effective_date)
        and (effective_date is None or chunk["effective_until"] is None
             or chunk["effective_until"] >= effective_date)
    ]
    from backend.rag import matched_terms, content_terms
    query_word_set = content_terms(query)
    scored = []
    for chunk in candidate_chunks:
        matched = len(matched_terms(query_word_set,
                                    index.chunk_terms.get(chunk["chunk_id"], frozenset())))
        if matched < 2:
            continue
        entry = dict(chunk)
        entry["score"] = round(matched / max(1, len(query_word_set)), 4)
        entry["graph_paths"] = [
            " -> ".join(path["path"]) for path in paths
            if path["via_article"] == chunk["article_id"]]
        scored.append(entry)
    scored.sort(key=lambda entry: entry["score"], reverse=True)
    return scored[:limit]
