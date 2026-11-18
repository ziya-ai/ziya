---
ziya-doc: 1
title: "Queue Depth at the Ingest Tier"
subtitle: "An M/M/c model of headroom between 70% and 95% utilisation"
author: "Capacity Planning"
date: 2026-11-13
layout: report
numbering:
  sections: true
page:
  margin: {top: 16mm, bottom: 18mm, left: 19mm, right: 19mm}
---

# Summary

The ingest tier behaves as an M/M/c queue with $c = 12$ workers. At the current utilisation of $\rho = 0.85$ the mean queue holds about three requests; at $\rho = 0.95$ it holds fifteen. The tier should not be run above $\rho = 0.90$ without adding workers.

# Model

## Assumptions

Arrivals are Poisson with rate $\lambda$, service times are exponential with rate $\mu$ per worker, and there are $c$ identical workers. Utilisation is

$$\rho = \frac{\lambda}{c\,\mu}.$$

The model is stable only for $\rho < 1$. Measured inter-arrival times have a coefficient of variation of 1.08, close enough to the exponential value of 1 for the model to be useful.

## Probability of waiting

The probability that an arriving request finds all workers busy is given by the Erlang C formula:

$$C(c, a) = \frac{\dfrac{a^{c}}{c!}\,\dfrac{1}{1-\rho}}{\displaystyle\sum_{k=0}^{c-1}\frac{a^{k}}{k!} + \frac{a^{c}}{c!}\,\frac{1}{1-\rho}}, \qquad a = \frac{\lambda}{\mu}.$$

## Queue length and delay

The mean number waiting and the mean wait follow directly:

$$L_q = C(c, a)\,\frac{\rho}{1-\rho}, \qquad W_q = \frac{L_q}{\lambda} = \frac{C(c, a)}{c\,\mu - \lambda}.$$

The tail of the waiting time is exponential. For a threshold $t$,

$$P(W_q > t) = C(c, a)\, e^{-(c\mu - \lambda)\,t}.$$

Setting the right side to 0.01 and solving for $t$ gives the 99th percentile wait:

$$t_{99} = \frac{\ln\bigl(100\,C(c, a)\bigr)}{c\,\mu - \lambda}.$$

# Results

Table: Model output for $c = 12$ and $\mu = 40$ requests per second per worker.

| $\rho$ | $\lambda$ (req/s) | $C(c,a)$ | $L_q$ | $W_q$ (ms) | $t_{99}$ (ms) |
|-------:|------------------:|---------:|------:|-----------:|--------------:|
| 0.70 | 336 | 0.123 | 0.29 | 0.9 | 17 |
| 0.80 | 384 | 0.305 | 1.22 | 3.2 | 36 |
| 0.85 | 408 | 0.450 | 2.55 | 6.3 | 53 |
| 0.90 | 432 | 0.640 | 5.76 | 13.3 | 87 |
| 0.95 | 456 | 0.825 | 15.7 | 34.4 | 184 |

The queue length is convex in $\rho$: the step from 0.90 to 0.95 adds more waiting requests than the whole range from 0.70 to 0.90.

```vega-lite
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "width": 520, "height": 240,
  "data": {"values": [
    {"rho": 0.70, "lq": 0.29}, {"rho": 0.75, "lq": 0.62}, {"rho": 0.80, "lq": 1.22},
    {"rho": 0.85, "lq": 2.55}, {"rho": 0.90, "lq": 5.76}, {"rho": 0.93, "lq": 9.9},
    {"rho": 0.95, "lq": 15.7}
  ]},
  "mark": {"type": "line", "point": true},
  "encoding": {
    "x": {"field": "rho", "type": "quantitative", "title": "utilisation ρ", "scale": {"domain": [0.68, 0.96]}},
    "y": {"field": "lq", "type": "quantitative", "title": "mean requests waiting"}
  }
}
```

Figure: Mean queue length against utilisation for twelve workers.

# Sensitivity

## Service time variability

If service times are not exponential, the Allen–Cunneen approximation scales the wait by the mean of the squared coefficients of variation:

$$W_q^{G} \approx W_q^{M}\,\frac{c_a^{2} + c_s^{2}}{2}.$$

With the measured $c_s = 1.4$ and $c_a = 1.08$ the factor is 1.56, so the $t_{99}$ column above is optimistic by about half.

## Adding workers

Holding $\lambda = 432$ and raising $c$ from 12 to 14 lowers $\rho$ to 0.77 and $L_q$ from 5.76 to 0.71. Two workers buy an eightfold reduction in queue length at this load.

# Recommendation

Alert at $\rho = 0.85$ sustained for ten minutes and add two workers when the weekly peak passes $\rho = 0.90$.
