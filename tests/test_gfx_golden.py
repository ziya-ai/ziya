"""Golden-tier plumbing: image comparison and the ledger's trust rules.

No browser here.  Synthetic images stand in for renders so each rule is
tested by construction:

  * the comparison PASSES a one-pixel anti-aliasing shift and FAILS a
    dropped element — the two cases the tolerance exists to separate;
  * a pixel hash ignores PNG encoding and notices a single pixel;
  * a provisional golden can never replace a validated one;
  * `record` with an image and status ok produces a VALIDATED golden and
    writes its hash into the committed corpus; status fail keeps the image
    as evidence and leaves the golden alone;
  * `promote` carries a validated hash into expectations.json;
  * trust-from-hash validates only an exact hash match;
  * rebaseline needs a reason and archives the prior golden.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "gfx_render"))
import gfx_ledger as L  # noqa: E402
import golden as G  # noqa: E402

W, H = 400, 300
OK = {"status": "ok", "signature": "", "detail": "fine"}
FAIL = {"status": "fail", "signature": "node-missing", "detail": "node gone"}


# ── synthetic renders ─────────────────────────────────────────────────────

def _diagram(shift: int = 0, drop_node: bool = False, bg=(255, 255, 255)) -> bytes:
    """A 'diagram': two boxes, an edge, a label.  Deterministic."""
    im = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(im)
    d.rectangle([40, 60, 140, 140], outline=(20, 20, 20), width=3)
    if not drop_node:
        d.rectangle([240, 60, 340, 140], outline=(20, 20, 20), width=3)
    d.line([140, 100, 240, 100], fill=(20, 20, 20), width=2)
    d.text((60 + shift, 200), "label text", fill=(20, 20, 20))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _reencode(png: bytes, level: int) -> bytes:
    im = Image.open(io.BytesIO(png))
    buf = io.BytesIO()
    im.save(buf, format="PNG", compress_level=level)
    return buf.getvalue()


@pytest.fixture
def root(tmp_path):
    """.ziya/gfx-sweep with the corpus resolving to <tmp>/tests/gfx_corpus."""
    r = tmp_path / ".ziya" / "gfx-sweep"
    r.mkdir(parents=True)
    return r


def _spec_on_disk(root: Path, engine: str, sid: str) -> None:
    p = root / "specs" / engine / f"{sid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"type": engine, "definition": "graph TD; A-->B", "intent": "x"}))


# ── comparison ────────────────────────────────────────────────────────────

class TestCompare:
    def test_identical_is_exact_and_zero(self):
        a = _diagram()
        assert G.pixel_hash(a) == G.pixel_hash(a)
        c = G.compare(a, a)
        assert c.fraction == 0.0 and c.differing == 0 and c.same_size

    def test_one_pixel_text_shift_is_noise(self):
        """Anti-aliasing / hinting moves a glyph edge; must stay under tolerance."""
        a, b = _diagram(), _diagram(shift=1)
        assert G.pixel_hash(a) != G.pixel_hash(b), "the hash must notice the shift"
        c = G.compare(a, b)
        assert 0 < c.fraction < G.DEFAULT_TOLERANCE, c.fraction
        assert c.within(G.DEFAULT_TOLERANCE)

    def test_dropped_node_exceeds_tolerance(self):
        """The failure the tier exists for — a 100x80 box missing."""
        c = G.compare(_diagram(), _diagram(drop_node=True))
        assert c.fraction >= G.DEFAULT_TOLERANCE, c.fraction
        assert not c.within(G.DEFAULT_TOLERANCE)

    def test_size_change_is_total_difference(self):
        im = Image.new("RGB", (W + 10, H), (255, 255, 255))
        buf = io.BytesIO(); im.save(buf, format="PNG")
        c = G.compare(_diagram(), buf.getvalue())
        assert not c.same_size and c.fraction == 1.0 and c.mask_png is None
        assert not c.within(0.99)

    def test_heatmap_marks_only_differing_pixels(self):
        a, b = _diagram(), _diagram(drop_node=True)
        c = G.compare(a, b)
        heat = Image.open(io.BytesIO(G.heatmap(a, c.mask_png))).convert("RGB")
        # The dropped box's top edge is red in the heatmap; an untouched area is not.
        # (Not its left edge at x=240: the connecting line ends there in BOTH images.)
        assert heat.getpixel((290, 61)) == (255, 0, 0)
        assert heat.getpixel((10, 10)) != (255, 0, 0)


class TestPixelHash:
    def test_hash_ignores_png_encoding(self):
        a = _diagram()
        assert G.pixel_hash(_reencode(a, 0)) == G.pixel_hash(_reencode(a, 9))
        assert _reencode(a, 0) != _reencode(a, 9), "encodings really differ"

    def test_hash_sees_one_pixel(self):
        im = Image.open(io.BytesIO(_diagram())).convert("RGB")
        im.putpixel((5, 5), (254, 255, 255))
        buf = io.BytesIO(); im.save(buf, format="PNG")
        assert G.pixel_hash(buf.getvalue()) != G.pixel_hash(_diagram())

    def test_hash_includes_dimensions(self):
        """Same bytes reflowed to another shape must not collide."""
        a = Image.new("RGB", (20, 10), (0, 0, 0)); b = Image.new("RGB", (10, 20), (0, 0, 0))
        ba, bb = io.BytesIO(), io.BytesIO(); a.save(ba, "PNG"); b.save(bb, "PNG")
        assert G.pixel_hash(ba.getvalue()) != G.pixel_hash(bb.getvalue())


# ── golden trust rules ────────────────────────────────────────────────────

class TestGoldenCapture:
    def test_capture_writes_png_and_sidecar(self, root):
        rec = L.golden_capture(root, "mermaid", "m-1", "light", _diagram(),
                               trust="provisional", run="r1", source="runner")
        png, side = L.golden_paths(root, "mermaid", "m-1", "light")
        assert png.exists() and side.exists()
        assert rec["trust"] == "provisional" and rec["hash"] == G.pixel_hash(_diagram())
        assert L.golden_root(root) == root.parent / "gfx-golden"

    def test_provisional_cannot_replace_validated(self, root):
        L.golden_capture(root, "mermaid", "m-1", "light", _diagram(),
                         trust="validated", run="r1", source="record")
        with pytest.raises(L.LedgerError, match="VALIDATED"):
            L.golden_capture(root, "mermaid", "m-1", "light", _diagram(drop_node=True),
                             trust="provisional", run="r2", source="runner")
        # and the bytes are untouched
        png = L.golden_paths(root, "mermaid", "m-1", "light")[0].read_bytes()
        assert G.pixel_hash(png) == G.pixel_hash(_diagram())

    def test_validated_replaces_and_archives(self, root):
        L.golden_capture(root, "mermaid", "m-1", "light", _diagram(),
                         trust="provisional", run="r1", source="runner")
        rec = L.golden_capture(root, "mermaid", "m-1", "light", _diagram(shift=3),
                               trust="validated", run="r2", source="record")
        assert rec["trust"] == "validated"
        assert [h["trust"] for h in rec["history"]] == ["provisional"]
        archived = list((L.golden_root(root) / "history").rglob("m-1.light.png"))
        assert len(archived) == 1, "prior golden must be recoverable"
        assert G.pixel_hash(archived[0].read_bytes()) == G.pixel_hash(_diagram())

    def test_bad_theme_and_trust_rejected(self, root):
        with pytest.raises(L.LedgerError):
            L.golden_paths(root, "mermaid", "m-1", "sepia")
        with pytest.raises(L.LedgerError):
            L.golden_capture(root, "mermaid", "m-1", "light", _diagram(),
                             trust="gospel", run="r", source="x")


class TestRecordWithImages:
    def test_ok_with_png_becomes_validated_golden_and_syncs_corpus(self, root, tmp_path):
        _spec_on_disk(root, "mermaid", "m-1")
        L.promote(root, engine="mermaid", spec_id="m-1", origin="regression_set")
        lp, dp = tmp_path / "l.png", tmp_path / "d.png"
        lp.write_bytes(_diagram()); dp.write_bytes(_diagram(bg=(30, 30, 30)))
        out = L.record(root, "mermaid", "m-1", run="r1", light=OK, dark=OK,
                       light_png=lp, dark_png=dp)
        assert out["goldens"]["light"]["trust"] == "validated"
        assert out["goldens"]["light"]["in_corpus"] is True
        exp = json.loads((L.corpus_root(root) / "mermaid" / "expectations.json").read_text())
        g = exp["specs"]["m-1"]["golden"]
        assert g["light"]["hash"] == G.pixel_hash(_diagram())
        assert g["dark"]["hash"] == G.pixel_hash(_diagram(bg=(30, 30, 30)))
        # and the verdict itself was still appended as before
        bb = json.loads((root / "mermaid.json").read_text())
        assert bb["specs"][0]["current"]["light"]["status"] == "ok"

    def test_fail_with_png_is_evidence_not_golden(self, root, tmp_path):
        dp = tmp_path / "d.png"; dp.write_bytes(_diagram(drop_node=True))
        out = L.record(root, "mermaid", "m-1", run="r1", light=OK, dark=FAIL, dark_png=dp)
        assert "evidence" in out["goldens"]["dark"]
        assert Path(out["goldens"]["dark"]["evidence"]).exists()
        assert L.golden_get(root, "mermaid", "m-1", "dark") is None
        assert L.golden_get(root, "mermaid", "m-1", "light") is None  # no png given

    def test_missing_png_path_is_an_error_before_any_write(self, root):
        with pytest.raises(L.LedgerError, match="no such file"):
            L.record(root, "mermaid", "m-1", run="r1", light=OK, dark=OK,
                     light_png=Path("/nonexistent/x.png"))
        assert not (root / "mermaid.json").exists()

    def test_record_without_pngs_is_unchanged(self, root):
        out = L.record(root, "mermaid", "m-1", run="r1", light=OK, dark=OK)
        assert "goldens" not in out


class TestPromoteCarriesHash:
    def test_validated_hash_lands_in_expectations(self, root):
        _spec_on_disk(root, "graphviz", "g-1")
        L.golden_capture(root, "graphviz", "g-1", "dark", _diagram(),
                         trust="validated", run="r1", source="record")
        L.golden_capture(root, "graphviz", "g-1", "light", _diagram(),
                         trust="provisional", run="r1", source="runner")
        rec = L.promote(root, engine="graphviz", spec_id="g-1", origin="D-001",
                        signature="node-missing")
        assert rec["golden"] == {"dark": {"hash": G.pixel_hash(_diagram()),
                                          "validated_at": rec["golden"]["dark"]["validated_at"]}}
        assert "light" not in rec["golden"], "provisional goldens are not committed"


class TestTrustFromHash:
    def test_exact_match_validates_mismatch_does_not(self, root):
        _spec_on_disk(root, "mermaid", "m-1"); _spec_on_disk(root, "mermaid", "m-2")
        for sid in ("m-1", "m-2"):
            L.promote(root, engine="mermaid", spec_id=sid, origin="regression_set")
        # "another machine" validated these hashes
        L.golden_capture(root, "mermaid", "m-1", "light", _diagram(),
                         trust="validated", run="r0", source="record")
        L.golden_capture(root, "mermaid", "m-2", "light", _diagram(),
                         trust="validated", run="r0", source="record")
        # this machine: wipe local goldens, re-render provisionally
        import shutil; shutil.rmtree(L.golden_root(root))
        L.golden_capture(root, "mermaid", "m-1", "light", _diagram(),
                         trust="provisional", run="r1", source="runner")
        L.golden_capture(root, "mermaid", "m-2", "light", _diagram(shift=1),
                         trust="provisional", run="r1", source="runner")
        out = L.golden_trust_from_hash(root, run="r1")
        assert out["validated"] == 1 and out["mismatch"] == ["mermaid/m-2[light]"]
        assert L.golden_get(root, "mermaid", "m-1", "light")["trust"] == "validated"
        assert L.golden_get(root, "mermaid", "m-2", "light")["trust"] == "provisional"


class TestRebaseline:
    def test_needs_reason(self, root, tmp_path):
        p = tmp_path / "n.png"; p.write_bytes(_diagram())
        with pytest.raises(L.LedgerError, match="reason"):
            L.golden_rebaseline(root, "mermaid", "m-1", "light", png_path=p, reason="  ",
                                run="r")

    def test_rebaseline_is_validated_and_records_who(self, root, tmp_path):
        L.golden_capture(root, "mermaid", "m-1", "light", _diagram(),
                         trust="validated", run="r0", source="record")
        p = tmp_path / "n.png"; p.write_bytes(_diagram(shift=2))
        rec = L.golden_rebaseline(root, "mermaid", "m-1", "light", png_path=p,
                                  reason="label re-hinted after font upgrade", run="r1",
                                  by="dcohn")
        assert rec["trust"] == "validated" and rec["source"] == "rebaseline:dcohn"
        assert rec["hash"] == G.pixel_hash(_diagram(shift=2))
        assert rec["history"][-1]["hash"] == G.pixel_hash(_diagram())


class TestStatus:
    def test_status_counts_missing_against_corpus(self, root):
        _spec_on_disk(root, "mermaid", "m-1")
        L.promote(root, engine="mermaid", spec_id="m-1", origin="regression_set")
        L.golden_capture(root, "mermaid", "m-1", "light", _diagram(),
                         trust="provisional", run="r", source="runner")
        st = L.golden_status(root)
        assert st["engines"]["mermaid"] == {"provisional": 1, "validated": 0, "missing": 1}


class TestCli:
    def test_golden_cli_roundtrip(self, root, tmp_path, capsys):
        p = tmp_path / "n.png"; p.write_bytes(_diagram())
        rc = L.main(["--root", str(root), "golden", "capture", "--engine", "mermaid",
                     "--spec", "m-1", "--theme", "light", "--png", str(p)])
        assert rc == 0
        capsys.readouterr()
        rc = L.main(["--root", str(root), "golden", "show", "--engine", "mermaid",
                     "--spec", "m-1", "--theme", "light"])
        assert rc == 0 and json.loads(capsys.readouterr().out)["trust"] == "provisional"
        rc = L.main(["--root", str(root), "golden", "rebaseline", "--engine", "mermaid",
                     "--spec", "m-1", "--theme", "light", "--png", str(p)])
        assert rc == 2, "rebaseline without --reason must be refused"

    def test_record_cli_accepts_pngs(self, root, tmp_path, capsys):
        p = tmp_path / "l.png"; p.write_bytes(_diagram())
        rc = L.main(["--root", str(root), "record", "mermaid", "m-1",
                     "--light", json.dumps(OK), "--dark", json.dumps(OK),
                     "--light-png", str(p)])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["goldens"]["light"]["trust"] == "validated"
