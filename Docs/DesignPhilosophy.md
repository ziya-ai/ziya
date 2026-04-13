# Design Philosophy

This document explains why Ziya is built the way it is — the reasoning behind
the architectural choices, not just the choices themselves.

---

## Origin

Ziya started because I wanted a frontend for AWS Bedrock and was constantly
exposed to token pressure from tools that didn't deal with it well. They either
cut off when things were getting productive, or felt abusive to the user's
judgment by compacting indiscriminately and losing work in the process.

Tools are often better now, but not entirely. And I still object to philosophies
like recency-weighted auto-compaction, because we know that the first few
messages in a conversation set the tone and objective for everything that
follows. That's hard to justify.

---

## The User Controls Context, Not the Machine

For interactive sessions, the user is aware of the context that matters and can
make reasonable conclusions quickly. Ziya provides mute, fork, truncate, and
selective file removal — not automatic summarization.

I've also flown in the face of the idea that sessions should be kept to as few
turns as possible. I've worked around that by making extremely long-lived
sessions of 30+ turns my operating norm, and I don't feel the model degradation
that everyone talks about. My conversations tend to split naturally when my
mental path forks.

The argument against recency-weighted compaction is specific: the first messages
in a conversation establish the objective, the constraints, the vocabulary. A
compaction scheme that weights recent messages higher will eventually discard the
messages that tell the model *what we're trying to do*. The user knows which
messages still matter. The machine doesn't.

---

## Security Is Not Optional — It's the Price of Admission

Ziya is built primarily for enterprise users in environments that have strict
controls about how information is handled and awareness of risks. Ideas like
encryption at rest are just sensible to win over enterprise users.

In my case, I was operating as an underdog — a mostly subversive project inside
a company that makes sort-of-competing products. I needed to answer any
criticism about the risks of security- and privacy-conscious enterprise users
adopting this tool. That's why it has things like pluggable expiration policies,
data retention controls, and encryption providers.

Also: I was frustrated by tools that interrupted constantly for approval of
obviously safe operations — and then destroyed my work by doing an irresponsible
git operation without asking. That's built in. Safe
operations are trusted from the start. Less safe ones can be approved globally,
or on a project, discussion, or file basis, with reasonable inheritance models.

### Hallucinated Tool Results Are Dishonest

Hallucinated tool results are corrosive to extended workflows. As I often have dozens of turns in my exchanges, the risk of having a
poisoned well gets higher, so I do everything I can to stop this from happening:

- **HMAC signing** of every tool result with per-session secrets — unsigned
  results are rejected before the model sees them
- **Fabrication detection** in the text stream — regex patterns catch the model
  narrating tool execution instead of calling the API
- **Corrective feedback injection** — when fabrication is detected, the
  conversation is truncated and a reinforcement message is injected
- **Three-strike limit** — after three hallucination retries, the stream ends
  with an explicit warning rather than letting garbage accumulate

Over 30+ turn sessions, a single hallucinated tool result can compound. The
model treats its own fabricated output as ground truth and builds on it. By
iteration 20, you're debugging phantom problems. The signing and detection
layers exist to prevent that poisoned-well failure mode.

---

## Visual Thinking Is Not a Nice-to-Have

I work visually. Honestly, timing diagrams have been the most valuable thing —
they are tedious to implement by hand. But I found that the more structured
visualization capability I give the model, with encouragement to explain things
visually when it makes sense and to do so inline in conversations, the more
opportunity it takes to use directed graphics that make a really big difference
in the moment.

This is especially helpful during high-pressure operational debugging sessions.
Other forms of graphics are really helpful when trying to synthesize hundreds of
inputs into system architectures.

Seven renderers (Mermaid, Graphviz, Vega-Lite, DrawIO, KaTeX, HTML mockups,
packet diagrams) sounds like scope creep. It isn't. Each covers a different
class of visual explanation that the model naturally reaches for when the
capability exists. The preprocessing normalization layers exist because LLM
output is imperfect — but imperfect diagrams that render are more useful than
perfect specifications that don't.

---

## Deliberate Isolation from Competitors

I tried to never look at what anyone else was doing, because I didn't want to be
tainted by their interface or architectural decisions. That's not blindness to
standards and learnings — it's a belief that if I want to build something whose
experience matches my workflow and thought patterns, I would be unlikely to find
that in the few clusters of interfaces being built around IDE integration or
wizard-driven planning.

I don't want to necessarily make things too easy for users. I want to provide the
right level of interface that gives them the power-multiplicative potential of the
tooling, not something that dumbs them down while trying to do that. That is a
difficult balance.

Eventually I said: nobody knew or cared that I was building this thing while they
flocked to Claude Code or Cline or Codex. At some point I needed to understand
why people were using those tools, because if I was going to win users, I needed
to either have all the major green checkmarks or have a better way and be ready
to be specific about that.

That's why the competitive analysis exists and why it opens with "The
Uncomfortable Summary" listing everything we're missing. It was written for me,
to force an honest accounting.

---

## Thin Providers, Thick Orchestrator

The provider abstraction (`app/providers/base.py`) defines frozen dataclass
stream events and an abstract `LLMProvider` interface. Each backend (Bedrock,
Anthropic, OpenAI, Google) is thin — 300-600 lines owning only client init,
request body construction, stream parsing, and message formatting.

The orchestrator (`StreamingToolExecutor.stream_with_tools()`) is thick. It owns
every cross-cutting concern: throttle coordination, hallucination detection,
feedback integration, cache health monitoring, code block continuation, adaptive
inter-tool delay.

This is intentional. Cross-cutting concerns don't decompose cleanly into
middleware chains or event buses — they need shared mutable state and conditional
logic that depends on the *combination* of what's happening. A middleware
architecture would scatter this logic across a dozen files and make the
interaction effects invisible.

---

## On Agentic Tools

Whether autonomous multi-tool loops are useful depends heavily, right now, on the
skill of the user to effectively structure direction and prompting. There are
genuinely tedious tasks with repetition that benefit from loops and long-running
sessions. But without meaningful guidance on structuring prompts and direction,
the results are often poor — powerful tools don't help if you can't aim them.

My current experience — which I expect will change rapidly — is that I can't
trust a model to keep on task in a way that creates what I want for too long.
But this is definitely changing, and I regularly have models perform extended
lower-risk activities for me now, like multi-round research or test building.

I think it's very important for users to understand the appropriate places for
using agents to perform transforms multiple times versus using them to build
structured code that performs the function. You have to hold the reins at exactly
the right distance.

---

## Refactor Incrementally, Never Rewrite

The orchestrator was monolithic for a long time. That was intentional — it worked,
and I had other things that were more important. I decided to clean it up when I
wanted to make the code generally available and encourage others to look at it.
At that point, unclear code is a liability.

The protocol is always the same:

1. Write tests for the current behavior
2. Extract a module with its own tests
3. Replace the inline code with a call to the extracted module
4. Verify all tests still pass

`server.py` went from 7,177 lines to 2,879 (−60%). The orchestrator went from
3,935 to 3,312 with four extracted modules. 74 tests were added during the
extraction. No behavior changed.

The remaining bulk in the orchestrator is documented in `REFACTORING_HANDOFF.md`
with exact line counts and extraction candidates. It will be finished, but it
ships working at every intermediate step.

---

## Who This Is For

Primarily, senior technical ICs dealing with very complex distributed
multi-domain problems who want the right interface. It's great for others too,
all the way to beginners, but the differentiation starts to show when things go
well beyond "fix this function," "explain this thing," or chat interfaces.

The target user is someone who moves between code, architecture diagrams,
operational analysis, and visual debugging across many parallel workstreams —
and finds that no existing AI tool covers that full surface.

---

## What I'd Do Differently

Honestly, I'm really happy with where this is. My biggest challenges have been
in telling others about it and coming up with effective pitches — not in the
technical decisions.
