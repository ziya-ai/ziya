"""
Calibrator poisoning by image bytes (mbcohn bug report, 2026-11-02).

His ~/.ziya/token_calibration.json had learned 13.41 chars/token for
opus-5-5 (and 12.69 for the 'claude' family) against a tokenizer that does
~2.5.  Every turn stamped ONE ratio on all five pinned files -- the per-file
split cancels algebraically in the attribution, so each sample is that
turn's whole-payload chars/tokens -- and the char count was
``len(str(message['content']))``, which for a block list is the Python repr
including any base64 image data.  The samples piled up just under the
MAX_CHARS_PER_TOKEN = 15 cap.

Pinned here: (1) text-only char counting, (2) a per-family plausible band
that rejects such samples, (3) a load-time purge of a poisoned file, using
a slice of his real file as the fixture, (4) the executor seam that feeds
the calibrator.
"""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.utils.token_calibrator import TokenCalibrator, payload_text_chars

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "mbcohn_token_calibration_2026-11-02.json"


def _cal(tmp_path, data=None):
    p = tmp_path / "cal.json"
    if data is not None:
        p.write_text(json.dumps(data))
    return TokenCalibrator(cache_file=str(p))


# --- (1) char counting --------------------------------------------------------

def test_image_blocks_contribute_no_chars():
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "A" * 700_000}}
    msgs = [{"role": "user", "content": [img, {"type": "text", "text": "look at this"}]},
            {"role": "assistant", "content": "ok"}]
    assert payload_text_chars(msgs) == len("look at this") + 2
    # The old count, for contrast: the repr of the block list.
    assert len(str(msgs[0]["content"])) > 700_000


def test_nested_tool_result_text_and_langchain_content_are_counted():
    msgs = [
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                      "content": [{"type": "text", "text": "x" * 100}]}]},
        {"role": "user", "content": [{"type": "tool_result", "content": "y" * 50}]},
        SimpleNamespace(type="human", content="z" * 10),
        None, 7,
    ]
    assert payload_text_chars(msgs) == 160
    assert payload_text_chars("sys" * 5, msgs) == 175


# --- (2) plausible band -------------------------------------------------------

def test_claude_band_rejects_the_poisoned_ratio_and_accepts_a_real_one(tmp_path):
    cal = _cal(tmp_path)
    assert cal.plausible_band("anthropic.claude-opus-5-5") == (1.5, 6.0)
    assert cal.plausible_band("claude") == (1.5, 6.0)
    assert cal.plausible_band("nova") == (cal.MIN_CHARS_PER_TOKEN, cal.MAX_CHARS_PER_TOKEN)
    files = {"a.py": "x" * 13_400}
    cal.record_actual_usage("c", files, actual_tokens=1000, model_id="anthropic.claude-opus-5-5",
                            model_family="claude")
    assert "anthropic.claude-opus-5-5" not in cal.global_by_model, "13.4 chars/token rejected"
    cal.record_actual_usage("c", {"a.py": "x" * 2500}, actual_tokens=1000,
                            model_id="anthropic.claude-opus-5-5", model_family="claude")
    assert cal.global_by_model["anthropic.claude-opus-5-5"] == pytest.approx(2.5)


# --- (3) purge on load --------------------------------------------------------

def test_poisoned_file_is_purged_on_load(tmp_path):
    raw = json.loads(FIXTURE.read_text())
    assert raw["global_by_model"]["anthropic.claude-opus-5-5"] == pytest.approx(13.41, abs=0.01)
    before = sum(len(v) for v in raw["samples"]["anthropic.claude-opus-5-5"].values())
    bad_before = sum(1 for v in raw["samples"]["anthropic.claude-opus-5-5"].values()
                     for c, t in v if c / t > 6.0)
    assert bad_before > before / 2, "fixture: most of his samples are inflated"

    p = tmp_path / "cal.json"
    p.write_text(FIXTURE.read_text())
    cal = TokenCalibrator(cache_file=str(p))

    for bucket in ("anthropic.claude-opus-5-5", "claude"):
        kept = [s.chars_per_token for ext in cal.samples_by_model_and_type[bucket]
                for s in cal.samples_by_model_and_type[bucket][ext]]
        assert kept and all(1.5 <= r <= 6.0 for r in kept), bucket
        g = cal.global_by_model[bucket]
        assert 1.5 <= g <= 6.0, f"{bucket} global {g}"
    assert cal.estimate_tokens_from_chars(1_000_000, model_family="anthropic.claude-opus-5-5") > 1_000_000 / 6.0
    assert cal.has_unsaved_data, "the purge is persisted on the next save"
    # Stats for a type whose samples all went are gone too.
    for bucket, types in cal.stats_by_model_and_type.items():
        for ext in types:
            assert cal.samples_by_model_and_type[bucket][ext], (bucket, ext)


def test_global_outside_band_with_no_samples_is_dropped(tmp_path):
    cal = _cal(tmp_path, {"version": "1.1", "global_by_model": {"claude": 12.7, "nova": 4.0},
                          "samples": {}})
    assert "claude" not in cal.global_by_model
    assert cal.global_by_model["nova"] == 4.0


def test_clean_file_is_untouched(tmp_path):
    cal = _cal(tmp_path, {"version": "1.1", "global_by_model": {"claude": 2.6},
                          "samples": {"claude": {".py": [[2600, 1000], [2500, 1000]]}}})
    assert cal.global_by_model["claude"] == 2.6
    assert len(cal.samples_by_model_and_type["claude"][".py"]) == 2
    assert not cal.has_unsaved_data


# --- (4) the executor seam ----------------------------------------------------

def _executor():
    from app.streaming_tool_executor import StreamingToolExecutor
    inst = StreamingToolExecutor.__new__(StreamingToolExecutor)
    inst.model_config = {"token_limit": 1_000_000, "family": "claude"}
    return inst


def test_record_calibration_ignores_image_bytes(tmp_path):
    """100k chars of file + a 400k-char image block, billed 40k tokens.
    Counting the image as text attributes a fifth of the tokens to the file
    (12.5 chars/token); counting text only gives the real 2.5."""
    from app.streaming_tool_executor import IterationUsage
    cal = _cal(tmp_path)
    lines = [f"[{i:03d} ] def f{i}(): return {i}" for i in range(4000)]
    system_content = "File: a.py\n" + "\n".join(lines)
    file_chars = len("\n".join(l.split("] ", 1)[1] for l in lines))
    conversation = [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "A" * 400_000}},
        {"type": "text", "text": "what is this"}]}]
    tokens = int(file_chars / 2.5)
    with patch("app.utils.token_calibrator.get_token_calibrator", return_value=cal), \
         patch("app.agents.models.ModelManager.get_model_id", return_value="global.anthropic.claude-opus-5-5"), \
         patch("app.agents.models.ModelManager.get_model_config", return_value={"family": "claude"}), \
         patch("app.streaming_tool_executor.ziya_env", return_value="x"):
        _executor()._record_calibration(IterationUsage(), "c", conversation, system_content,
                                        fresh=tokens, cached=0, total_input=tokens)
    samples = cal.samples_by_model_and_type["anthropic.claude-opus-5-5"][".py"]
    assert samples, "a sample was recorded (the old path produced 12.5 and was rejected)"
    # The attribution cancels to text_chars / tokens.  The text includes the
    # "[NNN ] " line-number prefixes (payload) that are stripped from the
    # attributed file content, so the sample sits a little above the file's
    # own 2.5 -- and nowhere near the 12.5 the image bytes produced.
    text_chars = len(system_content) + len("what is this")
    assert samples[-1].chars_per_token == pytest.approx(text_chars / tokens, rel=0.02)
    assert samples[-1].chars_per_token < 4.0


def test_executor_uses_text_only_chars_and_the_family_band():
    src = (ROOT / "app" / "streaming_tool_executor.py").read_text()
    i = src.index("def _record_calibration(")
    body = src[i:src.index("async def stream_with_tools", i)]
    assert "payload_text_chars(" in body
    assert "len(str(m.get('content'" not in body
    assert "plausible_band(" in body
