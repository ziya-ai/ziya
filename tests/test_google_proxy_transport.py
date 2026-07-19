"""Regression tripwires for google-genai proxy/CA-bundle support.

Ziya's --proxy / --ca-bundle flags fan out to HTTPS_PROXY and
SSL_CERT_FILE (app/config/environment.py).  The Bedrock and
Anthropic/OpenAI stacks honor those natively; the Google endpoint
depends on two google-genai SDK behaviors that were verified
empirically (SDK 1.70.0) and are pinned here so a future SDK upgrade
that regresses either one fails loudly:

  1. The SDK's aiohttp session is created with trust_env=True, so
     HTTPS_PROXY is honored and an https request goes out as a
     CONNECT tunnel through the proxy.
  2. The SDK builds its SSL context from os.environ['SSL_CERT_FILE']
     (falling back to certifi) for both httpx and aiohttp paths.

These tests make no external network calls: the proxy test connects
only to an in-process localhost listener.
"""

import asyncio
import os
import ssl

import pytest

genai = pytest.importorskip("google.genai")


@pytest.fixture(autouse=True)
def _isolate_client_cache():
    """Our provider caches clients module-wide; these tests build raw
    genai.Client instances, but clear proxy env afterwards regardless."""
    saved = {k: os.environ.get(k) for k in ("HTTPS_PROXY", "HTTP_PROXY", "SSL_CERT_FILE")}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_async_path_honors_https_proxy():
    """A generate_content call with HTTPS_PROXY set must reach the
    proxy as a CONNECT request (aiohttp trust_env=True behavior)."""
    hits = []

    async def scenario():
        async def handle(reader, writer):
            data = await reader.read(4096)
            hits.append(data)
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        os.environ["HTTPS_PROXY"] = f"http://127.0.0.1:{port}"
        os.environ["HTTP_PROXY"] = f"http://127.0.0.1:{port}"

        client = genai.Client(api_key="fake-key-transport-test")
        with pytest.raises(Exception):
            # Fails (fake proxy hangs up) — we only care where it connected.
            await client.aio.models.generate_content(
                model="gemini-2.0-flash", contents="hi",
            )
        await asyncio.sleep(0.1)
        server.close()
        await server.wait_closed()

    asyncio.run(scenario())

    assert hits, (
        "google-genai did NOT route through HTTPS_PROXY — the SDK's "
        "aiohttp session may have dropped trust_env=True.  Ziya's "
        "--proxy flag no longer covers the Google endpoint."
    )
    assert hits[0].startswith(b"CONNECT "), f"expected CONNECT tunnel, got: {hits[0][:60]!r}"


def test_ssl_context_reads_ssl_cert_file(tmp_path):
    """The SDK must build its SSL context from SSL_CERT_FILE.  We point
    it at a single-cert PEM and assert the resulting CA store holds
    exactly one CA (the default certifi bundle holds >100)."""
    import certifi

    # Extract the first certificate from the certifi bundle.
    bundle = open(certifi.where()).read()
    end_marker = "-----END CERTIFICATE-----"
    first_cert = bundle[: bundle.index(end_marker) + len(end_marker)] + "\n"
    pem = tmp_path / "single-ca.pem"
    pem.write_text(first_cert)

    os.environ["SSL_CERT_FILE"] = str(pem)
    client = genai.Client(api_key="fake-key-ssl-test")

    # The aiohttp request args carry the SSL context the SDK built.
    args = getattr(client._api_client, "_async_client_session_request_args", None)
    if not args or not isinstance(args.get("ssl"), ssl.SSLContext):
        pytest.skip("SDK internals changed shape; update this probe")
    ctx = args["ssl"]
    stats = ctx.cert_store_stats()
    assert stats["x509_ca"] == 1, (
        f"expected exactly the 1 CA from SSL_CERT_FILE, got {stats} — "
        "the SDK may have stopped reading SSL_CERT_FILE"
    )
