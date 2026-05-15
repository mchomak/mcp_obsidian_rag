import argparse
import hashlib
import json
import logging
import re
import sys
import threading
from pathlib import Path
from typing import Any

import chromadb
import frontmatter
from tqdm import tqdm

from config import (
    VAULT_PATH,
    CHROMA_DB_PATH,
    COLLECTION_NAME,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    TOP_K_RESULTS,
)
from embeddings import embed_batch, embed_text, ping

logger = logging.getLogger(__name__)

_write_lock = threading.Lock()

_chroma = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
_collection = _chroma.get_or_create_collection(
    name=COLLECTION_NAME,
    metadata={"hnsw:space": "cosine"},
)


def get_collection():
    return _collection


# --- Path helpers ---

def _relative_source(path: Path) -> str:
    return path.relative_to(VAULT_PATH).as_posix()


def _is_hidden(rel_source: str) -> bool:
    return any(p.startswith(".") for p in rel_source.split("/"))


def _is_in_vault(path: Path) -> bool:
    try:
        path.relative_to(VAULT_PATH)
        return True
    except ValueError:
        return False


# --- Frontmatter ---

def _parse_file(path: Path) -> tuple[dict[str, Any], str]:
    try:
        post = frontmatter.load(path)
        meta = dict(post.metadata) if post.metadata else {}
        return meta, post.content or ""
    except Exception as exc:
        logger.warning("Frontmatter parse failed for %s (%s); reading raw.", path, exc)
        return {}, path.read_text(encoding="utf-8")


# --- Chunking ---

_HEADING_RE = re.compile(r"^(#{1,2})\s+(.+)$", re.MULTILINE)


def _split_by_headings(body: str) -> list[tuple[str, str]]:
    matches = list(_HEADING_RE.finditer(body))
    if not matches:
        stripped = body.strip()
        return [("", stripped)] if stripped else []

    sections: list[tuple[str, str]] = []
    if matches[0].start() > 0:
        preamble = body[: matches[0].start()].strip()
        if preamble:
            sections.append(("", preamble))

    for i, m in enumerate(matches):
        heading = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        text = body[start:end].strip()
        if text:
            sections.append((heading, text))

    return sections


def _split_by_size(text: str, size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text]
    step = max(1, size - overlap)
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start += step
    return chunks


def chunk_body(body: str) -> list[dict[str, Any]]:
    body = body.strip()
    if not body:
        return []

    chunks: list[dict[str, Any]] = []
    for heading, section in _split_by_headings(body):
        for piece in _split_by_size(section, CHUNK_SIZE, CHUNK_OVERLAP):
            piece = piece.strip()
            if piece:
                chunks.append({
                    "text": piece,
                    "heading": heading,
                    "chunk_index": len(chunks),
                })

    total = len(chunks)
    for c in chunks:
        c["chunk_total"] = total
    return chunks


# --- IDs & metadata ---

def _build_chunk_id(rel_source: str, chunk_index: int) -> str:
    return hashlib.sha1(f"{rel_source}::{chunk_index}".encode("utf-8")).hexdigest()


def _build_metadata(rel_source: str, file_meta: dict, chunk: dict, mtime: float) -> dict:
    tags = file_meta.get("tags") or []
    if not isinstance(tags, list):
        tags = [str(tags)]
    title = file_meta.get("title") or Path(rel_source).stem
    return {
        "source": rel_source,
        "title": str(title),
        "project": str(file_meta.get("project") or ""),
        "tags": json.dumps([str(t) for t in tags], ensure_ascii=False),
        "note_type": str(file_meta.get("type") or ""),
        "chunk_index": int(chunk["chunk_index"]),
        "chunk_total": int(chunk["chunk_total"]),
        "heading": str(chunk.get("heading") or ""),
        "mtime": float(mtime),
        "created": str(file_meta.get("created") or ""),
    }


# --- Core operations ---

def reindex_file(path: Path) -> bool:
    path = Path(path).resolve()

    if path.suffix.lower() != ".md":
        return False
    if not _is_in_vault(path):
        logger.warning("Skipped (outside vault): %s", path)
        return False

    rel_source = _relative_source(path)
    if _is_hidden(rel_source):
        return False

    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        logger.debug("File gone before indexing: %s", rel_source)
        return False

    with _write_lock:
        existing = _collection.get(
            where={"source": rel_source},
            limit=1,
            include=["metadatas"],
        )
        if existing["metadatas"]:
            if existing["metadatas"][0].get("mtime") == mtime:
                logger.debug("Skip (unchanged): %s", rel_source)
                return False

        try:
            file_meta, body = _parse_file(path)
        except (FileNotFoundError, UnicodeDecodeError) as exc:
            logger.warning("Cannot read %s: %s", rel_source, exc)
            return False

        chunks = chunk_body(body)
        if not chunks:
            logger.debug("Empty body, removing if present: %s", rel_source)
            _collection.delete(where={"source": rel_source})
            return False

        embed_inputs = [
            f"{c['heading']}\n\n{c['text']}" if c["heading"] else c["text"]
            for c in chunks
        ]
        try:
            vectors = embed_batch(embed_inputs)
        except Exception as exc:
            logger.error("Embedding failed for %s: %s", rel_source, exc)
            return False

        ids = [_build_chunk_id(rel_source, c["chunk_index"]) for c in chunks]
        metadatas = [_build_metadata(rel_source, file_meta, c, mtime) for c in chunks]
        documents = [c["text"] for c in chunks]

        _collection.delete(where={"source": rel_source})
        _collection.upsert(
            ids=ids,
            embeddings=vectors,
            metadatas=metadatas,
            documents=documents,
        )

        logger.info("Indexed %s (%d chunks)", rel_source, len(chunks))
        return True


def remove_file(path: Path) -> bool:
    path = Path(path)
    try:
        rel_source = path.resolve().relative_to(VAULT_PATH).as_posix()
    except ValueError:
        return False

    with _write_lock:
        existing = _collection.get(where={"source": rel_source}, include=["metadatas"])
        if not existing["ids"]:
            return False
        _collection.delete(where={"source": rel_source})
        logger.info("Removed %s (%d chunks)", rel_source, len(existing["ids"]))
        return True


# --- Bulk ---

def iter_vault_md_files() -> list[Path]:
    files: list[Path] = []
    for p in VAULT_PATH.rglob("*.md"):
        if not p.is_file():
            continue
        try:
            rel = p.relative_to(VAULT_PATH).as_posix()
        except ValueError:
            continue
        if _is_hidden(rel):
            continue
        files.append(p)
    return files


def index_full_scan() -> dict[str, int]:
    files = iter_vault_md_files()
    counts = {"indexed": 0, "skipped": 0, "errors": 0, "total": len(files)}

    for path in tqdm(files, desc="Indexing vault", unit="file"):
        try:
            if reindex_file(path):
                counts["indexed"] += 1
            else:
                counts["skipped"] += 1
        except Exception as exc:
            logger.exception("Error indexing %s: %s", path, exc)
            counts["errors"] += 1

    return counts


# --- Query ---

def search(query: str, top_k: int = TOP_K_RESULTS) -> list[dict[str, Any]]:
    if not query or not query.strip():
        return []

    query_vec = embed_text(query)
    results = _collection.query(
        query_embeddings=[query_vec],
        n_results=top_k,
        include=["metadatas", "documents", "distances"],
    )

    docs = (results.get("documents") or [[]])[0]
    metas = (results.get("metadatas") or [[]])[0]
    dists = (results.get("distances") or [[]])[0]

    out = []
    for doc, meta, dist in zip(docs, metas, dists):
        try:
            tags = json.loads(meta.get("tags", "[]"))
        except (json.JSONDecodeError, TypeError):
            tags = []
        out.append({
            "source": meta.get("source", ""),
            "title": meta.get("title", ""),
            "project": meta.get("project", ""),
            "tags": tags,
            "note_type": meta.get("note_type", ""),
            "heading": meta.get("heading", ""),
            "text": doc,
            "score": 1.0 - float(dist),
        })
    return out


def list_projects() -> list[str]:
    all_meta = _collection.get(include=["metadatas"])
    projects = set()
    for m in all_meta.get("metadatas") or []:
        p = m.get("project")
        if p:
            projects.add(p)
    return sorted(projects)


def get_project_notes(project: str) -> list[dict[str, Any]]:
    if not project:
        return []
    results = _collection.get(
        where={"project": project},
        include=["metadatas"],
    )

    notes: dict[str, dict] = {}
    for meta in results.get("metadatas") or []:
        source = meta.get("source", "")
        if source not in notes:
            try:
                tags = json.loads(meta.get("tags", "[]"))
            except (json.JSONDecodeError, TypeError):
                tags = []
            notes[source] = {
                "source": source,
                "title": meta.get("title", ""),
                "project": meta.get("project", ""),
                "tags": tags,
                "note_type": meta.get("note_type", ""),
                "chunks": 0,
            }
        notes[source]["chunks"] += 1

    return sorted(notes.values(), key=lambda n: n["source"])


# --- CLI ---

def _main() -> int:
    parser = argparse.ArgumentParser(description="Obsidian RAG indexer")
    parser.add_argument(
        "--full-scan",
        action="store_true",
        help="Index all .md files in the vault",
    )
    args = parser.parse_args()

    if not args.full_scan:
        parser.print_help()
        return 1

    if not ping():
        logger.error("Ollama is not reachable or model is missing. Aborting.")
        return 2

    counts = index_full_scan()
    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
