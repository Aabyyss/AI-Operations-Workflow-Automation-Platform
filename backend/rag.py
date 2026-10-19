"""RAG layer — ingestion, chunking, retrieval.

Retrieval runs on a dependency-free TF-IDF + cosine index so the demo, the
PI tests and CI work with no key and no database. In live mode the same
`retrieve(query) -> list[RetrievedChunk]` call is served by embeddings
instead — in this process, or from pgvector when a Postgres backend is
configured (backend/vectors.py) — and falls back to TF-IDF if the provider
or the database is unavailable.

TF-IDF is always fitted, even when embeddings are used: it is the fallback
that keeps a live deployment answering tickets during an API outage, and it
costs milliseconds.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

from . import config
from .models import RetrievedChunk
from .vectors import build_vector_index, embed_texts

WORD_RE = re.compile(r"[a-z0-9']+")


def tokenize(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def chunk_markdown(path: Path) -> list[dict]:
    """Split a policy doc on '## ' headings; keep title + doc id on chunks."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    doc_id = path.stem
    title = text.splitlines()[0].lstrip("# ").strip() if text else doc_id
    sections = re.split(r"\n(?=## )", text)
    chunks: list[dict] = []
    for i, sec in enumerate(sections):
        body = sec.strip()
        if len(body) < 40:
            continue
        head = body.splitlines()[0].lstrip("# ").strip()
        chunks.append({
            "doc_id": doc_id,
            "title": f"{title} — {head}" if head and head != title else title,
            "text": body,
            "chunk_index": i,
        })
    return chunks


class TfidfIndex:
    """Minimal TF-IDF vector space with cosine similarity. Honest math."""

    def __init__(self) -> None:
        self.docs: list[dict] = []
        self.idf: dict[str, float] = {}
        self.vectors: list[dict[str, float]] = []

    def fit(self, docs: list[dict]) -> None:
        self.docs = docs
        df: Counter = Counter()
        for d in docs:
            df.update(set(tokenize(d["text"])))
        n = max(1, len(docs))
        self.idf = {t: math.log(n / c) + 1.0 for t, c in df.items()}
        self.vectors = [self._vector(d["text"]) for d in docs]

    def _vector(self, text: str) -> dict[str, float]:
        tf = Counter(tokenize(text))
        vec = {t: (1 + math.log(c)) * self.idf.get(t, 1.0) for t, c in tf.items() if t in self.idf}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    def search(self, query: str, k: int = 4) -> list[tuple[int, float]]:
        q = self._vector(query)
        scores = [
            sum(w * q.get(t, 0.0) for t, w in vec.items())
            for vec in self.vectors
        ]
        ranked = sorted(enumerate(scores), key=lambda x: -x[1])[:k]
        return [(i, round(s, 4)) for i, s in ranked if s > 0]


class Retriever:
    """Loads knowledge_base/*.md, chunks, indexes, retrieves. Singleton.

    `index` is always the TF-IDF fallback; `vectors` is the embedding index
    when one could be built (memory or pgvector). Retrieval prefers vectors
    and degrades rather than failing: an embeddings outage must not stop a
    support ticket from being answered.
    """

    def __init__(self) -> None:
        self.index = TfidfIndex()
        self.vectors = None
        self.reload()

    def reload(self) -> int:
        docs: list[dict] = []
        if config.KNOWLEDGE_DIR.exists():
            for path in sorted(config.KNOWLEDGE_DIR.glob("*.md")):
                docs.extend(chunk_markdown(path))
        self.index.fit(docs)
        self.vectors = None
        if config.is_live():
            try:
                self.vectors = build_vector_index(docs)
            except Exception:
                # No provider, no driver, no pgvector extension: TF-IDF keeps
                # working. This is the zero-config path, not an incident.
                self.vectors = None
        return len(docs)

    def backend(self) -> str:
        """Which retriever is actually serving — for health and docs."""
        if self.vectors is None:
            return "tfidf"
        return "pgvector" if type(self.vectors).__name__ == "PgVectorIndex" else "embeddings"

    def retrieve(self, query: str, k: int = 4) -> list[RetrievedChunk]:
        if self.vectors is not None:
            try:
                hits = self.vectors.search(embed_texts([query])[0], k=k)
                if hits:
                    return hits
            except Exception:
                pass  # fall through to TF-IDF rather than fail the ticket
        out: list[RetrievedChunk] = []
        for i, score in self.index.search(query, k=k):
            d = self.index.docs[i]
            out.append(RetrievedChunk(doc_id=d["doc_id"], title=d["title"],
                                      text=d["text"][:900], score=score))
        return out


retriever = Retriever()
