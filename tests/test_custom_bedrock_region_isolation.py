"""A per-region Bedrock client must not move the whole process's region.

CustomBedrockClient.__init__ used to write os.environ["AWS_REGION"] from the
client it wrapped.  BedrockRegionRouter creates such a client for every
failover region, so one opus5.5 ServiceUnavailable in us-west-2 (observed
2026-10-07 11:00) failed over to eu-west-1 and left AWS_REGION=eu-west-1 for
any later code that reads get_current_region().  Some later fable5.1 requests
were sent to eu-west-1 and rejected by its never-set retention switch; whether
they got eu-west-1 from this write is not yet confirmed.
"""
from types import SimpleNamespace

from app.utils.custom_bedrock import CustomBedrockClient


class _Client:
    def __init__(self, region):
        self.meta = SimpleNamespace(region_name=region)

    def invoke_model_with_response_stream(self, **_kw):
        return {"body": []}


def test_failover_client_does_not_rewrite_process_region(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    wrapped = CustomBedrockClient(_Client("eu-west-1"))
    assert wrapped.region == "eu-west-1"          # the client itself is regional
    import os
    assert os.environ["AWS_REGION"] == "us-west-2"  # the process is not


def test_unset_region_stays_unset(monkeypatch):
    monkeypatch.delenv("AWS_REGION", raising=False)
    CustomBedrockClient(_Client("eu-west-1"))
    import os
    assert "AWS_REGION" not in os.environ
