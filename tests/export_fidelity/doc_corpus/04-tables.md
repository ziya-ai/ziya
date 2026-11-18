---
ziya-doc: 1
title: "Regional Cost Review"
subtitle: "Compute and storage spend by region, third quarter"
author: "Cloud FinOps"
date: 2026-11-13
layout: report
page:
  margin: {top: 16mm, bottom: 18mm, left: 19mm, right: 19mm}
---

# Headline

Total spend for the quarter was $4.82M, 6% over plan. Two regions account for the whole overrun: eu-west, where the analytics cluster was not scaled down after the migration, and ap-south, where egress doubled.

Table: Spend against plan by region, in thousands of dollars.

| Region | Plan | Actual | Variance | Variance % |
|---|---:|---:|---:|---:|
| us-east | 1,640 | 1,612 | −28 | −1.7% |
| us-west | 910 | 902 | −8 | −0.9% |
| eu-west | 980 | 1,174 | +194 | +19.8% |
| eu-central | 420 | 431 | +11 | +2.6% |
| ap-south | 360 | 472 | +112 | +31.1% |
| ap-northeast | 240 | 229 | −11 | −4.6% |
| **Total** | **4,550** | **4,820** | **+270** | **+5.9%** |

# Drivers

Table: The five largest contributors to the variance.

| Driver | Region | Amount ($K) | Cause | Owner | Status |
|---|---|---:|---|---|---|
| Analytics cluster left at peak size | eu-west | 151 | The migration runbook had no scale-down step, so 48 nodes ran idle for seven weeks. | Data Platform | Scaled down 4 November |
| Cross-region egress | ap-south | 96 | The replica in ap-south began serving reads for eu-west during the failover test and was never switched back. | Storage | Fix scheduled |
| Unattached volumes | eu-west | 31 | Volumes from terminated batch jobs were kept by a retention policy meant for databases. | Batch | Policy corrected |
| GPU reservations unused | us-east | 22 | Reserved for a training run that slipped to next quarter. | ML Infra | Accepted |
| Log retention at 90 days | all | 18 | The default changed from 30 to 90 days in a library upgrade. | Observability | Reverting |

The first two rows are one-off and account for 247 of the 270 thousand dollars. Without them the quarter would have closed within 0.5% of plan.

# Service detail

Table: Monthly spend by service, in thousands of dollars.

| Service | Region | July | August | September | Quarter |
|---|---|---:|---:|---:|---:|
| api-gateway | us-east | 88 | 91 | 90 | 269 |
| api-gateway | us-west | 52 | 51 | 53 | 156 |
| api-gateway | eu-west | 47 | 49 | 50 | 146 |
| catalog | us-east | 131 | 129 | 134 | 394 |
| catalog | eu-west | 72 | 74 | 75 | 221 |
| catalog | ap-south | 31 | 44 | 58 | 133 |
| checkout | us-east | 96 | 97 | 99 | 292 |
| checkout | us-west | 61 | 60 | 62 | 183 |
| checkout | eu-central | 44 | 45 | 45 | 134 |
| search | us-east | 142 | 140 | 139 | 421 |
| search | us-west | 83 | 82 | 84 | 249 |
| search | eu-west | 66 | 68 | 67 | 201 |
| search | ap-northeast | 39 | 38 | 37 | 114 |
| analytics | eu-west | 148 | 197 | 201 | 546 |
| analytics | us-east | 77 | 78 | 81 | 236 |
| batch | us-west | 58 | 55 | 57 | 170 |
| batch | eu-west | 19 | 21 | 20 | 60 |
| ml-training | us-east | 64 | 66 | 70 | 200 |
| ml-inference | us-east | 48 | 49 | 52 | 149 |
| ml-inference | ap-northeast | 37 | 38 | 40 | 115 |
| observability | us-east | 41 | 44 | 48 | 133 |
| observability | eu-central | 28 | 29 | 31 | 88 |
| storage-replica | ap-south | 62 | 91 | 126 | 279 |
| storage-replica | eu-central | 69 | 70 | 70 | 209 |
| identity | us-east | 22 | 22 | 23 | 67 |
| identity | eu-central | 18 | 18 | 19 | 55 |
| Total |  | 1,543 | 1,606 | 1,671 | 4,820 |

# Actions

| Action | Owner | Due |
|---|---|---|
| Add a scale-down step to the migration runbook | Data Platform | 21 November |
| Return eu-west reads to the eu-west replica | Storage | 28 November |
| Restore 30-day log retention | Observability | 5 December |
