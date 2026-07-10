"""
PenPal #90 regression (server-side, defense-in-depth): RegistryServiceInfo
nulls out non-http(s) provider-supplied URLs in __post_init__, so a
malicious registry entry's `javascript:`/`data:` URL never reaches the UI's
window.open() sink even if the frontend guard is bypassed or removed.
"""
from datetime import datetime

from app.mcp.registry.interface import (
    RegistryServiceInfo, ServiceStatus, SupportLevel, InstallationType,
)


def _make(**url_overrides):
    return RegistryServiceInfo(
        service_id="svc",
        service_name="Svc",
        service_description="d",
        version=1,
        status=ServiceStatus.ACTIVE,
        support_level=SupportLevel.COMMUNITY,
        created_at=datetime.now(),
        last_updated_at=datetime.now(),
        installation_instructions={},
        installation_type=InstallationType.UNKNOWN,
        **url_overrides,
    )


class TestRegistryUrlSanitization:
    def test_http_and_https_preserved(self):
        s = _make(
            repository_url="https://github.com/x/y",
            homepage_url="http://example.com",
        )
        assert s.repository_url == "https://github.com/x/y"
        assert s.homepage_url == "http://example.com"

    def test_javascript_url_nulled(self):
        s = _make(repository_url="javascript:alert(document.cookie)")
        assert s.repository_url is None

    def test_data_and_vbscript_urls_nulled(self):
        s = _make(
            security_review_url="data:text/html,<script>alert(1)</script>",
            documentation_url="vbscript:msgbox(1)",
        )
        assert s.security_review_url is None
        assert s.documentation_url is None

    def test_all_four_url_fields_are_guarded(self):
        s = _make(
            homepage_url="javascript:1",
            repository_url="javascript:2",
            security_review_url="javascript:3",
            documentation_url="javascript:4",
        )
        assert s.homepage_url is None
        assert s.repository_url is None
        assert s.security_review_url is None
        assert s.documentation_url is None

    def test_none_stays_none(self):
        s = _make()
        assert s.repository_url is None
        assert s.homepage_url is None

    def test_whitespace_wrapped_javascript_nulled(self):
        s = _make(repository_url="  javascript:alert(1)  ")
        assert s.repository_url is None
