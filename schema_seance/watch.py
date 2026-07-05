"""Watch mode — re-summon whenever a file (or glob) changes.

Powers ``seance watch <path>``: clear the screen, re-render the profile
on every change to the target file(s), and optionally show a schema
diff versus the previous render so you can watch drift heal (or break)
in real time as you iterate on the source.

The module is intentionally I/O-light: the render callback, the clock,
and the file-change source can all be injected so the tests never need
real filesystem timing or a real ``watchfiles`` install.

Two file-change sources are provided:

* :class:`WatchfilesSource` — thin wrapper around the optional
  ``watchfiles`` extra (``pip install schema-seance[watch]``). Fast,
  event-driven, cross-platform.
* :class:`MtimePollSource` — dependency-free fallback that polls
  ``os.stat`` at a configurable interval.

Both yield :class:`WatchEvent` objects containing the set of paths that
changed. :func:`run_watch_loop` debounces bursty writes, calls the
render callback with the current path list, and swallows individual
render errors so a broken file (mid-edit) doesn't kill the loop.
"""

from __future__ import annotations

import glob
import os
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

__all__ = [
    "DEFAULT_DEBOUNCE_MS",
    "DEFAULT_POLL_INTERVAL",
    "MtimePollSource",
    "WatchEvent",
    "WatchSource",
    "WatchfilesSource",
    "debounce_events",
    "expand_targets",
    "have_watchfiles",
    "run_watch_loop",
]


# Public defaults — surfaced to CLI help so the values are discoverable.
DEFAULT_DEBOUNCE_MS = 500
DEFAULT_POLL_INTERVAL = 1.0  # seconds


@dataclass(frozen=True)
class WatchEvent:
    """A single (post-debounce) change notification.

    ``paths`` is the ordered, de-duplicated list of paths that changed
    since the previous fire. ``at`` is the timestamp (seconds, from the
    injected clock) at which the debounce window closed.
    """

    paths: tuple[Path, ...]
    at: float = 0.0


class WatchSource(Protocol):
    """Anything that yields raw (pre-debounce) change events.

    A source yields an iterable of ``Path`` on every notification; the
    debounce layer collapses bursty notifications into a single
    :class:`WatchEvent`. Sources should honour ``stop_when()`` to allow
    tests and Ctrl-C to break the loop promptly.
    """

    def iter_changes(
        self,
        *,
        stop_when: Callable[[], bool],
    ) -> Iterator[Iterable[Path]]:  # pragma: no cover - protocol
        ...


def have_watchfiles() -> bool:
    """Return ``True`` when the optional ``watchfiles`` extra is available."""
    try:
        import watchfiles  # noqa: F401
    except Exception:
        return False
    return True


def expand_targets(patterns: Iterable[str | Path]) -> tuple[list[Path], list[Path]]:
    """Expand a list of paths/globs into (files, dirs_to_watch).

    Globs (``*.csv``, ``exports/**/*.parquet``) are expanded via
    :func:`glob.glob` with ``recursive=True``. Non-existent literals are
    dropped from the file list but their parent directory (or ``.``) is
    still watched so a create-then-write dance is picked up.

    The second tuple element is the ordered, de-duplicated set of
    directories any watcher should subscribe to.
    """
    files: list[Path] = []
    dirs: list[Path] = []
    seen_files: set[Path] = set()
    seen_dirs: set[Path] = set()

    def _add_dir(d: Path) -> None:
        d = d.resolve() if d.exists() else d
        if d not in seen_dirs:
            seen_dirs.add(d)
            dirs.append(d)

    for raw in patterns:
        text = str(raw)
        matched: list[str]
        if any(ch in text for ch in "*?[") or "**" in text:
            matched = sorted(glob.glob(text, recursive=True))
        else:
            matched = [text]
        for m in matched:
            p = Path(m)
            if p.is_file():
                key = p.resolve()
                if key not in seen_files:
                    seen_files.add(key)
                    files.append(p)
                _add_dir(p.parent if p.parent != Path("") else Path("."))
            else:
                # Non-existent literal — still watch the parent so we
                # notice when it's created. Best effort.
                parent = p.parent if p.parent != Path("") else Path(".")
                if parent.exists():
                    _add_dir(parent)
    return files, dirs


class MtimePollSource:
    """Zero-dependency change source that polls ``os.stat`` on a schedule.

    Falls back for hosts without ``watchfiles``. The clock and sleeper
    are injectable so tests can drive it without wall-clock delays.
    """

    def __init__(
        self,
        files: Iterable[Path],
        *,
        interval: float = DEFAULT_POLL_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._files: list[Path] = list(files)
        self._interval = max(0.01, float(interval))
        self._clock = clock
        self._sleep = sleep
        self._snapshot: dict[Path, tuple[float, int] | None] = {
            p: _stat_key(p) for p in self._files
        }

    def iter_changes(
        self,
        *,
        stop_when: Callable[[], bool],
    ) -> Iterator[list[Path]]:
        while not stop_when():
            self._sleep(self._interval)
            if stop_when():
                return
            changed: list[Path] = []
            for path in self._files:
                key = _stat_key(path)
                if key != self._snapshot.get(path):
                    self._snapshot[path] = key
                    changed.append(path)
            if changed:
                yield changed


class WatchfilesSource:
    """Change source backed by the optional ``watchfiles`` extra.

    We watch the enclosing directories (``watchfiles`` needs them) and
    filter events back down to the caller's target file list. If
    ``watchfiles`` isn't importable, constructing the source raises
    :class:`ImportError`.
    """

    def __init__(
        self,
        files: Iterable[Path],
        dirs: Iterable[Path],
        *,
        poll_delay_ms: int = 100,
    ) -> None:
        from watchfiles import watch  # local import — optional dep

        self._files: set[Path] = {p.resolve() for p in files}
        self._dirs = [str(d) for d in dirs] or ["."]
        self._watch = watch
        self._poll_delay_ms = poll_delay_ms

    def iter_changes(
        self,
        *,
        stop_when: Callable[[], bool],
    ) -> Iterator[list[Path]]:
        for batch in self._watch(
            *self._dirs,
            stop_event=_StopEventAdapter(stop_when),
            debounce=self._poll_delay_ms,
        ):
            if stop_when():
                return
            paths: list[Path] = []
            seen: set[Path] = set()
            for _change, raw_path in batch:
                p = Path(raw_path)
                resolved = p.resolve() if p.exists() else p
                if self._files and resolved not in self._files:
                    continue
                if resolved in seen:
                    continue
                seen.add(resolved)
                paths.append(p)
            if paths:
                yield paths


class _StopEventAdapter:
    """Duck-typed ``threading.Event`` that flips when ``stop_when()`` is True."""

    def __init__(self, stop_when: Callable[[], bool]) -> None:
        self._stop_when = stop_when

    def is_set(self) -> bool:  # pragma: no cover - trivial adapter
        return bool(self._stop_when())


def debounce_events(
    source: Iterator[Iterable[Path]],
    *,
    window_ms: int = DEFAULT_DEBOUNCE_MS,
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[WatchEvent]:
    """Coalesce bursty source batches into rate-limited events.

    A single source batch always becomes a single :class:`WatchEvent`;
    if additional batches arrive within ``window_ms`` of the previous
    fire we fold their paths in and re-emit only once the window has
    passed. Ordering is preserved and duplicates are suppressed within
    a single emitted event.

    Both bundled sources (:class:`MtimePollSource`,
    :class:`WatchfilesSource`) already throttle themselves, so in
    practice this collapses editor "save + swap-file" storms into one
    render without adding latency to slow-cadence changes.

    The source iterator is drained lazily — callers can ``break`` out
    of the returned iterator to stop the loop.
    """
    if window_ms < 0:
        raise ValueError("window_ms must be >= 0")

    window = window_ms / 1000.0
    pending: list[Path] = []
    seen: set[Path] = set()
    last_fire: float | None = None

    def _flush(now: float) -> WatchEvent | None:
        nonlocal pending, seen, last_fire
        if not pending:
            return None
        event = WatchEvent(paths=tuple(pending), at=now)
        pending = []
        seen = set()
        last_fire = now
        return event

    for batch in source:
        for path in batch:
            if path in seen:
                continue
            seen.add(path)
            pending.append(path)
        if not pending:
            continue
        now = clock()
        if last_fire is not None and (now - last_fire) < window:
            # Still inside the debounce window — keep accumulating,
            # wait for the source to hand us another batch.
            continue
        event = _flush(now)
        if event is not None:
            yield event

    # Drain anything the debounce window swallowed at the end.
    tail = _flush(clock())
    if tail is not None:
        yield tail


RenderFn = Callable[[list[Path], int], None]
"""Signature: ``render(paths, iteration)`` — iteration starts at 0 for the
first render, ``1`` after the first change, and so on."""


@dataclass
class WatchLoopStats:
    """Small, testable summary of what a watch loop actually did."""

    iterations: int = 0
    errors: int = 0
    paths_seen: list[Path] = field(default_factory=list)


def run_watch_loop(
    files: Iterable[Path],
    *,
    render: RenderFn,
    source: WatchSource | None = None,
    debounce_ms: int = DEFAULT_DEBOUNCE_MS,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    prefer_watchfiles: bool = True,
    max_iterations: int | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    stop_when: Callable[[], bool] | None = None,
    on_error: Callable[[BaseException], None] | None = None,
) -> WatchLoopStats:
    """Drive the watch loop end-to-end.

    Always calls ``render`` once up front (iteration 0). Then, for every
    debounced change event, calls it again with the changed path list
    and an incremented iteration counter. Individual render exceptions
    are routed to ``on_error`` (if provided) and counted in the returned
    :class:`WatchLoopStats` — they never abort the loop.

    ``max_iterations`` and ``stop_when`` exist so tests (and the CLI's
    Ctrl-C handling) can end the loop deterministically.
    """
    file_list = list(files)
    stats = WatchLoopStats(paths_seen=list(file_list))

    def _stopped() -> bool:
        if stop_when is not None and stop_when():
            return True
        if max_iterations is not None and stats.iterations >= max_iterations:
            return True
        return False

    # Initial render (iteration 0). Errors here count but don't abort.
    _safe_render(render, file_list, stats.iterations, stats, on_error)
    stats.iterations += 1

    if _stopped():
        return stats

    if source is None:
        if prefer_watchfiles and have_watchfiles():
            _files, dirs = expand_targets([str(p) for p in file_list])
            try:
                source = WatchfilesSource(file_list, dirs)
            except Exception:
                source = MtimePollSource(
                    file_list, interval=poll_interval, clock=clock, sleep=sleep
                )
        else:
            source = MtimePollSource(file_list, interval=poll_interval, clock=clock, sleep=sleep)

    change_iter = source.iter_changes(stop_when=_stopped)
    for event in debounce_events(change_iter, window_ms=debounce_ms, clock=clock):
        if _stopped():
            break
        _safe_render(render, list(event.paths), stats.iterations, stats, on_error)
        stats.iterations += 1
        if _stopped():
            break
    return stats


def _safe_render(
    render: RenderFn,
    paths: list[Path],
    iteration: int,
    stats: WatchLoopStats,
    on_error: Callable[[BaseException], None] | None,
) -> None:
    try:
        render(paths, iteration)
    except BaseException as exc:  # noqa: BLE001 - keep the loop alive
        stats.errors += 1
        if on_error is not None:
            try:
                on_error(exc)
            except Exception:
                # Never let the error hook itself take down the loop.
                pass
        # Re-raise KeyboardInterrupt so Ctrl-C still exits promptly.
        if isinstance(exc, KeyboardInterrupt):
            raise


def _stat_key(path: Path) -> tuple[float, int] | None:
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return (st.st_mtime, st.st_size)
