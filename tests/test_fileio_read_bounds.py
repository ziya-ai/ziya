"""Regression test for PenPal #96 [CWE-400]: FileReadTool bounds its read.

`FileReadTool.execute` previously did `resolved.read_text()`, slurping the
whole file into memory before the offset/limit slice. A multi-GB file would
OOM the process. The fix reads at most ZIYA_MAX_FILE_READ_BYTES(+1) bytes and
flags truncation in the returned metadata.
"""
import os

import pytest

from app.mcp.tools.fileio import FileReadTool


@pytest.mark.asyncio
async def test_small_file_reads_fully(tmp_path):
    os.environ["ZIYA_MAX_FILE_READ_BYTES"] = "1000"
    f = tmp_path / "small.txt"
    f.write_text("hello\nworld\n")
    res = await FileReadTool().execute(_workspace_path=str(tmp_path), path="small.txt")
    assert "hello" in res.get("content", "")
    assert "read cap" not in res.get("metadata", "")


@pytest.mark.asyncio
async def test_large_file_is_truncated_with_note(tmp_path):
    os.environ["ZIYA_MAX_FILE_READ_BYTES"] = "100"
    f = tmp_path / "big.txt"
    f.write_text("A" * 5000 + "\n")
    res = await FileReadTool().execute(_workspace_path=str(tmp_path), path="big.txt")
    content = res.get("content", "")
    assert len(content) <= 200  # bounded by the 100-byte cap, not 5000
    assert "read cap" in res.get("metadata", "")


@pytest.mark.asyncio
async def test_cap_measured_in_bytes_for_multibyte(tmp_path):
    os.environ["ZIYA_MAX_FILE_READ_BYTES"] = "300"
    f = tmp_path / "mb.txt"
    f.write_text("\u4e2d" * 2000)  # 3 bytes each = 6000 bytes
    res = await FileReadTool().execute(_workspace_path=str(tmp_path), path="mb.txt")
    assert "read cap" in res.get("metadata", "")


@pytest.mark.asyncio
async def test_negative_control_unbounded_would_read_everything(tmp_path):
    # Prove the bound is what limits it: a large file under a tiny cap yields
    # far less content than the file holds.
    os.environ["ZIYA_MAX_FILE_READ_BYTES"] = "50"
    f = tmp_path / "huge.txt"
    original = "line\n" * 10000
    f.write_text(original)
    res = await FileReadTool().execute(_workspace_path=str(tmp_path), path="huge.txt")
    assert len(res.get("content", "")) < len(original)
    assert "read cap" in res.get("metadata", "")


def teardown_function(_):
    os.environ.pop("ZIYA_MAX_FILE_READ_BYTES", None)
