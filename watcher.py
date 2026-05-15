import logging
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from config import VAULT_PATH, WATCH_DEBOUNCE_MS
from indexer import reindex_file, remove_file

logger = logging.getLogger(__name__)


def _is_md(path_str: str) -> bool:
    return path_str.lower().endswith(".md")


class VaultWatcher(FileSystemEventHandler):
    def __init__(self, debounce_ms: int):
        super().__init__()
        self._debounce = debounce_ms / 1000.0
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def _schedule(self, key: str, action: Callable[[], None]) -> None:
        with self._lock:
            existing = self._timers.get(key)
            if existing is not None:
                existing.cancel()
            timer = threading.Timer(self._debounce, self._run, args=(key, action))
            timer.daemon = True
            self._timers[key] = timer
            timer.start()

    def _run(self, key: str, action: Callable[[], None]) -> None:
        with self._lock:
            self._timers.pop(key, None)
        try:
            action()
        except Exception as exc:
            logger.exception("Watcher action failed for %s: %s", key, exc)

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory or not _is_md(event.src_path):
            return
        path = Path(event.src_path)
        self._schedule(event.src_path, lambda: reindex_file(path))

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory or not _is_md(event.src_path):
            return
        path = Path(event.src_path)
        self._schedule(event.src_path, lambda: reindex_file(path))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory or not _is_md(event.src_path):
            return
        path = Path(event.src_path)
        self._schedule(event.src_path, lambda: remove_file(path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src = event.src_path
        dst = getattr(event, "dest_path", "") or ""
        if _is_md(src):
            src_path = Path(src)
            self._schedule(src, lambda: remove_file(src_path))
        if _is_md(dst):
            dst_path = Path(dst)
            self._schedule(dst, lambda: reindex_file(dst_path))


def start_watching(
    vault_path: Path = VAULT_PATH,
    debounce_ms: int = WATCH_DEBOUNCE_MS,
) -> BaseObserver:
    handler = VaultWatcher(debounce_ms)
    observer = Observer()
    observer.daemon = True
    observer.schedule(handler, str(vault_path), recursive=True)
    observer.start()
    logger.info("Watching vault: %s (debounce: %dms)", vault_path, debounce_ms)
    return observer


if __name__ == "__main__":
    observer = start_watching()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        observer.join()
        logger.info("Watcher stopped")
