"""Poll the spec files for changes.

A background thread stats the known files a few times a second and reloads the
ones that changed. Polling rather than inotify keeps the dependency list at
PyYAML, and a handful of stat() calls on six files costs nothing.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from .loader import Registry, reload_if_changed


class Watcher:
    def __init__(
        self,
        registry: Registry,
        sources: dict[str, str] | None = None,
        interval: float = 0.5,
        on_change: Callable[[str], None] | None = None,
    ) -> None:
        self.registry = registry
        self.sources = sources
        self.interval = interval
        self.on_change = on_change
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        # Bumped on every reload; SSE clients compare it against their own.
        self.revision = registry.revision

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, name="docwarden-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                if reload_if_changed(self.registry, self.sources):
                    self.revision = self.registry.revision
                    if self.on_change:
                        self.on_change(self.revision)
            except Exception:  # a half-written file mid-save must not kill the thread
                continue
