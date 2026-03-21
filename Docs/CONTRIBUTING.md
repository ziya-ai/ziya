# Contributing to Ziya

Thanks for your interest in contributing.

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

- **Bug fixes** — always welcome, especially with a test case
- **Visualization renderers** — improvements to Graphviz, Mermaid, Vega-Lite, DrawIO, or packet diagram normalization
- **Diff application** — edge cases in the multi-strategy patch pipeline
- **Model support** — adding or improving support for LLM providers
- **Documentation** — the `Docs/` directory is the source of truth; improvements are valuable
- **Skills** — new built-in skills or improvements to the skills system

## Guidelines

- Keep PRs focused. One logical change per PR.
- Add tests for new behavior. The diff regression suite (`tests/run_diff_tests.pl`) covers patch application; `pytest` covers everything else.
- Match the existing code style. The project uses Python type hints and Pydantic models.
- Update `Docs/FeatureInventory.md` if you add or change a user-visible feature.

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
