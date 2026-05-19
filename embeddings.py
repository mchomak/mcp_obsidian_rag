import time
import logging
import ollama

from config import EMBED_MODEL, OLLAMA_BASE_URL, EMBED_DIM

logger = logging.getLogger(__name__)

_client = ollama.Client(host=OLLAMA_BASE_URL, timeout=30.0)

_MAX_RETRIES = 3
_BACKOFF_BASE = 0.5


def _extract_embeddings(response) -> list[list[float]]:
    if hasattr(response, "embeddings"):
        return response.embeddings
    return response["embeddings"]


def _embed_with_retry(texts: list[str]) -> list[list[float]]:
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = _client.embed(model=EMBED_MODEL, input=texts)
            return _extract_embeddings(response)
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                delay = _BACKOFF_BASE * (2 ** attempt)
                logger.warning(
                    "Ollama embed failed (attempt %d/%d): %s. Retry in %.1fs",
                    attempt + 1, _MAX_RETRIES, exc, delay,
                )
                time.sleep(delay)
    raise RuntimeError(
        f"Ollama embed failed after {_MAX_RETRIES} attempts"
    ) from last_exc


def embed_text(text: str) -> list[float]:
    if not text or not text.strip():
        raise ValueError("Cannot embed empty text")
    return _embed_with_retry([text])[0]


def embed_batch(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    for i, t in enumerate(texts):
        if not t or not t.strip():
            raise ValueError(f"Empty text at index {i}")
    vectors = _embed_with_retry(texts)
    for i, vec in enumerate(vectors):
        if len(vec) != EMBED_DIM:
            raise RuntimeError(
                f"Unexpected embedding dim: got {len(vec)}, expected {EMBED_DIM} (idx {i})"
            )
    return vectors


def _model_names(response) -> list[str]:
    models = response.models if hasattr(response, "models") else response.get("models", [])
    names = []
    for m in models:
        if hasattr(m, "model"):
            name = m.model
        elif hasattr(m, "name"):
            name = m.name
        elif isinstance(m, dict):
            name = m.get("model") or m.get("name") or ""
        else:
            name = ""
        if name:
            names.append(name)
    return names


def ping() -> bool:
    try:
        names = _model_names(_client.list())
        for name in names:
            if name == EMBED_MODEL or name.startswith(f"{EMBED_MODEL}:"):
                return True
        logger.error(
            "Model %r not found in Ollama. Available: %s. Run: ollama pull %s",
            EMBED_MODEL, names, EMBED_MODEL,
        )
        return False
    except Exception as exc:
        logger.error("Ollama ping failed: %s", exc)
        return False
