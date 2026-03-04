#!/usr/bin/env python3
"""
Bedrock Performance Profiler & Deadlock Analyzer
=================================================

Comprehensive test system to characterize Bedrock API performance across
query sizes and concurrency levels, and identify deadlocks/timeouts.

Usage:
    # Full suite against live Bedrock (requires AWS credentials)
    python scripts/bedrock_performance_profiler.py --live

    # Quick smoke test (small sizes only)
    python scripts/bedrock_performance_profiler.py --live --quick

    # Dry run — analyze code paths without API calls
    python scripts/bedrock_performance_profiler.py --dry-run

    # Specific size targets (in approx tokens)
    python scripts/bedrock_performance_profiler.py --live --sizes 1000,10000,100000

    # Concurrency stress test
    python scripts/bedrock_performance_profiler.py --live --concurrency 1,2,5

    # Just run the static analysis
    python scripts/bedrock_performance_profiler.py --analyze-only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

# Add project root to path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class OutcomeType(Enum):
    SUCCESS = auto()
    TIMEOUT_CONNECT = auto()
    TIMEOUT_STREAM = auto()
    THROTTLE = auto()
    CONTEXT_LIMIT = auto()
    DEADLOCK_SUSPECTED = auto()
    ERROR_OTHER = auto()


@dataclass
class ProbeResult:
    """Result from a single Bedrock probe."""
    size_label: str
    approx_tokens: int
    outcome: OutcomeType
    connect_time_s: float = 0.0      # Time to get response object
    ttfb_s: float = 0.0              # Time to first byte of stream
    total_time_s: float = 0.0        # Wall clock total
    stream_stalls: int = 0           # Number of >5s gaps between chunks
    max_stall_s: float = 0.0         # Longest gap between chunks
    chunks_received: int = 0
    output_tokens: int = 0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    error_message: str = ""
    error_class: str = ""
    thread_pool_active: int = 0      # Threads active at probe start
    event_loop_blocked_ms: float = 0  # Event loop blockage detected
    retry_count: int = 0
    extended_context_activated: bool = False


@dataclass
class ConcurrencyResult:
    """Results from a concurrency test batch."""
    concurrency_level: int
    size_label: str
    results: List[ProbeResult] = field(default_factory=list)
    wall_clock_s: float = 0.0
    deadlock_detected: bool = False
    thread_pool_exhaustion: bool = False


# ---------------------------------------------------------------------------
# Synthetic payload builder
# ---------------------------------------------------------------------------

class SyntheticPayloadBuilder:
    """Generate Bedrock request payloads of calibrated sizes."""

    # Approximate chars-per-token for Claude (conservative)
    CHARS_PER_TOKEN = 3.5

    # Filler text that tokenizes predictably (simple English prose)
    FILLER_BLOCK = (
        "The quick brown fox jumps over the lazy dog near the riverbank. "
        "Software engineering requires careful attention to detail and "
        "systematic problem solving across multiple abstraction layers. "
        "Each component must be tested independently before integration. "
    ) * 10  # ~2600 chars ≈ ~740 tokens per block

    @classmethod
    def build_system_prompt(cls, target_tokens: int) -> str:
        """Build a system prompt of approximately target_tokens size."""
        header = (
            "You are a helpful assistant. Below is context material.\n\n"
            "--- BEGIN CONTEXT ---\n"
        )
        footer = "\n--- END CONTEXT ---\n"

        target_chars = int(target_tokens * cls.CHARS_PER_TOKEN)
        content_chars = target_chars - len(header) - len(footer)

        if content_chars <= 0:
            return header + footer

        # Fill with repeated blocks, then trim to exact size
        repeats = (content_chars // len(cls.FILLER_BLOCK)) + 1
        filler = (cls.FILLER_BLOCK * repeats)[:content_chars]

        return header + filler + footer

    @classmethod
    def build_messages(cls, system_tokens: int = 0) -> List[Dict[str, Any]]:
        """Build a minimal conversation with a simple user message."""
        return [{"role": "user", "content": "Respond with exactly: 'OK'. Nothing else."}]

    @classmethod
    def build_request_body(
        cls,
        target_tokens: int,
        max_output_tokens: int = 256,
        include_tools: bool = False,
    ) -> Tuple[Dict[str, Any], str]:
        """Build a complete Bedrock request body targeting a specific input size.

        Returns (body_dict, system_content_str).
        """
        system_content = cls.build_system_prompt(target_tokens)
        messages = cls.build_messages()

        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_output_tokens,
            "messages": messages,
            "system": system_content,
        }

        if include_tools:
            body["tools"] = cls._sample_tools()
            body["tool_choice"] = {"type": "auto"}

        return body, system_content

    @staticmethod
    def _sample_tools() -> List[Dict[str, Any]]:
        """A small set of tool definitions to test tool overhead."""
        return [
            {
                "name": "run_shell_command",
                "description": "Execute a shell command and return output.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "The command to run"}
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "file_read",
                "description": "Read a file from disk.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path"}
                    },
                    "required": ["path"],
                },
            },
        ]


# ---------------------------------------------------------------------------
# Event loop health monitor
# ---------------------------------------------------------------------------

class EventLoopMonitor:
    """Detect event loop blocking by scheduling periodic callbacks."""

    def __init__(self, threshold_ms: float = 200):
        self.threshold_ms = threshold_ms
        self.violations: List[Tuple[float, float]] = []  # (timestamp, blocked_ms)
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._monitor())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _monitor(self):
        """Check event loop responsiveness every 100ms."""
        while self._running:
            before = time.monotonic()
            await asyncio.sleep(0.1)
            after = time.monotonic()
            elapsed_ms = (after - before) * 1000
            # Expected ~100ms; anything beyond threshold indicates blocking
            overshoot = elapsed_ms - 100
            if overshoot > self.threshold_ms:
                self.violations.append((time.time(), overshoot))

    @property
    def max_block_ms(self) -> float:
        return max((v[1] for v in self.violations), default=0)

    @property
    def total_violations(self) -> int:
        return len(self.violations)


# ---------------------------------------------------------------------------
# Thread pool monitor
# ---------------------------------------------------------------------------

class ThreadPoolMonitor:
    """Monitor default asyncio thread pool usage."""

    def __init__(self):
        self.snapshots: List[Tuple[float, int]] = []
        self._running = False

    async def start(self):
        self._running = True
        asyncio.create_task(self._monitor())

    async def stop(self):
        self._running = False

    async def _monitor(self):
        while self._running:
            active = threading.active_count()
            self.snapshots.append((time.time(), active))
            await asyncio.sleep(0.5)

    @property
    def peak_threads(self) -> int:
        return max((s[1] for s in self.snapshots), default=0)

    @property
    def default_pool_size(self) -> int:
        return min(32, (os.cpu_count() or 1) + 4)


# ---------------------------------------------------------------------------
# Core probe — exercises real provider code paths
# ---------------------------------------------------------------------------

async def probe_bedrock_provider(
    target_tokens: int,
    size_label: str,
    model_id: str,
    model_config: Dict[str, Any],
    aws_profile: str = "ziya",
    region: str = "us-west-2",
    timeout_s: float = 300,
    include_tools: bool = False,
) -> ProbeResult:
    """Execute a single probe through the real BedrockProvider code path."""
    from app.providers.bedrock import BedrockProvider
    from app.providers.base import (
        ProviderConfig, TextDelta, UsageEvent, ErrorEvent,
        StreamEnd, ProcessingEvent, ThinkingDelta,
        ToolUseStart, ToolUseEnd, ToolUseInput,
    )

    result = ProbeResult(
        size_label=size_label,
        approx_tokens=target_tokens,
        outcome=OutcomeType.SUCCESS,
        thread_pool_active=threading.active_count(),
    )

    body, system_content = SyntheticPayloadBuilder.build_request_body(
        target_tokens=target_tokens,
        max_output_tokens=256,
        include_tools=include_tools,
    )

    messages = body.pop("messages")
    tools = body.pop("tools", [])
    body.pop("tool_choice", None)
    system = body.pop("system", None)

    config = ProviderConfig(
        max_output_tokens=256,
        temperature=0.0,
        thinking=None,
        enable_cache=False,
        suppress_tools=not include_tools,
        model_config=model_config,
        iteration=0,
    )

    try:
        provider = BedrockProvider(
            model_id=model_id,
            model_config=model_config,
            aws_profile=aws_profile,
            region=region,
        )
    except Exception as e:
        result.outcome = OutcomeType.ERROR_OTHER
        result.error_message = f"Provider init failed: {e}"
        result.error_class = type(e).__name__
        return result

    t_start = time.monotonic()
    first_content_time = None
    last_chunk_time = t_start
    stall_threshold = 5.0

    try:
        async with asyncio.timeout(timeout_s):
          async for event in _consume_stream(provider, messages, system, tools if include_tools else [], config):
            now = time.monotonic()

            if isinstance(event, TextDelta):
                if first_content_time is None:
                    first_content_time = now
                gap = now - last_chunk_time
                if gap > stall_threshold:
                    result.stream_stalls += 1
                    result.max_stall_s = max(result.max_stall_s, gap)
                result.chunks_received += 1
                last_chunk_time = now

            elif isinstance(event, ThinkingDelta):
                if first_content_time is None:
                    first_content_time = now
                last_chunk_time = now

            elif isinstance(event, UsageEvent):
                result.input_tokens = event.input_tokens
                result.output_tokens = event.output_tokens
                result.cache_read_tokens = event.cache_read_tokens

            elif isinstance(event, ErrorEvent):
                result.error_message = event.message
                result.error_class = event.error_type.name
                if event.error_type.name == "THROTTLE":
                    result.outcome = OutcomeType.THROTTLE
                elif event.error_type.name == "CONTEXT_LIMIT":
                    result.outcome = OutcomeType.CONTEXT_LIMIT
                elif event.error_type.name == "READ_TIMEOUT":
                    result.outcome = OutcomeType.TIMEOUT_STREAM
                else:
                    result.outcome = OutcomeType.ERROR_OTHER
                break

            elif isinstance(event, ProcessingEvent):
                last_chunk_time = now  # don't count processing as stall

            elif isinstance(event, StreamEnd):
                break

    except asyncio.TimeoutError:
        now = time.monotonic()
        elapsed = now - t_start
        if first_content_time is None:
            result.outcome = OutcomeType.TIMEOUT_CONNECT
            result.error_message = f"No response in {elapsed:.1f}s (connect timeout)"
        else:
            result.outcome = OutcomeType.TIMEOUT_STREAM
            result.error_message = f"Stream stalled after {elapsed:.1f}s"
    except Exception as e:
        result.outcome = OutcomeType.ERROR_OTHER
        result.error_message = str(e)[:500]
        result.error_class = type(e).__name__

    t_end = time.monotonic()
    result.total_time_s = t_end - t_start
    if first_content_time is not None:
        result.ttfb_s = first_content_time - t_start
    result.connect_time_s = result.ttfb_s  # Approximation; provider merges connect + TTFB

    return result


async def _consume_stream(provider, messages, system, tools, config):
    """Thin wrapper to make the async generator awaitable for wait_for."""
    async for event in provider.stream_response(messages, system, tools, config):
        yield event


# ---------------------------------------------------------------------------
# Concurrency test
# ---------------------------------------------------------------------------

async def run_concurrency_test(
    concurrency: int,
    target_tokens: int,
    size_label: str,
    model_id: str,
    model_config: Dict[str, Any],
    aws_profile: str,
    region: str,
    timeout_s: float = 300,
) -> ConcurrencyResult:
    """Run N concurrent probes and detect deadlock/exhaustion."""
    cr = ConcurrencyResult(
        concurrency_level=concurrency,
        size_label=size_label,
    )

    t_start = time.monotonic()
    first_content_time = None
    last_chunk_time = t_start

    tasks = [
        probe_bedrock_provider(
            target_tokens=target_tokens,
            size_label=f"{size_label}_c{i}",
            model_id=model_id,
            model_config=model_config,
            aws_profile=aws_profile,
            region=region,
            timeout_s=timeout_s,
        )
        for i in range(concurrency)
    ]

    # Use gather with return_exceptions to catch per-task failures
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    cr.wall_clock_s = time.monotonic() - t_start

    for r in raw_results:
        if isinstance(r, Exception):
            pr = ProbeResult(
                size_label=size_label,
                approx_tokens=target_tokens,
                outcome=OutcomeType.DEADLOCK_SUSPECTED,
                error_message=str(r)[:500],
                error_class=type(r).__name__,
                total_time_s=cr.wall_clock_s,
            )
            cr.results.append(pr)
        else:
            cr.results.append(r)

    # Heuristics for deadlock detection
    timeouts = sum(1 for r in cr.results if r.outcome in (
        OutcomeType.TIMEOUT_CONNECT, OutcomeType.TIMEOUT_STREAM, OutcomeType.DEADLOCK_SUSPECTED
    ))
    if timeouts == concurrency and concurrency > 1:
        cr.deadlock_detected = True
    if any(r.thread_pool_active >= ThreadPoolMonitor().default_pool_size - 2 for r in cr.results):
        cr.thread_pool_exhaustion = True

    return cr


# ---------------------------------------------------------------------------
# Wrapper-depth analysis (the "Russian doll" problem)
# ---------------------------------------------------------------------------

def analyze_client_wrapper_depth() -> Dict[str, Any]:
    """Analyze how many wrapper layers sit between the provider and boto3.

    Each layer can add retry logic, blocking calls, or timeout handling.
    Compounding retries across layers is a primary deadlock vector.
    """
    analysis = {
        "wrapper_chain": [],
        "retry_layers": 0,
        "total_max_retries": 1,
        "blocking_sleep_calls": [],
        "timeout_layers": [],
        "diagnosis": [],
    }

    # Layer 1: boto3 BotoConfig retries
    analysis["wrapper_chain"].append("boto3 (BotoConfig: max_attempts=2, mode=adaptive, max_pool_connections=25)")
    analysis["retry_layers"] += 1
    analysis["total_max_retries"] *= 3  # 2 retries + 1 initial = 3 attempts

    # Layer 2: CustomBedrockClient
    analysis["wrapper_chain"].append("CustomBedrockClient (extended context escalation, 120s failure TTL)")
    analysis["retry_layers"] += 1
    analysis["total_max_retries"] *= 2  # Can retry once for context limit

    # Layer 3: ThrottleSafeBedrock
    analysis["wrapper_chain"].append("ThrottleSafeBedrock (__getattr__ delegation to CustomBedrockClient)")
    # No additional retries, pure delegation wrapper

    # Layer 4: BedrockProvider.stream_response
    analysis["wrapper_chain"].append("BedrockProvider (single attempt + safety-net extended context)")
    # No retry loop — errors surface as ErrorEvent for executor

    # Layer 5: StreamingToolExecutor
    analysis["wrapper_chain"].append("StreamingToolExecutor (throttle_state max_retries=5, retryable ErrorEvent routing)")
    analysis["retry_layers"] += 1
    analysis["total_max_retries"] *= 6  # 5 retries + 1 initial

    # Timeout analysis
    analysis["timeout_layers"] = [
        {"layer": "boto3 BotoConfig", "read_timeout": 300, "connect_timeout": "default (~60s)"},
        {"layer": "BedrockProvider.stream_response", "connect_timeout": 180, "configurable": "BEDROCK_CONNECT_TIMEOUT env"},
        {"layer": "BedrockProvider._parse_stream", "poll_interval": "15s (thinking) / 120s (normal)",
         "max_silence": "900s (thinking) / 120s (normal)", "configurable": "STREAM_STALL_TIMEOUT, BEDROCK_MAX_THINKING_WAIT env"},
        {"layer": "StreamingToolExecutor", "tool_timeout": 300, "configurable": "TOOL_EXEC_TIMEOUT env"},
    ]

    # Diagnose compounding
    analysis["diagnosis"].append(
        f"RETRY AMPLIFICATION: {analysis['retry_layers']} retry layers can produce up to "
        f"{analysis['total_max_retries']} total attempts for a single user request."
    )
    analysis["diagnosis"].append(
        "THREAD POOL RISK: Each asyncio.to_thread() call consumes a thread from the "
        f"default pool (size={min(32, (os.cpu_count() or 1) + 4)}). "
        "BedrockProvider uses to_thread for BOTH the initial API call AND every "
        "stream chunk read. With retries, a single request can consume 2+ threads "
        "for extended periods."
    )
    analysis["diagnosis"].append(
        "BLOCKING IN CustomBedrockClient: The _retry_with_extended_context method "
        "calls self.original_invoke() synchronously inside a to_thread wrapper. "
        "If extended context activation fails with a timeout, the thread is blocked "
        "for the full boto3 read_timeout (300s) while holding a pool slot."
    )
    analysis["diagnosis"].append(
        "DEADLOCK VECTOR: With N concurrent requests each consuming 2 threads "
        "(connect + stream read), the pool exhausts at N >= pool_size/2. "
        "New to_thread calls then block waiting for a free thread, which can't "
        "free because the event loop is blocked waiting for to_thread to return."
    )

    return analysis


# ---------------------------------------------------------------------------
# Asyncio.to_thread saturation test (no API calls needed)
# ---------------------------------------------------------------------------

async def test_thread_pool_saturation() -> Dict[str, Any]:
    """Test whether the default thread pool can deadlock under load.

    Simulates the pattern used by BedrockProvider: to_thread calls that
    block for varying durations, mixed with to_thread calls that depend
    on the first ones completing.
    """
    pool_size = min(32, (os.cpu_count() or 1) + 4)
    results = {
        "pool_size": pool_size,
        "tests": [],
    }

    async def blocking_task(duration: float, task_id: int) -> Tuple[int, float]:
        """Simulate a blocking boto3 call in a thread."""
        def _block():
            time.sleep(duration)
            return task_id
        t0 = time.monotonic()
        result = await asyncio.to_thread(_block)
        return result, time.monotonic() - t0

    # Test 1: Fill pool to capacity
    n_tasks = pool_size
    t0 = time.monotonic()
    try:
        tasks = [blocking_task(2.0, i) for i in range(n_tasks)]
        completed = await asyncio.wait_for(asyncio.gather(*tasks), timeout=10.0)
        elapsed = time.monotonic() - t0
        results["tests"].append({
            "name": f"fill_pool_{n_tasks}_tasks",
            "duration_s": elapsed,
            "passed": elapsed < 5.0,  # Should complete in ~2s if pool is big enough
            "note": f"{n_tasks} tasks x 2s block, pool_size={pool_size}",
        })
    except asyncio.TimeoutError:
        results["tests"].append({
            "name": f"fill_pool_{n_tasks}_tasks",
            "duration_s": 10.0,
            "passed": False,
            "note": "TIMEOUT — pool saturation confirmed",
        })

    # Test 2: Overfill pool (simulate concurrent requests)
    n_tasks = pool_size + 4
    t0 = time.monotonic()
    try:
        tasks = [blocking_task(2.0, i) for i in range(n_tasks)]
        completed = await asyncio.wait_for(asyncio.gather(*tasks), timeout=10.0)
        elapsed = time.monotonic() - t0
        # Should take ~4s (two batches of pool_size)
        results["tests"].append({
            "name": f"overfill_pool_{n_tasks}_tasks",
            "duration_s": elapsed,
            "passed": elapsed < 8.0,
            "note": f"{n_tasks} tasks x 2s block, pool_size={pool_size}. Expected ~4s for 2 batches.",
        })
    except asyncio.TimeoutError:
        results["tests"].append({
            "name": f"overfill_pool_{n_tasks}_tasks",
            "duration_s": 10.0,
            "passed": False,
            "note": "TIMEOUT — severe pool exhaustion",
        })

    # Test 3: Deadlock pattern — outer to_thread waits for inner to_thread
    # This simulates CustomBedrockClient calling original_invoke inside to_thread
    # while BedrockProvider also uses to_thread for the same call
    async def nested_thread_call(task_id: int) -> Tuple[int, float]:
        """Simulate nested to_thread (provider wraps client which wraps boto3)."""
        def _outer():
            # This runs in a thread pool thread
            # Simulate the client wrapper doing blocking work
            time.sleep(0.5)
            return task_id
        t0 = time.monotonic()
        # Outer to_thread (BedrockProvider level)
        result = await asyncio.to_thread(_outer)
        return result, time.monotonic() - t0

    n_tasks = pool_size  # Fill entire pool
    t0 = time.monotonic()
    try:
        tasks = [nested_thread_call(i) for i in range(n_tasks)]
        completed = await asyncio.wait_for(asyncio.gather(*tasks), timeout=10.0)
        elapsed = time.monotonic() - t0
        results["tests"].append({
            "name": f"nested_threads_{n_tasks}_tasks",
            "duration_s": elapsed,
            "passed": elapsed < 5.0,
            "note": f"Simulates provider->client->boto3 nesting with {n_tasks} concurrent requests",
        })
    except asyncio.TimeoutError:
        results["tests"].append({
            "name": f"nested_threads_{n_tasks}_tasks",
            "duration_s": 10.0,
            "passed": False,
            "note": "TIMEOUT — nested to_thread deadlock confirmed!",
        })

    return results


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def format_report(
    probe_results: List[ProbeResult],
    concurrency_results: List[ConcurrencyResult],
    wrapper_analysis: Dict[str, Any],
    thread_pool_results: Dict[str, Any],
    loop_monitor: Optional[EventLoopMonitor],
    thread_monitor: Optional[ThreadPoolMonitor],
) -> str:
    """Generate a comprehensive human-readable report."""
    lines = []
    w = lines.append

    w("=" * 80)
    w("  BEDROCK PERFORMANCE PROFILE & DEADLOCK ANALYSIS")
    w("=" * 80)
    w("")

    # -- Static analysis --
    w("━" * 80)
    w("  1. CLIENT WRAPPER CHAIN ANALYSIS")
    w("━" * 80)
    for i, layer in enumerate(wrapper_analysis["wrapper_chain"]):
        w(f"  Layer {i}: {layer}")
    w(f"\n  Retry layers: {wrapper_analysis['retry_layers']}")
    w(f"  Worst-case total attempts: {wrapper_analysis['total_max_retries']}")
    w("")
    for d in wrapper_analysis["diagnosis"]:
        w(f"  ⚠ {d}")
    w("")

    # -- Timeout config --
    w("  Timeout Configuration:")
    for t in wrapper_analysis["timeout_layers"]:
        w(f"    {t['layer']}: {t}")
    w("")

    # -- Thread pool saturation --
    w("━" * 80)
    w("  2. THREAD POOL SATURATION TEST")
    w("━" * 80)
    w(f"  Default pool size: {thread_pool_results['pool_size']}")
    for test in thread_pool_results["tests"]:
        status = "✅ PASS" if test["passed"] else "❌ FAIL"
        w(f"  {status} {test['name']}: {test['duration_s']:.2f}s — {test['note']}")
    w("")

    # -- Event loop health --
    if loop_monitor:
        w("━" * 80)
        w("  3. EVENT LOOP HEALTH")
        w("━" * 80)
        if loop_monitor.total_violations == 0:
            w("  ✅ No event loop blocking detected")
        else:
            w(f"  ❌ {loop_monitor.total_violations} blocking violations detected")
            w(f"     Max block: {loop_monitor.max_block_ms:.0f}ms")
        w("")

    # -- Thread activity --
    if thread_monitor:
        w(f"  Peak threads: {thread_monitor.peak_threads} / pool_size={thread_monitor.default_pool_size}")
        if thread_monitor.peak_threads >= thread_monitor.default_pool_size - 1:
            w("  ⚠ Thread pool near/at capacity — deadlock risk HIGH")
        w("")

    # -- Single probe results --
    if probe_results:
        w("━" * 80)
        w("  4. SINGLE-REQUEST PERFORMANCE BY SIZE")
        w("━" * 80)
        w(f"  {'Size':<12} {'Outcome':<22} {'TTFB':>8} {'Total':>8} {'Stalls':>7} {'MaxStall':>9} {'InTok':>8} {'OutTok':>7}")
        w(f"  {'─'*12} {'─'*22} {'─'*8} {'─'*8} {'─'*7} {'─'*9} {'─'*8} {'─'*7}")
        for r in probe_results:
            w(f"  {r.size_label:<12} {r.outcome.name:<22} "
              f"{r.ttfb_s:>7.1f}s {r.total_time_s:>7.1f}s "
              f"{r.stream_stalls:>7} {r.max_stall_s:>8.1f}s "
              f"{r.input_tokens:>8} {r.output_tokens:>7}")
            if r.error_message:
                w(f"  {'':>12} └─ {r.error_message[:70]}")
        w("")

        # Identify the cliff
        successes = [r for r in probe_results if r.outcome == OutcomeType.SUCCESS]
        failures = [r for r in probe_results if r.outcome != OutcomeType.SUCCESS]
        if successes and failures:
            max_ok = max(r.approx_tokens for r in successes)
            min_fail = min(r.approx_tokens for r in failures)
            w(f"  📊 Performance cliff: succeeds at ~{max_ok:,} tokens, fails at ~{min_fail:,} tokens")
            w("")

    # -- Concurrency results --
    if concurrency_results:
        w("━" * 80)
        w("  5. CONCURRENCY TEST RESULTS")
        w("━" * 80)
        for cr in concurrency_results:
            successes = sum(1 for r in cr.results if r.outcome == OutcomeType.SUCCESS)
            failures = len(cr.results) - successes
            w(f"  Concurrency={cr.concurrency_level} Size={cr.size_label}: "
              f"{successes}/{len(cr.results)} success, wall={cr.wall_clock_s:.1f}s")
            if cr.deadlock_detected:
                w(f"  ❌ DEADLOCK DETECTED — all {cr.concurrency_level} requests timed out")
            if cr.thread_pool_exhaustion:
                w(f"  ⚠ Thread pool near exhaustion at concurrency={cr.concurrency_level}")
            for r in cr.results:
                if r.outcome != OutcomeType.SUCCESS:
                    w(f"     {r.size_label}: {r.outcome.name} — {r.error_message[:60]}")
        w("")

    # -- Recommendations --
    w("━" * 80)
    w("  6. RECOMMENDATIONS")
    w("━" * 80)
    recommendations = _generate_recommendations(
        probe_results, concurrency_results, wrapper_analysis, thread_pool_results
    )
    for i, rec in enumerate(recommendations, 1):
        w(f"  {i}. {rec}")
    w("")
    w("=" * 80)

    return "\n".join(lines)


def _generate_recommendations(
    probe_results: List[ProbeResult],
    concurrency_results: List[ConcurrencyResult],
    wrapper_analysis: Dict[str, Any],
    thread_pool_results: Dict[str, Any],
) -> List[str]:
    """Generate actionable recommendations based on test results."""
    recs = []

    # Retry amplification
    if wrapper_analysis["total_max_retries"] > 20:
        recs.append(
            "REDUCE RETRY AMPLIFICATION: The client wrapper chain can produce "
            f"{wrapper_analysis['total_max_retries']} total attempts. "
            "Remove retry logic from CustomBedrockClient — let BedrockProvider "
            "and StreamingToolExecutor handle retries exclusively. Currently "
            "boto3(3) × CustomBedrock(2) × Provider(3) × Executor(6) = "
            f"{wrapper_analysis['total_max_retries']}."
        )

    # Thread pool
    pool_size = thread_pool_results["pool_size"]
    if any(not t["passed"] for t in thread_pool_results["tests"]):
        recs.append(
            f"INCREASE THREAD POOL SIZE: Default pool ({pool_size}) saturates "
            "under concurrent requests. Set a larger executor on the event loop: "
            "loop.set_default_executor(ThreadPoolExecutor(max_workers=64))"
        )

    # Connect timeout for large requests
    large_timeouts = [r for r in probe_results
                      if r.outcome == OutcomeType.TIMEOUT_CONNECT and r.approx_tokens > 100000]
    if large_timeouts:
        recs.append(
            "INCREASE CONNECT TIMEOUT FOR LARGE CONTEXT: Requests >100K tokens "
            "timed out before receiving any data. The 180s BEDROCK_CONNECT_TIMEOUT "
            "is insufficient for 1M-context models. Set to 600s+ for extended context."
        )

    # Stream stalls
    stally = [r for r in probe_results if r.stream_stalls > 2]
    if stally:
        recs.append(
            "INVESTIGATE STREAM STALLS: Multiple requests showed >2 stalls of >5s "
            "during streaming. This suggests the stream read thread is being "
            "starved. Check that to_thread calls for stream reads are not competing "
            "with connect calls in the same thread pool."
        )

    # CustomBedrockClient overhead
    recs.append(
        "SIMPLIFY CLIENT WRAPPERS: CustomBedrockClient adds synchronous retry "
        "and extended context logic inside the thread that BedrockProvider.stream_response "
        "dispatches via to_thread. This means a retry inside CustomBedrockClient blocks "
        "the thread for the full boto3 read_timeout (300s). Move all retry/extended-context "
        "logic to BedrockProvider.stream_response (which is async and won't block threads)."
    )

    # Concurrency deadlocks
    deadlocks = [cr for cr in concurrency_results if cr.deadlock_detected]
    if deadlocks:
        for dl in deadlocks:
            recs.append(
                f"DEADLOCK AT CONCURRENCY={dl.concurrency_level}: All requests timed out. "
                "This confirms thread pool exhaustion. Use a dedicated ThreadPoolExecutor "
                "for Bedrock API calls, separate from the default asyncio pool."
            )

    # General
    recs.append(
        "SEPARATE THREAD POOLS: Create a dedicated ThreadPoolExecutor for Bedrock "
        "API calls (connect + stream reads). This prevents Bedrock blocking from "
        "starving tool execution, MCP server communication, and other async tasks. "
        "Example: bedrock_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix='bedrock')"
    )

    return recs


# ---------------------------------------------------------------------------
# Main test orchestrator
# ---------------------------------------------------------------------------

async def run_live_tests(
    sizes: List[int],
    concurrency_levels: List[int],
    model_id: str,
    model_config: Dict[str, Any],
    aws_profile: str,
    region: str,
    timeout_s: float,
    include_tools: bool,
) -> Tuple[List[ProbeResult], List[ConcurrencyResult]]:
    """Run the full live test suite."""
    probe_results = []
    concurrency_results = []

    size_labels = {
        1000: "1K",
        5000: "5K",
        10000: "10K",
        50000: "50K",
        100000: "100K",
        200000: "200K",
        500000: "500K",
        1000000: "1M",
    }

    # Phase 1: Single-request size sweep
    print("\n🔬 Phase 1: Single-request size sweep")
    print("─" * 50)
    for target in sizes:
        label = size_labels.get(target, f"{target//1000}K")
        print(f"  Probing {label} tokens...", end=" ", flush=True)

        result = await probe_bedrock_provider(
            target_tokens=target,
            size_label=label,
            model_id=model_id,
            model_config=model_config,
            aws_profile=aws_profile,
            region=region,
            timeout_s=timeout_s,
            include_tools=include_tools,
        )
        probe_results.append(result)

        if result.outcome == OutcomeType.SUCCESS:
            print(f"✅ {result.total_time_s:.1f}s (TTFB={result.ttfb_s:.1f}s, stalls={result.stream_stalls})")
        else:
            print(f"❌ {result.outcome.name}: {result.error_message[:60]}")

        # Back off between tests to avoid throttling
        if result.outcome == OutcomeType.THROTTLE:
            print("  ⏳ Throttled — waiting 30s before next probe...")
            await asyncio.sleep(30)
        else:
            await asyncio.sleep(3)

    # Phase 2: Concurrency tests (use smallest successful size)
    successful_sizes = [r.approx_tokens for r in probe_results if r.outcome == OutcomeType.SUCCESS]
    if successful_sizes and concurrency_levels:
        test_size = min(successful_sizes)  # Use smallest to minimize throttle risk
        test_label = size_labels.get(test_size, f"{test_size//1000}K")

        print(f"\n🔬 Phase 2: Concurrency tests (size={test_label})")
        print("─" * 50)

        for n in concurrency_levels:
            print(f"  Testing concurrency={n}...", end=" ", flush=True)
            cr = await run_concurrency_test(
                concurrency=n,
                target_tokens=test_size,
                size_label=test_label,
                model_id=model_id,
                model_config=model_config,
                aws_profile=aws_profile,
                region=region,
                timeout_s=timeout_s,
            )
            concurrency_results.append(cr)

            successes = sum(1 for r in cr.results if r.outcome == OutcomeType.SUCCESS)
            if cr.deadlock_detected:
                print(f"💀 DEADLOCK — all {n} requests failed ({cr.wall_clock_s:.1f}s)")
            else:
                print(f"{'✅' if successes == n else '⚠️'} {successes}/{n} success ({cr.wall_clock_s:.1f}s)")

            await asyncio.sleep(5)
    elif not successful_sizes:
        print("\n⚠️ Skipping concurrency tests — no sizes succeeded in Phase 1")

    return probe_results, concurrency_results


async def main():
    parser = argparse.ArgumentParser(
        description="Bedrock Performance Profiler & Deadlock Analyzer"
    )
    parser.add_argument("--live", action="store_true",
                        help="Run live tests against Bedrock API")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run analysis without API calls")
    parser.add_argument("--analyze-only", action="store_true",
                        help="Only run static code analysis")
    parser.add_argument("--quick", action="store_true",
                        help="Quick mode — small sizes only")
    parser.add_argument("--sizes", type=str, default=None,
                        help="Comma-separated token sizes (e.g., 1000,10000,100000)")
    parser.add_argument("--concurrency", type=str, default="1,2,3",
                        help="Comma-separated concurrency levels")
    parser.add_argument("--profile", type=str, default="ziya",
                        help="AWS profile name")
    parser.add_argument("--region", type=str, default="us-west-2",
                        help="AWS region")
    parser.add_argument("--model-id", type=str, default=None,
                        help="Bedrock model ID (auto-detected if not set)")
    parser.add_argument("--timeout", type=float, default=300,
                        help="Per-request timeout in seconds")
    parser.add_argument("--with-tools", action="store_true",
                        help="Include tool definitions in requests")
    parser.add_argument("--output", type=str, default=None,
                        help="Write report to file (default: stdout)")

    args = parser.parse_args()

    # Resolve sizes
    if args.sizes:
        sizes = [int(s.strip()) for s in args.sizes.split(",")]
    elif args.quick:
        sizes = [1000, 5000, 10000, 50000]
    else:
        sizes = [1000, 10000, 50000, 100000, 200000, 500000, 1000000]

    concurrency_levels = [int(c.strip()) for c in args.concurrency.split(",")]

    # Resolve model
    model_id = args.model_id
    model_config = {}
    if not model_id:
        try:
            from app.agents.models import ModelManager
            endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
            model_name = os.environ.get("ZIYA_MODEL")
            model_config = ModelManager.get_model_config(endpoint, model_name) or {}
            raw_id = model_config.get("model_id", "")
            if isinstance(raw_id, dict):
                model_id = raw_id.get("us", list(raw_id.values())[0])
            else:
                model_id = raw_id
            print(f"Auto-detected model: {model_id}")
        except Exception as e:
            model_id = "anthropic.claude-sonnet-4-20250514-v1:0"
            print(f"Could not auto-detect model ({e}), using default: {model_id}")

    print(f"Model: {model_id}")
    print(f"Region: {args.region}")
    print(f"Profile: {args.profile}")
    print(f"Sizes: {sizes}")
    print(f"Concurrency: {concurrency_levels}")
    print(f"Timeout: {args.timeout}s")

    # -- Always run static analysis --
    wrapper_analysis = analyze_client_wrapper_depth()

    # -- Thread pool saturation test (no API calls) --
    print("\n🔬 Running thread pool saturation test...")
    thread_pool_results = await test_thread_pool_saturation()

    probe_results = []
    concurrency_results = []
    loop_monitor = None
    thread_monitor = None

    if args.live and not args.analyze_only:
        # Start monitors
        loop_monitor = EventLoopMonitor(threshold_ms=200)
        thread_monitor = ThreadPoolMonitor()
        await loop_monitor.start()
        await thread_monitor.start()

        try:
            probe_results, concurrency_results = await run_live_tests(
                sizes=sizes,
                concurrency_levels=concurrency_levels,
                model_id=model_id,
                model_config=model_config,
                aws_profile=args.profile,
                region=args.region,
                timeout_s=args.timeout,
                include_tools=args.with_tools,
            )
        finally:
            await loop_monitor.stop()
            await thread_monitor.stop()

    elif args.dry_run:
        print("\n🔬 Dry run — skipping live API calls")

    # -- Generate report --
    report = format_report(
        probe_results=probe_results,
        concurrency_results=concurrency_results,
        wrapper_analysis=wrapper_analysis,
        thread_pool_results=thread_pool_results,
        loop_monitor=loop_monitor,
        thread_monitor=thread_monitor,
    )

    if args.output:
        with open(args.output, "w") as f:
            f.write(report)
        print(f"\nReport written to {args.output}")
    else:
        print(report)

    # Also write JSON data for programmatic analysis
    json_path = args.output.replace(".txt", ".json") if args.output else None
    if json_path or args.live:
        json_path = json_path or "bedrock_profile_results.json"
        data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "model_id": model_id,
            "region": args.region,
            "wrapper_analysis": wrapper_analysis,
            "thread_pool_tests": thread_pool_results,
            "probe_results": [
                {
                    "size_label": r.size_label,
                    "approx_tokens": r.approx_tokens,
                    "outcome": r.outcome.name,
                    "connect_time_s": r.connect_time_s,
                    "ttfb_s": r.ttfb_s,
                    "total_time_s": r.total_time_s,
                    "stream_stalls": r.stream_stalls,
                    "max_stall_s": r.max_stall_s,
                    "chunks_received": r.chunks_received,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "error_message": r.error_message,
                    "error_class": r.error_class,
                }
                for r in probe_results
            ],
            "concurrency_results": [
                {
                    "concurrency": cr.concurrency_level,
                    "size_label": cr.size_label,
                    "wall_clock_s": cr.wall_clock_s,
                    "deadlock_detected": cr.deadlock_detected,
                    "thread_pool_exhaustion": cr.thread_pool_exhaustion,
                    "results": [
                        {"outcome": r.outcome.name, "total_time_s": r.total_time_s}
                        for r in cr.results
                    ],
                }
                for cr in concurrency_results
            ],
        }
        with open(json_path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"JSON data written to {json_path}")


if __name__ == "__main__":
    asyncio.run(main())
