# README & Public Collateral Rewrite Plan

## Problem Statement

The current GitHub landing page and PyPI listing actively repel potential users:

- **README.md** (169 lines): Reads like `--help` output. No visuals, no tagline, no value proposition, no differentiation. Feature list is generic bullets that match 30 competitors.
- **pyproject.toml**: `description = ""` — literally empty. No keywords, classifiers, project URLs, or readme reference. PyPI page shows nothing.
- **Zero images** in the entire repository. A product whose #1 differentiator is rich visual rendering has no visual evidence of its existence.
- **No community files**: No CONTRIBUTING.md, CHANGELOG.md, SECURITY.md, CODE_OF_CONDUCT.md, .github/ISSUE_TEMPLATE/.
- **No badges**: No stars, version, license, Python version, or download indicators.

### Competitive Comparison

| Element | Aider (41k ⭐) | Open WebUI (80k ⭐) | Ziya (current) |
|---|---|---|---|
| Time to understand value | ~5 sec | ~5 sec | Never |
| Hero visual | Animated SVG screencast | Banner + demo image | None |
| Tagline | "AI Pair Programming in Your Terminal" | "self-hosted AI platform" | None |
| Badges | Stars, 5.7M installs, tokens/week | Stars, forks, watchers, Discord | Zero |
| Images in repo | Multiple SVG/PNG | banner.png, demo.png | Zero |

## Shot List (Screenshots & GIFs to Capture)

All assets go in `docs/images/`. Recommended capture tool: [Kap](https://getkap.co/) for GIFs on macOS, or OBS → ffmpeg for MP4→GIF.

### Must-Have (Above the Fold)

| # | Filename | What to show | Duration/Size | Priority |
|---|---|---|---|---|
| 1 | `hero.gif` | Complete workflow: ask about code → response with Graphviz diagram + diff → click Apply → green checkmarks | 15-20 sec, <5MB | **P0** |
| 2 | `hero-screenshot.png` | Full UI: file tree, conversation with diagram + diff, dark mode | Static, ~1200px wide | **P0** |
| 3 | `logo.png` | ℤiya wordmark per BrandGuide.md specs | 300px wide, transparent bg | **P0** |

### Feature Section Images

| # | Filename | What to show | Priority |
|---|---|---|---|
| 4 | `diff-apply.gif` | Diff appears → Apply click → hunks turn green | **P1** |
| 5 | `ops-analysis.png` | Graphviz architecture/deadlock diagram from pasted data | **P1** |
| 6 | `vega-chart.png` | Vega-Lite chart rendered inline | **P1** |
| 7 | `packet-diagram.png` | Rendered packet/protocol frame diagram | **P2** |
| 8 | `multi-project.png` | Project switcher with 2-3 projects | **P2** |
| 9 | `mcp-tools.png` | MCP tool results in conversation | **P2** |
| 10 | `drawio-diagram.png` | DrawIO architecture diagram rendered inline | **P2** |
| 11 | `skills-panel.png` | Skills/Contexts panel with active indicators | **P2** |

### GIF Recording Tips

- **Resolution**: Record at 1280×800 or 1440×900 (not full retina — keeps GIF size reasonable)
- **Dark mode**: Almost all successful dev tool READMEs use dark mode screenshots
- **Clean state**: Use a project with recognizable but non-proprietary code
- **Crop**: Remove browser chrome — just the Ziya UI
- **Compress**: Use `gifsicle -O3 --lossy=80` to keep GIFs under 5MB for GitHub

## New README.md Structure

```
1. Hero Section
   - Centered logo (logo.png)
   - Tagline: one line, category-defining
   - Badge row: PyPI version, Python version, License, Stars
   - Hero GIF (hero.gif) or screenshot (hero-screenshot.png)

2. What is Ziya? (3-4 sentences)
   - NOT an IDE. NOT a plugin. NOT a CLI-only tool.
   - Self-hosted AI technical workbench
   - Works alongside your existing editor
   - Enterprise-proven (deployed at major tech companies via plugin system)

3. What Makes This Different (visual feature grid)
   - Each feature: emoji/icon + bold headline + one sentence + screenshot
   - Rendered Diffs with Apply/Undo (diff-apply.gif)
   - Architecture & Ops Analysis (ops-analysis.png)
   - Rich Visualizations (Graphviz, Mermaid, Vega-Lite, DrawIO, Packet diagrams)
   - Project-Scoped Everything (multi-project.png)
   - MCP Tool Integration
   - Skills System
   - Swarm Delegation
   - Enterprise Plugin Architecture

4. Quick Start (< 10 lines)
   pip install ziya
   ziya
   → screenshot of what you see

5. Supported Models
   Table: provider, models, what you need

6. Documentation Links
   Pointer to Docs/ for deep dives

7. Enterprise
   One paragraph + link to Enterprise.md

8. Contributing
   Pointer to CONTRIBUTING.md

9. License
```

## pyproject.toml Metadata Fixes

```toml
[tool.poetry]
name = "ziya"
version = "0.6.1.0"
description = "Self-hosted AI technical workbench — not an IDE, not a plugin. Architecture analysis, operational diagnostics, code editing with rich visualizations. Works alongside your existing tools."
authors = [...]
license = "MIT"
readme = "README.md"
homepage = "https://github.com/ziya-ai/ziya"
repository = "https://github.com/ziya-ai/ziya"
keywords = ["ai", "llm", "coding", "development", "architecture", "operations", "visualization", "self-hosted"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "Topic :: Software Development :: Libraries :: Application Frameworks",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
]
```

## Additional Files Needed

| File | Purpose | Status |
|---|---|---|
| CONTRIBUTING.md | Invite contributions, explain process | To create |
| CHANGELOG.md | Show active development, recent features | To create |
| SECURITY.md | Security reporting (especially important for enterprise story) | To create |
| .github/ISSUE_TEMPLATE/bug_report.md | Structured bug reports | To create |
| .github/ISSUE_TEMPLATE/feature_request.md | Feature requests | To create |

## Execution Order

1. Capture P0 images (hero.gif, hero-screenshot.png, logo.png)
2. Apply pyproject.toml metadata fixes
3. Replace README.md with new version (image placeholders until captures ready)
4. Create CONTRIBUTING.md, CHANGELOG.md, SECURITY.md
5. Create .github/ISSUE_TEMPLATE/ files
6. Capture P1 images and uncomment in README
7. Push all at once as a single "README & project metadata overhaul" commit
