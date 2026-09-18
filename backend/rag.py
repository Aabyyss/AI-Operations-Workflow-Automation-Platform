"""RAG layer — ingestion, chunking, retrieval.

Retrieval runs on a dependency-free TF-IDF + cosine similarity index so the
demo works offline, with a drop-in OpenAI-embeddings path for live mode.
Same interface either way: retrieve(query) -> list[RetrievedChunk].
"""
from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

from . import config
from .models import RetrievedChunk

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


class LiveIndex(TfidfIndex):
    """Same retrieval contract, but scoring via OpenAI embeddings when configured."""

    def fit(self, docs: list[dict]) -> None:  # noqa: D102
        super().fit(docs)
        if config.is_live():
            try:
                from openai import OpenAI
                client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL)
                resp = client.embeddings.create(
                    model=config.EMBED_MODEL,
                    input=[d["text"] for d in docs],
                )
                self.embeddings = [e.embedding for e in resp.data]
            except Exception:
                self.embeddings = []

    def search(self, query: str, k: int = 4) -> list[tuple[int, float]]:  # noqa: D102
        if config.is_live() and getattr(self, "embeddings", None):
            try:
                from openai import OpenAI
                client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL)
                q = client.embeddings.create(model=config.EMBED_MODEL, input=[query]).data[0].embedding
                sims = []
                for vec in self.embeddings:
                    dot = sum(a * b for a, b in zip(q, vec))
                    nq = math.sqrt(sum(a * a for a in q)) or 1.0
                    nv = math.sqrt(sum(b * b for b in vec)) or 1.0
                    sims.append(dot / (nq * nv))
                ranked = sorted(enumerate(sims), key=lambda x: -x[1])[:k]
                return [(i, round(s, 4)) for i, s in ranked if s > 0]
            except Exception:
                pass
        return super().search(query, k)


class Retriever:
    """Loads knowledge_base/*.md, chunks, indexes, retrieves. Singleton."""

    def __init__(self) -> None:
        self.index = LiveIndex()
        self.reload()

    def reload(self) -> int:
        docs: list[dict] = []
        if config.KNOWLEDGE_DIR.exists():
            for path in sorted(config.KNOWLEDGE_DIR.glob("*.md")):
                docs.extend(chunk_markdown(path))
        self.index.fit(docs)
        return len(docs)

    def retrieve(self, query: str, k: int = 4) -> list[RetrievedChunk]:
        out: list[RetrievedChunk] = []
        for i, score in self.index.search(query, k=k):
            d = self.index.docs[i]
            out.append(RetrievedChunk(doc_id=d["doc_id"], title=d["title"],
                                      text=d["text"][:900], score=score))
        return out


retriever = Retriever()
