"""Document wiki and RAG baseline (issue C1).

Ingestion is deterministic: heading-aware chunks, SHA-256 article hashes, and
strict rejection of duplicate or invalid metadata. Retrieval is bounded
cosine similarity with language/scope/version filters and a strict context
limit. Answers may cite only passages actually retrieved; fabricated citation
tags are removed. Business facts (live stock, price, ownership, eligibility)
are never sourced from documents; those belong to the catalog and the later
tool issues. All fixture content is draft demo data pending review.
"""

import hashlib
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from backend.ollama_client import OllamaError
from backend import ollama_client


ARTICLES_DIRECTORY = Path(__file__).resolve().parent.parent / "knowledge" / "articles"
CHUNKING_VERSION = "heading-split-v1"
RAG_GENERATOR_VERSION = "rag-baseline-v1"
MAX_RETRIEVED_CHUNKS = 4
SECTION_HEADING = re.compile(r"^##\s+")
CITATION_TAG = re.compile(r"\[src:([A-Za-z0-9_.:-]+)\]")

_FRONT_MATTER_KEYS = ("id", "version", "language", "scope", "effective_from",
                      "effective_until", "title")

EVIDENCE_INSTRUMENT = (
    "Answer using the evidence above. If the evidence is insufficient to"
    " answer, ask a clarifying question or clearly state that the knowledge"
    " article is not enough. Cite passages as [src:<id>]."
    " Never invent stock, prices, ownership, or eligibility;"
    " business facts must be confirmed by catalog tools, not from memory."
)


class IngestionError(Exception):
    pass


def embed_model() -> str:
    """Embedder version string recorded in the manifest.

    Default is the deterministic local hashing embedder (no network, fully
    reproducible). Set OLLAMA_EMBED_MODEL to a model on an embedding-capable
    Ollama server to use neural embeddings instead.
    """
    return os.environ.get("OLLAMA_EMBED_MODEL", "hash-256-v1")


HASH_EMBED_DIMENSION = 256
_TOKEN = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "a an and are as at be but by can for from has have how i in is it its "
    "my of on or should that the this to was what when which with you your "
    "do does were will shall must may".split())


def _hash_embedding(text, dimension=HASH_EMBED_DIMENSION):
    """Deterministic bag-of-content-words feature hashing; local, reproducible."""
    vector = [0.0] * dimension
    for token in _TOKEN.findall((text or "").lower()):
        if token in STOPWORDS:
            continue
        bucket = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16) % dimension
        vector[bucket] += 1.0
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def local_embed(text):
    """Local deterministic embedding generation (feature hashing)."""
    return _hash_embedding(text)


def embed(text):
    """Embed text via the configured embedder (default: local hashing)."""
    if embed_model() == "hash-256-v1" or not embed_model().startswith("ollama:"):
        if os.environ.get("OLLAMA_EMBED_MODEL") is None:
            return local_embed(text)
    response = ollama_client.httpx.post(
        f"{ollama_client.OLLAMA_BASE_URL}/api/embeddings",
        json={"model": embed_model(), "prompt": text},
        timeout=30,
    )
    if response.status_code != 200:
        raise OllamaError(f"Embedding model returned status {response.status_code}")
    embedding = response.json().get("embedding")
    if not isinstance(embedding, list) or not embedding or \
            not all(isinstance(value, (int, float)) for value in embedding):
        raise OllamaError("Embedding response malformed")
    return embedding


def parse_front_matter(text):
    """Return (metadata, body) for a well-formed article, else None."""
    match = re.match(r"\A---\n(.*?)\n---\n(.*)\Z", text, re.S)
    if not match:
        return None
    metadata = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key not in _FRONT_MATTER_KEYS or not value:
            return None
        try:
            metadata[key] = int(value) if key == "version" else (
                None if value == "null" else value)
        except ValueError:
            return None
    if any(key not in metadata for key in _FRONT_MATTER_KEYS):
        return None
    if metadata["effective_until"] and metadata["effective_from"] > metadata["effective_until"]:
        return None
    return metadata, match.group(2)


def _slugify(heading):
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-") or "overview"


def chunk_article(metadata, body, source_path):
    """Split a body into heading-aware chunks with stable IDs."""
    chunks = []
    heading = "overview"
    lines = []

    def flush():
        text = "\n".join(lines).strip()
        if not text:
            return
        chunk_id = f"{metadata['id']}::{_slugify(heading)}"
        if any(chunk["chunk_id"] == chunk_id for chunk in chunks):
            raise IngestionError(
                f"duplicate chunk id {chunk_id!r} in {metadata['id']}")
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        chunks.append({
            "chunk_id": chunk_id,
            "article_id": metadata["id"],
            "version": metadata["version"],
            "language": metadata["language"],
            "scope": metadata["scope"],
            "effective_from": metadata["effective_from"],
            "effective_until": metadata["effective_until"],
            "heading": heading,
            "text": text,
            "source_path": source_path,
            "hash": text_hash,
        })

    for line in body.splitlines():
        if SECTION_HEADING.match(line):
            flush()
            heading = line.lstrip("# ").strip()
        else:
            lines.append(line)
    flush()
    return chunks


def load_articles(directory=ARTICLES_DIRECTORY):
    """Load and chunk all valid articles; invalid metadata is rejected."""
    directory = Path(directory)
    if not directory.is_dir():
        return {"chunks": [], "articles": [], "hashes": {}}
    article_ids = set()
    articles = []
    article_hashes = {}
    chunks = []
    for path in sorted(directory.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        parsed = parse_front_matter(raw)
        if parsed is None:
            raise IngestionError(f"malformed metadata in {path.name}")
        metadata, body = parsed
        if metadata["id"] in article_ids:
            raise IngestionError(f"duplicate article id: {metadata['id']}")
        article_ids.add(metadata["id"])
        article_hashes[metadata["id"]] = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        articles.append(metadata)
        chunks.extend(chunk_article(metadata, body, path.name))
    return {"chunks": chunks, "articles": articles, "hashes": article_hashes}


def ingest_manifest(corpus=None, directory=ARTICLES_DIRECTORY) -> dict:
    """Deterministic manifest: generator/chunking/embedder versions + hashes."""
    corpus = corpus or load_articles(directory)
    article_ids = sorted(article["id"] for article in corpus["articles"])
    article_hashes = {} if not corpus["hashes"] else \
        corpus["hashes"]
    return {
        "generator": RAG_GENERATOR_VERSION,
        "chunking": CHUNKING_VERSION,
        "embedder": f"{embed_model()}:1",
        "articles": article_ids,
        "article_count": len(article_ids),
        "chunk_count": len(corpus["chunks"]),
        "max_chunks_per_query": MAX_RETRIEVED_CHUNKS,
        "article_hashes": article_hashes,
    }


@dataclass
class ArticleIndex:
    chunks: list = field(default_factory=list)
    chunk_embeddings: dict = field(default_factory=dict)
    chunk_terms: dict = field(default_factory=dict)  # for coverage scoring

    def chunk_by_id(self, chunk_id):
        for chunk in self.chunks:
            if chunk["chunk_id"] == chunk_id:
                return chunk
        return None


def build_index(corpus=None, embed_fn=embed, directory=ARTICLES_DIRECTORY) -> ArticleIndex:
    corpus = corpus or load_articles(directory)
    index = ArticleIndex(chunks=corpus["chunks"])
    for chunk in corpus["chunks"]:
        index.chunk_embeddings[chunk["chunk_id"]] = embed_fn(
            f"{chunk['heading']}\n{chunk['text']}")
        index.chunk_terms[chunk["chunk_id"]] = \
            frozenset(_TOKEN.findall((chunk["heading"] + " " + chunk["text"]).lower())) - STOPWORDS
    return index


def content_terms(text):
    """The distinct content words of a question; used for coverage scoring."""
    return frozenset(_TOKEN.findall((text or "").lower())) - STOPWORDS


def coverage(index, chunk, query):
    """Deterministic coverage scoring for the local hashing mode."""
    query_terms = content_terms(query)
    if not query_terms:
        return 0.0
    chunk_terms = index.chunk_terms.get(chunk["chunk_id"], frozenset())
    return len(query_terms & chunk_terms) / len(query_terms)


def build_index(corpus=None, embed_fn=embed, directory=ARTICLES_DIRECTORY) -> ArticleIndex:
    corpus = corpus or load_articles(directory)
    index = ArticleIndex(chunks=corpus["chunks"])
    for chunk in corpus["chunks"]:
        index.chunk_embeddings[chunk["chunk_id"]] = embed_fn(
            f"{chunk['heading']}\n{chunk['text']}")
        index.chunk_terms[chunk["chunk_id"]] = \
            frozenset(_TOKEN.findall((chunk["heading"] + " " + chunk["text"]).lower())) - STOPWORDS
    return index


def content_terms(text):
    """The distinct content words of a question; used for coverage scoring."""
    return frozenset(_TOKEN.findall((text or "").lower())) - STOPWORDS


def matched_terms(query_terms, chunk_terms):
    """Content words that match, allowing lightweight stem prefixes."""
    matched = set()
    for left in query_terms:
        for right in chunk_terms:
            if left == right or (len(left) >= 4 and len(right) >= 4 and (
                    left.startswith(right) or right.startswith(left))):
                matched.add(left)
    return matched


def coverage(index, chunk, query):
    """Deterministic content-word coverage for the local hashing mode."""
    query_terms = content_terms(query)
    if not query_terms:
        return 0.0
    chunk_terms = index.chunk_terms.get(chunk["chunk_id"], frozenset())
    return len(matched_terms(query_terms, chunk_terms)) / len(query_terms)

def _cosine(left, right):
    dot = sum(l_r[0] * l_r[1] for l_r in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def retrieve(index: ArticleIndex, query: str, language="en", scope="public-shop",
             effective_date=None, limit=MAX_RETRIEVED_CHUNKS,
             embed_fn=embed, min_coverage=0.3) -> list:
    """Bounded retrieval: at most `limit` chunks.

    The local default embedder scores coverage: the fraction of the
    question's content words present in the chunk. Chunks below
    `min_coverage` are not returned, so unknown topics abstain instead of
    grounding weak evidence. Neural embeddings (OLLAMA_EMBED_MODEL) score
    with cosine instead.
    """
    query = (query or "").strip()
    if len(query) < 3:
        return []
    candidates = [
        chunk for chunk in index.chunks
        if chunk["language"] == language
        and (scope is None or chunk["scope"] == scope)
        and (effective_date is None or chunk["effective_from"] <= effective_date)
        and (effective_date is None or chunk["effective_until"] is None
             or chunk["effective_until"] >= effective_date)
    ]
    if not candidates:
        return []
    local_mode = os.environ.get("OLLAMA_EMBED_MODEL") is None
    scored = []
    if local_mode:
        query_terms = content_terms(query)
        for chunk in candidates:
            chunk_terms = index.chunk_terms.get(chunk["chunk_id"], frozenset())
            matched = matched_terms(query_terms, chunk_terms)
            # At least two distinct content words (after lightweight stem
            # matching) must overlap so unrelated questions never ground.
            if len(matched) >= 2:
                entry = dict(chunk)
                entry["score"] = round(len(matched) / len(query_terms), 4)
                scored.append(entry)
    else:
        query_embedding = embed_fn(query)
        for chunk in candidates:
            chunk_embedding = index.chunk_embeddings.get(chunk["chunk_id"])
            if chunk_embedding is None:
                continue
            entry = dict(chunk)
            entry["score"] = round(_cosine(query_embedding, chunk_embedding), 4)
            scored.append(entry)
    # Highest-scoring bounded selection; below-threshold chunks mean the
    # topic is unknown (abstention rather than weak evidence).
    scored.sort(key=lambda entry: entry["score"], reverse=True)
    return scored[:max(0, int(limit))]


def validate_citations(text, retrieved):
    """Return the citation tags used; fabricated tags are reported missing."""
    used = CITATION_TAG.findall(text or "")
    allowed = {chunk["chunk_id"] for chunk in (retrieved or [])}
    return [tag for tag in used if tag in allowed], \
        [tag for tag in used if tag not in allowed]


def remove_invalid_citations(text, retrieved):
    """Strip fabricated citation tags from a model answer."""
    allowed = {chunk["chunk_id"] for chunk in (retrieved or [])}
    return CITATION_TAG.sub(
        lambda match: match.group(0) if match.group(1) in allowed else "",
        text or "")


def build_evidence_context(retrieved_chunks):
    """Evidence system prompt section for citation-safe answers."""
    lines = ["Available knowledge excerpts."]
    for chunk in retrieved_chunks:
        flat = " ".join(chunk["text"].split()).replace("[", " ").replace("]", " ")
        lines.append(f"[src:{chunk['chunk_id']}] ({chunk['heading']}) {flat}")
    return "\n".join(lines)


def assemble_messages(evidence_text, use_style=None):
    """Model messages list: evidence instruction + strict citation style."""
    from backend.agent import SYSTEM_PROMPT
    instruction = EVIDENCE_INSTRUMENT
    style = (
        " When enough evidence exists, answer in one short paragraph and"
        " cite the article passages you used as [src:<chunk_id>]."
    ) if use_style is None else use_style
    evidence_block = (
        "Shop knowledge quotes (grounding). Do not use outside facts"
        " for shop topics.\n" + (evidence_text or "")
    ) if evidence_text else ""
    return [
        {"role": "system",
         "content": SYSTEM_PROMPT + "\n\nWhen shop knowledge evidence is"
                    " supplied below, follow the evidence instructions.\n"},
        {"role": "system", "content":
            (evidence_text + "\n Instructions: " + instruction + style)
            if evidence_text else ""},
    ]
