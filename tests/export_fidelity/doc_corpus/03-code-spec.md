---
ziya-doc: 1
title: "Retry Budget API"
subtitle: "Specification for the client-side retry limiter, version 2"
author: "RPC Framework"
date: 2026-11-13
layout: report
page:
  margin: {top: 16mm, bottom: 18mm, left: 19mm, right: 19mm}
---

# Overview

A retry budget caps the share of a client's traffic that may be retries. Each successful first attempt deposits a fraction of a token; each retry withdraws one. When the bucket is empty the client fails fast instead of retrying. This keeps a struggling server from receiving a multiple of its normal load.

# Configuration

The budget is configured per destination service. All fields are optional.

```yaml
retry_budget:
  ratio: 0.1            # tokens deposited per successful first attempt
  min_per_second: 3     # retries always allowed, regardless of the bucket
  max_tokens: 100       # bucket capacity
  ttl: 10s              # deposits older than this expire
```

| Field | Type | Default | Meaning |
|---|---|---|---|
| `ratio` | float | `0.1` | Retries permitted as a fraction of first attempts. |
| `min_per_second` | int | `3` | Floor that applies even to an idle client. |
| `max_tokens` | int | `100` | Upper bound on saved-up retries. |
| `ttl` | duration | `10s` | Sliding window over which deposits count. |

# Interface

## Python

```python
class RetryBudget:
    """Token bucket that limits retries to a fraction of first attempts."""

    def __init__(self, ratio: float = 0.1, min_per_second: int = 3,
                 max_tokens: int = 100, ttl: float = 10.0) -> None:
        self._ratio = ratio
        self._floor = min_per_second
        self._cap = max_tokens
        self._window = _SlidingWindow(ttl)

    def record_success(self) -> None:
        """Call once per successful FIRST attempt (never for a retry)."""
        self._window.add(self._ratio)

    def try_acquire(self) -> bool:
        """Return True and withdraw a token if a retry is allowed now."""
        balance = min(self._window.total(), self._cap) + self._floor_credit()
        if balance < 1.0:
            return False
        self._window.add(-1.0)
        return True
```

Usage inside a call wrapper:

```python
def call_with_retries(stub, request, budget: RetryBudget, attempts: int = 3):
    for attempt in range(attempts):
        try:
            response = stub(request, timeout=_deadline_for(attempt))
        except Unavailable as exc:
            if attempt + 1 == attempts or not budget.try_acquire():
                raise RetryBudgetExhausted(f"gave up after {attempt + 1} attempt(s)") from exc
            time.sleep(_backoff(attempt))
            continue
        if attempt == 0:
            budget.record_success()
        return response
```

## Wire format

Retries carry the attempt number so servers can shed them first. The header is `x-retry-attempt` and its value is a decimal integer starting at 1.

```json
{
  "method": "catalog.v2.Items/Get",
  "headers": {
    "x-retry-attempt": "2",
    "x-retry-budget-remaining": "37.4",
    "x-request-id": "b3f1c2e4-9a6d-4e0f-8c55-1d2a7e90f6ab"
  },
  "deadline_ms": 250
}
```

# Behaviour change from version 1

Version 1 counted retries against the budget only after they failed. Version 2 withdraws the token before the retry is sent:

```diff
--- a/rpc/retry.py
+++ b/rpc/retry.py
@@ -41,9 +41,8 @@ def call_with_retries(stub, request, budget, attempts=3):
         try:
             response = stub(request, timeout=_deadline_for(attempt))
         except Unavailable as exc:
-            if attempt + 1 == attempts:
+            if attempt + 1 == attempts or not budget.try_acquire():
                 raise RetryBudgetExhausted(...) from exc
-            budget.record_failure()
             time.sleep(_backoff(attempt))
             continue
```

# Operating it

Check the remaining budget of a running client:

```bash
curl -s localhost:9901/debug/retry_budget | jq '.destinations[] | select(.name == "catalog") | {name, tokens, ratio, exhausted_total}'
```

A client whose `exhausted_total` counter is rising is being told to stop retrying. That is the limiter working, and the fix belongs on the server side.

> **Note:** Hedged requests are not retries and do not draw on the budget. They are limited separately by `hedging.max_ratio`.

# Error reference

| Error | When | Retryable by caller |
|---|---|---|
| `RetryBudgetExhausted` | The bucket was empty when a retry was due. | No |
| `DeadlineExceeded` | The overall deadline passed between attempts. | No |
| `Unavailable` | The final attempt failed with a transport error. | Yes, after backoff |
