"""Tests for the ``seance watch`` feature (issue #41).

Everything here uses injected clocks / fake sources so the tests never
touch real filesystem timing and don't depend on ``watchfiles``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from schema_seance.cli import main
from schema_seance.watch import (
    DEFAULT_DEBOUNCE_MS,
    MtimePollSource,
    debounce_events,
    expand_targets,
    have_watchfiles,
    run_watch_loop,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _FakeClock:
    """Manually-advanced monotonic clock."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _ScriptedSource:
    """Yields a canned list of batches, no real I/O."""

    def __init__(
        self,
        batches: list[list[Path]],
        *,
        between_calls: Callable[[], None] | None = None,
    ) -> None:
        self._batches = batches
        self._between = between_calls

    def iter_changes(
        self,
        *,
        stop_when: Callable[[], bool],
    ) -> Iterator[list[Path]]:
        for batch in self._batches:
            if stop_when():
                return
            if self._between is not None:
                self._between()
            yield batch


# ---------------------------------------------------------------------------
# expand_targets
# ---------------------------------------------------------------------------


def test_expand_targets_single_file(tmp_path: Path) -> None:
    f = tmp_path / "data.csv"
    f.write_text("a,b\n1,2\n")

    files, dirs = expand_targets([str(f)])
    assert files == [f]
    assert tmp_path.resolve() in dirs


def test_expand_targets_glob(tmp_path: Path) -> None:
    (tmp_path / "a.csv").write_text("x\n1\n")
    (tmp_path / "b.csv").write_text("x\n2\n")
    (tmp_path / "c.txt").write_text("nope\n")

    files, dirs = expand_targets([str(tmp_path / "*.csv")])
    names = sorted(p.name for p in files)
    assert names == ["a.csv", "b.csv"]
    assert tmp_path.resolve() in dirs


def test_expand_targets_dedupes_and_missing_paths(tmp_path: Path) -> None:
    f = tmp_path / "one.csv"
    f.write_text("x\n1\n")
    files, dirs = expand_targets([str(f), str(f), str(tmp_path / "does-not-exist.csv")])
    assert files == [f]
    # Parent still watched so a future create-then-write is picked up.
    assert tmp_path.resolve() in dirs


# ---------------------------------------------------------------------------
# debounce_events
# ---------------------------------------------------------------------------


def test_debounce_single_batch_emits_once() -> None:
    clock = _FakeClock()
    src = iter([[Path("a.csv")]])
    events = list(debounce_events(src, window_ms=500, clock=clock))
    assert len(events) == 1
    assert events[0].paths == (Path("a.csv"),)


def test_debounce_collapses_bursty_batches() -> None:
    """Batches that arrive inside the debounce window merge into one event."""

    clock = _FakeClock()
    calls = {"n": 0}

    def source() -> Iterator[list[Path]]:
        # 1st batch — first-ever fire, emits immediately.
        yield [Path("a.csv")]
        # 2nd batch arrives 100ms later — inside the 500ms window → swallowed.
        clock.advance(0.1)
        yield [Path("a.csv"), Path("b.csv")]
        # 3rd batch arrives another 100ms later — still inside window.
        clock.advance(0.1)
        yield [Path("c.csv")]
        calls["n"] += 1

    events = list(debounce_events(source(), window_ms=500, clock=clock))
    # First fire + tail flush = 2 events total. Duplicates within the
    # tail collapse; `a.csv` legitimately reappears in the tail because
    # the first fire already reset the seen-set.
    assert len(events) == 2
    assert events[0].paths == (Path("a.csv"),)
    tail_paths = set(events[1].paths)
    assert tail_paths == {Path("a.csv"), Path("b.csv"), Path("c.csv")}


def test_debounce_batches_outside_window_fire_separately() -> None:
    clock = _FakeClock()

    def source() -> Iterator[list[Path]]:
        yield [Path("a.csv")]
        # Advance well past the debounce window before the next batch.
        clock.advance(2.0)
        yield [Path("b.csv")]

    events = list(debounce_events(source(), window_ms=500, clock=clock))
    assert [e.paths for e in events] == [(Path("a.csv"),), (Path("b.csv"),)]


def test_debounce_rejects_negative_window() -> None:
    with pytest.raises(ValueError):
        list(debounce_events(iter([]), window_ms=-1))


# ---------------------------------------------------------------------------
# MtimePollSource
# ---------------------------------------------------------------------------


def test_mtime_source_detects_size_change(tmp_path: Path) -> None:
    f = tmp_path / "data.csv"
    f.write_text("a,b\n1,2\n")

    sleeps: list[float] = []
    tick = {"n": 0}

    def fake_sleep(s: float) -> None:
        sleeps.append(s)
        tick["n"] += 1
        # Mutate the file so the second poll sees the change, then stop.
        if tick["n"] == 1:
            f.write_text("a,b\n1,2\n3,4\n")

    src = MtimePollSource([f], interval=0.05, sleep=fake_sleep, clock=lambda: 0.0)
    stopper = {"stop": False}

    def stop_when() -> bool:
        # Stop after we've observed one change.
        return stopper["stop"]

    batches: list[list[Path]] = []
    it = src.iter_changes(stop_when=stop_when)
    for batch in it:
        batches.append(batch)
        stopper["stop"] = True
    assert batches == [[f]]
    assert sleeps  # we did poll at least once


def test_mtime_source_no_change_yields_nothing(tmp_path: Path) -> None:
    f = tmp_path / "data.csv"
    f.write_text("a,b\n1,2\n")

    calls = {"sleeps": 0}
    stopper = {"stop": False}

    def fake_sleep(_: float) -> None:
        calls["sleeps"] += 1
        if calls["sleeps"] >= 3:
            stopper["stop"] = True

    src = MtimePollSource([f], interval=0.01, sleep=fake_sleep)
    batches = list(src.iter_changes(stop_when=lambda: stopper["stop"]))
    assert batches == []


# ---------------------------------------------------------------------------
# run_watch_loop
# ---------------------------------------------------------------------------


def test_run_watch_loop_initial_render_only(tmp_path: Path) -> None:
    f = tmp_path / "data.csv"
    f.write_text("a\n1\n")
    calls: list[tuple[int, list[Path]]] = []

    def render(paths: list[Path], iteration: int) -> None:
        calls.append((iteration, list(paths)))

    stats = run_watch_loop(
        [f],
        render=render,
        source=_ScriptedSource([]),
        debounce_ms=0,
        prefer_watchfiles=False,
    )
    assert stats.iterations == 1
    assert calls == [(0, [f])]
    assert stats.errors == 0


def test_run_watch_loop_re_renders_on_change(tmp_path: Path) -> None:
    f = tmp_path / "data.csv"
    f.write_text("a\n1\n")
    calls: list[tuple[int, list[Path]]] = []

    def render(paths: list[Path], iteration: int) -> None:
        calls.append((iteration, list(paths)))

    stats = run_watch_loop(
        [f],
        render=render,
        source=_ScriptedSource([[f]]),
        debounce_ms=0,
        prefer_watchfiles=False,
    )
    assert stats.iterations == 2
    assert calls == [(0, [f]), (1, [f])]


def test_run_watch_loop_swallows_render_errors(tmp_path: Path) -> None:
    f = tmp_path / "data.csv"
    f.write_text("a\n1\n")
    errors: list[BaseException] = []

    def render(paths: list[Path], iteration: int) -> None:
        raise RuntimeError(f"boom {iteration}")

    stats = run_watch_loop(
        [f],
        render=render,
        source=_ScriptedSource([[f], [f]]),
        debounce_ms=0,
        prefer_watchfiles=False,
        on_error=errors.append,
    )
    # Initial + 2 change events, all failing, but loop stayed alive.
    assert stats.iterations == 3
    assert stats.errors == 3
    assert len(errors) == 3


def test_run_watch_loop_respects_max_iterations(tmp_path: Path) -> None:
    f = tmp_path / "data.csv"
    f.write_text("a\n1\n")

    calls = {"n": 0}

    def render(_paths: list[Path], _iteration: int) -> None:
        calls["n"] += 1

    # Feed many events but cap iterations at 2 (1 initial + 1 change).
    stats = run_watch_loop(
        [f],
        render=render,
        source=_ScriptedSource([[f]] * 20),
        debounce_ms=0,
        prefer_watchfiles=False,
        max_iterations=2,
    )
    assert stats.iterations == 2
    assert calls["n"] == 2


def test_run_watch_loop_keyboard_interrupt_bubbles(tmp_path: Path) -> None:
    f = tmp_path / "data.csv"
    f.write_text("a\n1\n")

    def render(_paths: list[Path], _iteration: int) -> None:
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        run_watch_loop(
            [f],
            render=render,
            source=_ScriptedSource([]),
            debounce_ms=0,
            prefer_watchfiles=False,
        )


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def test_cli_watch_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["watch", "--help"])
    assert result.exit_code == 0
    assert "Re-summon a file" in result.output
    assert "--diff" in result.output
    assert "--interval" in result.output
    assert "--debounce-ms" in result.output


def test_cli_watch_initial_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Wire the CLI to a source that yields nothing → initial render only."""
    f = tmp_path / "data.csv"
    f.write_text("id,name\n1,Alice\n2,Bob\n")

    from schema_seance import cli as cli_mod

    captured = {"stats": None}
    real_run = cli_mod.run_watch_loop

    def fake_run(files, *, render, source=None, **kwargs):
        # Replace the source with an empty scripted one so the loop
        # renders once and exits deterministically.
        captured["stats"] = real_run(
            files,
            render=render,
            source=_ScriptedSource([]),
            debounce_ms=kwargs.get("debounce_ms", 0),
            prefer_watchfiles=False,
            on_error=kwargs.get("on_error"),
        )
        return captured["stats"]

    monkeypatch.setattr(cli_mod, "run_watch_loop", fake_run)

    runner = CliRunner()
    result = runner.invoke(main, ["watch", "--no-clear", str(f)])
    assert result.exit_code == 0, result.output
    # Persona panel + veil parts + spirits speak all rendered.
    assert "The Veil Parts" in result.output
    assert "The Spirits Speak" in result.output
    assert captured["stats"].iterations == 1


def test_cli_watch_diff_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--diff renders the schema drift panel after a re-render."""
    f = tmp_path / "data.csv"
    f.write_text("id,name\n1,Alice\n")

    from schema_seance import cli as cli_mod

    real_run = cli_mod.run_watch_loop
    fired = {"changed": False}

    def fake_run(files, *, render, source=None, **kwargs):
        # First event: mutate the file (add a column) then fire.
        def mutate() -> None:
            if not fired["changed"]:
                f.write_text("id,name,email\n1,Alice,alice@example.com\n")
                fired["changed"] = True

        scripted = _ScriptedSource([files], between_calls=mutate)
        return real_run(
            files,
            render=render,
            source=scripted,
            debounce_ms=0,
            prefer_watchfiles=False,
            on_error=kwargs.get("on_error"),
        )

    monkeypatch.setattr(cli_mod, "run_watch_loop", fake_run)

    runner = CliRunner()
    result = runner.invoke(main, ["watch", "--no-clear", "--diff", str(f)])
    assert result.exit_code == 0, result.output
    # Two renders (initial + change) and the diff panel appears on the
    # second one. Look for the compare header text.
    assert "Two Spirits Compared" in result.output
    # The added ``email`` column should show up as an add in the diff.
    assert "email" in result.output


def test_cli_watch_rejects_remote_url() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["watch", "s3://bucket/data.csv"])
    assert result.exit_code != 0
    assert "cannot be watched" in result.output


def test_cli_watch_rejects_empty_glob(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["watch", str(tmp_path / "nope-*.csv")])
    assert result.exit_code == 2
    assert "find nothing" in result.output


# ---------------------------------------------------------------------------
# Sanity: watchfiles detection doesn't crash.
# ---------------------------------------------------------------------------


def test_have_watchfiles_returns_bool() -> None:
    assert isinstance(have_watchfiles(), bool)


def test_default_debounce_matches_spec() -> None:
    # Acceptance criteria call for a 500ms default; guard against drift.
    assert DEFAULT_DEBOUNCE_MS == 500
