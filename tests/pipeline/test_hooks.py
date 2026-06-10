from __future__ import annotations

from types import SimpleNamespace

from shortsforge.pipeline.hooks import (
    HookCandidate,
    _build_windows,
    _dedupe_overlaps,
    _normalize_and_snap_boundaries,
)


def _fake_transcript(duration: float = 120.0):
    words = [
        SimpleNamespace(start=9.8, end=10.0),
        SimpleNamespace(start=24.8, end=25.0),
        SimpleNamespace(start=34.8, end=35.0),
        SimpleNamespace(start=44.8, end=45.0),
    ]
    seg = SimpleNamespace(start=0.0, end=duration, text="hello world", words=words)
    return SimpleNamespace(duration_s=duration, segments=[seg])


def test_build_windows_uses_varied_lengths():
    transcript = _fake_transcript(90.0)
    windows = _build_windows(transcript, min_len=15.0, max_len=58.0)
    lengths = {round(w["end"] - w["start"], 1) for w in windows}

    assert 15.0 in lengths
    assert 25.0 in lengths
    assert 35.0 in lengths


def test_normalize_and_snap_enforces_bounds():
    transcript = _fake_transcript(120.0)
    hook = HookCandidate(
        start_s=100.0,
        end_s=400.0,
        headline="test",
        predicted_retention=0.5,
        rationale="r",
    )

    normalized = _normalize_and_snap_boundaries(
        hook,
        transcript,
        min_len_s=15.0,
        max_len_s=58.0,
    )

    assert normalized.end_s - normalized.start_s <= 58.0
    assert normalized.end_s <= transcript.duration_s


def test_dedupe_overlaps_keeps_distinct_hooks():
    hooks = [
        HookCandidate(start_s=0, end_s=30, headline="a", predicted_retention=0.9, rationale="r"),
        HookCandidate(start_s=5, end_s=35, headline="b", predicted_retention=0.8, rationale="r"),
        HookCandidate(start_s=50, end_s=80, headline="c", predicted_retention=0.7, rationale="r"),
    ]

    selected = _dedupe_overlaps(hooks, max_count=3)

    assert len(selected) == 2
    assert selected[0].headline in {"a", "c"}
    assert selected[1].headline in {"a", "c"}
