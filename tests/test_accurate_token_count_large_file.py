"""
``get_accurate_token_count`` must count large files, not zero them.

It used to return 0 for anything over 50k tokens, and the accurate-token
endpoint forwarded that 0 as a real count.  The token gauge preferred the
"accurate" figure whenever it was > 0 and otherwise fell back to the tree
estimate — which for a file the tree does not carry is also 0 — so the
largest pinned files contributed nothing to the displayed total while being
sent to the provider in full.  Fails against the pre-fix code (returns 0).
"""
import tiktoken

from app.utils import directory_util
from app.utils.directory_util import get_accurate_token_count

LINE = "def handler_{i}(payload, context):\n    return transform(payload['value_{i}'], context.settings)\n"


def test_file_over_50k_tokens_is_counted(tmp_path):
    big = tmp_path / "big.py"
    big.write_text("".join(LINE.format(i=i) for i in range(8000)))

    count = get_accurate_token_count(str(big))

    assert count > 50000, f"large file zeroed: {count}"


def test_small_file_count_is_positive_and_proportional(tmp_path):
    small = tmp_path / "small.py"
    small.write_text("".join(LINE.format(i=i) for i in range(10)))
    big = tmp_path / "big.py"
    big.write_text("".join(LINE.format(i=i) for i in range(100)))

    s, b = get_accurate_token_count(str(small)), get_accurate_token_count(str(big))
    assert 0 < s < b
    assert 7 < b / s < 13  # ten times the lines, roughly ten times the tokens


class _Cal:
    def __init__(self, ratio, source):
        self.ratio, self.source, self.asked = ratio, source, []

    def get_display_ratio(self, ext, model_family=None):
        self.asked.append(ext)
        return self.ratio, self.source


def _patch(monkeypatch, cal):
    import app.utils.token_calibrator as tc
    monkeypatch.setattr(tc, "get_token_calibrator", lambda: cal)


def test_learned_ratio_scales_the_count_above_tiktoken(tmp_path, monkeypatch):
    """The gauge prefers this figure over the tree's, so it must carry the
    same per-model calibration the tree estimate does (cl100k is ~40% low on
    fable).  Fails against pre-fix code, which returns bare tiktoken."""
    f = tmp_path / "x.py"
    f.write_text("".join(LINE.format(i=i) for i in range(200)))
    content = f.read_text()
    raw = len(tiktoken.get_encoding("cl100k_base").encode(content))
    cal = _Cal(2.0, "learned_type")
    _patch(monkeypatch, cal)

    assert get_accurate_token_count(str(f)) == len(content) // 2
    assert len(content) // 2 > raw
    assert cal.asked == [".py"], "ratio must be looked up per file type"


def test_fallback_ratio_leaves_tiktoken_untouched(tmp_path, monkeypatch):
    f = tmp_path / "x.py"
    f.write_text("".join(LINE.format(i=i) for i in range(50)))
    raw = len(tiktoken.get_encoding("cl100k_base").encode(f.read_text()))
    _patch(monkeypatch, _Cal(4.1, "fallback"))
    assert get_accurate_token_count(str(f)) == raw
    # A learned ratio that would say FEWER tokens than tiktoken is not trusted below it.
    _patch(monkeypatch, _Cal(50.0, "learned_type"))
    assert get_accurate_token_count(str(f)) == raw