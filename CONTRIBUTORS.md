# Contributors

Ziya was started in late 2023 by [Vishnu Kool](https://github.com/vishnukool) to put a usable frontend on AWS Bedrock and address some specific frustrations with the AI tools available at the time. Vishnu shaped the early architecture and core ideas of the project. Roughly a year and a half ago, [Daniel Cohn](https://github.com/dcohn1) took over active development, and that's where things have stayed since.

Beyond the original authors, the project has benefited from feedback, bug reports, and design conversations with engineers at Amazon and elsewhere who use Ziya as part of their daily work. A great deal of useful feedback has come through the internal `#ziya-dev` and `#ziya-interest` Slack channels at Amazon, and the shape of features like Task Cards, the multi-agent swarm system, and the memory architecture all owe a lot to those conversations.

## Contributing

Contributions are welcome — bug reports, design discussion, code, documentation, or new visualization plugins. The project is intentionally structured to make experimentation cheap (see the [Design Philosophy](Docs/DesignPhilosophy.md) for the reasoning), so adding a new block type, renderer, memory layer, or top-level operating paradigm rarely requires retrofitting much existing code.

If you're considering a substantive change, opening an issue first to talk through the approach tends to save time on both sides. For smaller fixes — typos, broken edge cases, validator regressions — a PR is fine without preamble.

If you build something on top of Ziya as a platform for your own experiments, I'd love to hear about it even if you don't end up upstreaming the work.

## Acknowledgements

Ziya stands on a lot of open-source work — FastAPI, React, MUI, prompt-toolkit, watchdog, the LangChain ecosystem in spots, Playwright for headless rendering, every parser and renderer it embeds (Mermaid, Graphviz, Vega-Lite, KaTeX, drawio), and the Python diff-application heritage that informs the patch pipeline. Thanks to all of them.
