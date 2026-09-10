"""Fixed baseline evaluation set and repeatable results (issue C1).

`python -m backend.rag_eval` writes knowledge/eval/baseline-report.json and a
markdown companion using the live embedding model. The metric computation is
pure: tests pass a deterministic fake embedder and assert the math and
abstention behavior.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from backend.rag import retrieve


BASELINE_SET = [
    {
        "question": "How long do I have to return unworn shoes?",
        "expect_articles": ["article-returns-policy"],
        "topic": "policy:returns",
    },
    {
        "question": "Can I exchange size 39 for a size 40 of the same product?",
        "expect_articles": ["article-exchanges-policy"],
        "topic": "policy:exchange",
    },
    {
        "question": "Can I cancel a shipped order?",
        "expect_articles": ["article-cancellation-policy"],
        "topic": "policy:cancellation",
    },
    {
        "question": "When do demo orders arrive?",
        "expect_articles": ["article-shipping-overview"],
        "topic": "policy:shipping",
    },
    {
        "question": "Which EU sizes do Stepwise Shoes use?",
        "expect_articles": ["article-sizing-guide"],
        "topic": "sizing",
    },
    {
        "question": "How much space should toes have in new shoes?",
        "expect_articles": ["article-fit-and-comfort"],
        "topic": "fit",
    },
    {
        "question": "How should I clean inflatable waterproof hiking shoes?",
        "expect_articles": ["article-care-dfg-products"],
        "topic": "care",
    },
    {
        "question": "Is the demo membrane breathable?",
        "expect_articles": ["article-waterproof-technology"],
        "topic": "technology",
    },
    {
        "question": "What kind of shoes fit short city runs?",
        "expect_articles": ["article-running-form"],
        "topic": "running",
    },
    {
        "question": "Are payments real in this demo?",
        "expect_articles": ["article-faq-demo"],
        "topic": "demo-limits",
    },
    {
        "question": "What happens if my problem is more complex than a return?",
        "expect_articles": ["article-support-handover"],
        "topic": "handover",
    },
    {
        "question": "Turquoise tie-dye wedding shoes with 3-inch heels",
        "expect_articles": [],
        "topic": "abstention",
    },
]

EVAL_DIRECTORY = Path(__file__).resolve().parent.parent / "knowledge" / "eval"


def evaluate(index, questions=None, limit=4, context_limit=4, embed=None):
    """Compute deterministic retrieval metrics over the baseline set."""
    if questions is None:
        questions = BASELINE_SET
    results = []
    for entry in questions:
        if embed is None:
            retrieved = retrieve(index, entry["question"], limit=limit)
        else:
            retrieved = retrieve(index, entry["question"], limit=limit,
                                 embed_fn=embed)
        found = [chunk["article_id"] for chunk in retrieved]
        wants = entry["expect_articles"]
        in_context = [chunk["article_id"] for chunk in retrieved[:context_limit]]
        results.append({
            "topic": entry["topic"],
            "question": entry["question"],
            "expected": wants,
            "retrieved": found,
            "recall": _recall(wants, found),
            "abstains": not wants and not found,
            "expected_in_context": len(set(wants) & set(in_context)),
        })
    targeted = [result for result in results if result["expected"]]
    summary = {
        "questions": len(results),
        "mean_recall": round(sum(result["recall"] for result in targeted)
                             / max(1, len(targeted)), 3),
        "abstention_respected": all(
            result["abstains"] for result in results
            if not result["expected"]),
    }
    return results, summary


def _recall(targets, found):
    return round(len(set(targets) & set(found)) / len(targets), 3) if targets \
        else 1.0 if not found else 0.0


def generate_report(index, embed=None, write=True):
    results, summary = evaluate(index, embed=embed)
    report = {
        "generator": "baseline-eval-v1",
        "description": (
            "Document-only RAG baseline over the demo wiki. Metrics measure "
            "retrieval only; answer quality is not simulated here."
        ),
        "summary": summary,
        "results": results,
    }
    if write:
        EVAL_DIRECTORY.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        (EVAL_DIRECTORY / "baseline-report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        (EVAL_DIRECTORY / "baseline-report.md").write_text(
            _render_markdown(report), encoding="utf-8")
    return report


def _render_markdown(report):
    lines = [
        "# RAG baseline evaluation report",
        "",
        f"Generator: {report['generator']}",
        "",
    ]
    summary = report["summary"]
    for key, value in summary.items():
        lines.append(f"- **{key}**: {value}")
    lines.append("")
    lines.append("| Topic | Question | Expected | Retrieved | Recall |")
    lines.append("| --- | --- | --- | --- | --- |")
    for result in report["results"]:
        lines.append(
            f"| {result['topic']} | {result['question']} "
            f"| {', '.join(result['expected']) or '—'} "
            f"| {', '.join(result['retrieved'][:3]) or '—'} "
            f"| {result['recall']} |")
    return "\n".join(lines) + "\n"


def main():
    import argparse
    from backend.rag import build_index, load_articles
    parser = argparse.ArgumentParser(
        description="Run the fixed baseline RAG evaluation (needs Ollama).")
    parser.parse_args()
    index = build_index(corpus=load_articles())
    report = generate_report(index)
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
