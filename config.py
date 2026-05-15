import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).parent.resolve()

load_dotenv(_PROJECT_ROOT / ".env")


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required env var: {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def _normalize_path(value: str) -> Path:
    expanded = os.path.expanduser(value)
    p = Path(expanded)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p.resolve()


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"Env var {name} must be an integer, got: {raw!r}")


VAULT_PATH: Path = _normalize_path(_require("OBSIDIAN_VAULT"))
if not VAULT_PATH.exists():
    raise RuntimeError(f"OBSIDIAN_VAULT path does not exist: {VAULT_PATH}")
if not VAULT_PATH.is_dir():
    raise RuntimeError(f"OBSIDIAN_VAULT must be a directory: {VAULT_PATH}")

CHROMA_DB_PATH: Path = _normalize_path(os.getenv("CHROMA_DB_PATH", "./data/chromadb"))
CHROMA_DB_PATH.mkdir(parents=True, exist_ok=True)

EMBED_MODEL: str = os.getenv("EMBED_MODEL", "nomic-embed-text").strip()
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip()

WATCH_DEBOUNCE_MS: int = _get_int("WATCH_DEBOUNCE_MS", 500)
CHUNK_SIZE: int = _get_int("CHUNK_SIZE", 1000)
CHUNK_OVERLAP: int = _get_int("CHUNK_OVERLAP", 200)
TOP_K_RESULTS: int = _get_int("TOP_K_RESULTS", 5)

COLLECTION_NAME: str = "obsidian_notes"
EMBED_DIM: int = 768

LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").strip().upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
