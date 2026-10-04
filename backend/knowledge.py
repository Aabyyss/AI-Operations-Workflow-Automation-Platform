"""Knowledge-base management — the corpus is data, not a deploy artifact.

Retrieval quality is only as good as the markdown in knowledge_base/.
Until now the only way to fix a stale policy was to edit files on the
server and call /api/knowledge/reload. These functions back a small
management API: list, read, create/update, and delete documents, with
slug sanitization (no path traversal) and an explicit overwrite flag so
an update never clobbers a policy by accident.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from . import config
from .rag import chunk_markdown

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def slugify(name: str) -> str:
    """'Refund Policy v2' -> 'refund-policy-v2' (filesystem-safe, traversal-proof).

    Names that would only be safe by being silently rewritten — path
    separators, leading dots, '..' — are rejected outright: an operator
    asking to create '.../evil' must hear 'no', not discover a file
    called 'evil.md' appear somewhere else.
    """
    if ".." in name or "/" in name or "\\" in name or name.strip().startswith("."):
        raise ValueError("name must not contain paths, '..' or leading dots")
    slug = _SLUG_RE.sub("-", name.strip().lower().replace(" ", "-"))
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if not slug or not re.search(r"[a-z0-9]", slug):
        raise ValueError("name must contain at least one letter or digit")
    return slug


def _path_for(doc_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", doc_id):
        raise ValueError("invalid document id")
    return config.KNOWLEDGE_DIR / f"{doc_id}.md"


def list_documents() -> list[dict]:
    """Every policy document with its chunk count and last-modified time."""
    docs: list[dict] = []
    for path in sorted(config.KNOWLEDGE_DIR.glob("*.md")):
        try:
            chunks = chunk_markdown(path)
        except OSError:
            continue
        stat = path.stat()
        docs.append({
            "doc_id": path.stem,
            "title": (chunks[0]["title"].split(" — ")[0] if chunks else path.stem),
            "chunks": len(chunks),
            "bytes": stat.st_size,
            "modified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)),
        })
    return docs


def read_document(doc_id: str) -> dict | None:
    try:
        path = _path_for(doc_id)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="ignore")
    return {"doc_id": doc_id, "markdown": text, "chunks": len(chunk_markdown(path))}


class DocumentExistsError(LookupError):
    """Raised when saving over an existing doc without overwrite=True."""


def save_document(name: str, markdown: str, overwrite: bool = False) -> dict:
    """Create or update a policy document, then return what changed."""
    slug = slugify(name)
    if not markdown or not markdown.strip():
        raise ValueError("markdown must not be empty")
    path = _path_for(slug)
    config.KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise DocumentExistsError(f"document already exists: {slug}")
    created = not path.exists()
    path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    chunks = chunk_markdown(path)
    return {"doc_id": slug, "created": created, "chunks": len(chunks),
            "bytes": path.stat().st_size}


def delete_document(doc_id: str) -> bool:
    path = _path_for(doc_id)
    if not path.exists():
        return False
    path.unlink()
    return True
