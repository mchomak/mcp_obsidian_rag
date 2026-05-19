import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).parent.resolve()

load_dotenv(_PROJECT_ROOT / ".env")

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("POSTHOG_DISABLED", "True")


_LOG_LEVEL_EARLY = os.getenv("LOG_LEVEL", "INFO").strip().upper()
_LOG_FORMAT = "%(asctime)s [%(levelname)s] pid=%(process)d %(name)s: %(message)s"

_root_logger = logging.getLogger()
_root_logger.setLevel(getattr(logging, _LOG_LEVEL_EARLY, logging.INFO))
_root_logger.handlers.clear()

_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
_root_logger.addHandler(_stderr_handler)

try:
    _file_handler = logging.FileHandler(
        _PROJECT_ROOT / "mcp_server.log",
        mode="a",
        encoding="utf-8",
    )
    _file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    _root_logger.addHandler(_file_handler)
except Exception as _exc:
    _root_logger.warning("Could not attach file log handler: %s", _exc)

logging.getLogger(__name__).info(
    "=== config.py loaded (pid=%d, level=%s) ===",
    os.getpid(), _LOG_LEVEL_EARLY,
)


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


def _parse_excluded_dirs(raw: str) -> tuple[str, ...]:
    items = [p.strip().strip("/\\") for p in raw.split(",")]
    return tuple(p for p in items if p)


EXCLUDED_DIRS: tuple[str, ...] = _parse_excluded_dirs(
    os.getenv("EXCLUDED_DIRS", "_Secrets,templates")
)

ARCHIVE_DIR_NAME: str = os.getenv("ARCHIVE_DIR_NAME", "Archive").strip() or "Archive"

COLLECTION_NAME: str = "obsidian_notes"
EMBED_DIM: int = 768

LOG_LEVEL: str = _LOG_LEVEL_EARLY
