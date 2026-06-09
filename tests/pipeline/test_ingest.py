"""Tests for the ingest pipeline module."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shortsforge.pipeline.ingest import (
    InputTooLargeError,
    Segment,
    Transcript,
    UnsupportedMediaError,
    Word,
    ingest,
)
from shortsforge.security.paths import UnsafePathError


def _make_fake_probe(duration: float = 10.0) -> dict:
    return {"format": {"duration": str(duration)}, "streams": []}


def _make_fake_whisper_output():
    """Return a mock WhisperModel that yields one segment with words."""
    segment = MagicMock()
    segment.start = 0.0
    segment.end = 5.0
    segment.text = "Hello world"
    word1 = MagicMock(); word1.start = 0.0; word1.end = 0.5; word1.word = "Hello"; word1.probability = 0.99
    word2 = MagicMock(); word2.start = 0.6; word2.end = 1.2; word2.word = "world"; word2.probability = 0.98
    segment.words = [word1, word2]

    info = MagicMock()
    info.language = "en"

    model = MagicMock()
    model.transcribe.return_value = ([segment], info)
    return model


@pytest.fixture
def tiny_wav(tmp_path: Path) -> Path:
    """Create a tiny valid WAV fixture in the allowed input roots."""
    import wave, struct
    # Register tmp_path as an allowed root for this test
    wav_path = tmp_path / "test.wav"
    with wave.open(str(wav_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(struct.pack("<" + "h" * 8000, *([0] * 8000)))
    return wav_path


class TestIngest:
    @patch("faster_whisper.WhisperModel")
    @patch("shortsforge.pipeline.ingest._probe_media")
    @patch("shortsforge.pipeline.ingest.safe_resolve")
    def test_happy_path(self, mock_resolve, mock_probe, mock_whisper_cls, tmp_path):
        """Happy path: valid file returns a Transcript."""
        wav = tmp_path / "test.wav"
        wav.write_bytes(b"\x00" * 1024)
        mock_resolve.return_value = wav
        mock_probe.return_value = _make_fake_probe(10.0)
        mock_whisper_cls.return_value = _make_fake_whisper_output()

        transcript = ingest(str(wav))

        assert isinstance(transcript, Transcript)
        assert transcript.language == "en"
        assert len(transcript.segments) == 1
        assert transcript.segments[0].text == "Hello world"
        assert len(transcript.segments[0].words) == 2

    @patch("shortsforge.pipeline.ingest.safe_resolve")
    def test_oversize_file_rejected(self, mock_resolve, tmp_path):
        """Files over the size limit must raise InputTooLargeError."""
        big = tmp_path / "big.mp4"
        big.write_bytes(b"\x00" * 100)
        mock_resolve.return_value = big

        with patch("shortsforge.pipeline.ingest._MAX_SIZE_BYTES", 50):
            with pytest.raises(InputTooLargeError, match="size"):
                ingest(str(big))

    def test_path_traversal_rejected(self):
        """Traversal paths must raise UnsafePathError."""
        with pytest.raises(UnsafePathError):
            ingest("../../etc/passwd")

    @patch("shortsforge.pipeline.ingest._probe_media")
    @patch("shortsforge.pipeline.ingest.safe_resolve")
    def test_corrupted_file_raises(self, mock_resolve, mock_probe, tmp_path):
        """Corrupted files must raise UnsupportedMediaError."""
        f = tmp_path / "bad.mp4"
        f.write_bytes(b"not a valid mp4")
        mock_resolve.return_value = f
        mock_probe.side_effect = UnsupportedMediaError("probe failed")

        with pytest.raises(UnsupportedMediaError):
            ingest(str(f))

    @patch("faster_whisper.WhisperModel")
    @patch("shortsforge.pipeline.ingest._probe_media")
    @patch("shortsforge.pipeline.ingest.safe_resolve")
    def test_over_duration_rejected(self, mock_resolve, mock_probe, mock_whisper_cls, tmp_path):
        """Videos over the duration limit must raise InputTooLargeError."""
        f = tmp_path / "long.mp4"
        f.write_bytes(b"\x00" * 100)
        mock_resolve.return_value = f
        mock_probe.return_value = _make_fake_probe(duration=99999.0)

        with pytest.raises(InputTooLargeError, match="Duration"):
            ingest(str(f))
