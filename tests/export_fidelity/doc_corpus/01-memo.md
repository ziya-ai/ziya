---
ziya-doc: 1
title: "Cache Eviction Change"
subtitle: "Why the edge tier moves from LRU to W-TinyLFU on 1 December"
author: "Platform Storage"
date: 2026-11-13
layout: report
page:
  margin: {top: 16mm, bottom: 18mm, left: 19mm, right: 19mm}
---

# Decision

The edge cache tier will switch its eviction policy from LRU to W-TinyLFU on 1 December. The change is a configuration flag; no data migration is needed and the rollback is the same flag.

# Why now

Hit rate on the edge tier has fallen from 91% to 84% since the catalogue service began issuing bulk scans in September. A scan touches every key once, which under LRU pushes out the keys that are read thousands of times an hour. W-TinyLFU admits a new key only if it has been seen more often than the key it would replace, so one pass of a scan does not displace hot entries.

The replay of last Tuesday's traffic against both policies gave these results:

- Hit rate rose from 84% to 93%.
- Origin requests fell by a little more than half.
- Median latency did not move; the 99th percentile fell from 212 ms to 140 ms.
- Memory use rose by about 1%, for the frequency sketch.

# What changes for you

1. Nothing, if you only read through the cache.
2. If you warm the cache on deploy, one pass is no longer enough to pin a key. Read each key twice, or use the `/warm` endpoint, which bypasses admission.
3. If you alert on eviction count, expect it to drop sharply; re-baseline the alert after the first week.

> **Note:** The admission filter resets its counters every ten minutes. A key that was hot yesterday and cold today is evicted on the normal schedule.

# Rollback

Set `cache.policy=lru` and restart the tier. The cache contents survive the restart. We will hold the old dashboards for 30 days so the two policies can be compared on live traffic.

# Open questions

- Whether the ten-minute reset is right for the batch tier, where access patterns repeat daily.
- Whether the `/warm` endpoint should be rate limited.
