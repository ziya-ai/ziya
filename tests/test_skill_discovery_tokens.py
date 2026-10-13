"""A discovered skill's tokenCount is known at list time (load_body=False):
the bench shows every skill's weight, not only the loaded ones'."""
from app.services.skill_discovery import discover_user_skills
from app.services.token_service import TokenService


def test_unloaded_skill_still_reports_its_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    d = tmp_path / "skills" / "weighty"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: weighty\ndescription: d\n---\n" + "word " * 200 + "\n")
    ts = TokenService()
    light = {s.name: s for s in discover_user_skills(ts, load_body=False)}["weighty"]
    full = {s.name: s for s in discover_user_skills(ts, load_body=True)}["weighty"]
    assert light.prompt == "" and full.prompt
    assert light.tokenCount == full.tokenCount > 0
