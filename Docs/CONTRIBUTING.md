# Contributing to Ziya

Thanks for your interest. A note before getting into mechanics: Ziya is closer to a personal research vehicle that turned out to be a useful tool than it is to a community open-source project with a roadmap and triage rotation. That shapes what kind of contribution is most useful — see the philosophy doc's [What I'd Do Differently](DesignPhilosophy.md#what-id-do-differently) section for the honest framing on that. The short version is: the project benefits more from people willing to dig into specific design questions and try things than from filing tickets and waiting for direction.

## Getting Started

1. Fork the repository
2. Clone your fork and install dependencies:
   ```bash
   git clone https://github.com/<your-username>/ziya.git
   cd ziya
   python ziya_build.py
   ```
3. Create a branch for your change: `git checkout -b my-change`
4. Make your changes
5. Run the tests:
   ```bash
   poetry run pytest
   python tests/run_diff_tests.pl --multi
   ```
6. Submit a pull request

## Development Setup

See [DEVELOPMENT.md](DEVELOPMENT.md) for detailed build and test instructions.

## What to Work On

The most useful contributions tend to fall into three categories:

**Concrete fixes the project obviously needs.** Bug fixes (always with a test case), edge cases in the diff application pipeline, visualization preprocessing for syntax patterns that fail to render, support for additional model providers, documentation that fills a gap. These are the ones I can review and merge on a normal cadence.

**Frontend legibility.** This is the area I care about most and have invested in least, and the philosophy doc admits as much. If you're a designer or frontend engineer who finds something here interesting and wants to make the existing capabilities more discoverable — better onboarding, surfacing the visualization options without the user having to know they exist, making the multi-agent system legible from the UI — that's the kind of help that would matter most for a curious reader actually understanding what's here.

**Research-adjacent experiments.** Memory architectures across sessions. Auto-curation strategies (and how to evaluate them). New agent-coordination primitives. Different decompositions of the orchestrator. Alternative interaction paradigms for context curation. The plumbing for these is in place — the provider abstraction is thin, the orchestrator is documented, adding a new memory backend or coordination primitive is a tractable amount of code rather than its own research project. I'm very happy to talk about what I've tried, what didn't work, and where the open questions are. The thing I'd most like is people running their own experiments here who want to compare notes.

## Guidelines

- Keep PRs focused. One logical change per PR.
- Add tests for new behavior. The diff regression suite (`tests/run_diff_tests.pl`) covers patch application; `pytest` covers everything else.
- Match the existing code style. The project uses Python type hints and Pydantic models.
- Update `Docs/FeatureInventory.md` if you add or change a user-visible feature.

## Documentation Conventions

A changelog entry or doc paragraph for a new capability must name the **user-facing vocabulary** a reader would actually search for — not only the class or module name that implements it. If a competitor product has an established term for the same thing, use that term too (as an "Also called:" alias or inline), so a reader coming from that vocabulary can find the capability here. "Implemented `TreeSitterParser`" tells a contributor something; "multi-language code intelligence (tree-sitter, 25+ languages)" is what a reader searching the docs is looking for.

`tests/test_docs_ledger_conformance.py` enforces the reader-facing half of this mechanically: for every capability in the internal capability ledger at or above a "mature" maturity level, the id, its name, or at least one alias must appear (case-insensitively) somewhere under `Docs/` or in `README.md`. A capability that ships but is undiscoverable in the docs fails that test. If you add a mature capability without updating the docs, expect this test to name it.

## Reporting Issues

Use [GitHub Issues](https://github.com/ziya-ai/ziya/issues). Include:
- Ziya version (`ziya --version`)
- Model and endpoint being used
- Steps to reproduce
- Expected vs actual behavior

## Security

See [SECURITY.md](SECURITY.md) for reporting security vulnerabilities.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
