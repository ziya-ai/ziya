# Ziya Brand & Logo Guide

## Logo Construction

The Ziya wordmark consists of two elements:

### ℤ — The Logomark

- **Character:** ℤ (U+2124, DOUBLE-STRUCK CAPITAL Z) — the same character used as the CLI prompt
- **Font:** Georgia, "Times New Roman", serif
- **Weight:** 800 (extra bold)
- **Size:** 1.3em relative to the h2 container
- **Letter-spacing:** -0.02em (tight)
- **Color:** Berry-to-teal gradient
  - Dark mode: `#b32ca8` → `#2dd4bf`
  - Light mode: `#b32ca8` → `#0d9488`
- **Glow:** drop-shadow, stronger in dark mode
  - Dark: `drop-shadow(0 0 12px rgba(179, 44, 168, 0.4))`
  - Light: `drop-shadow(0 0 8px rgba(179, 44, 168, 0.2))`

### iya — The Wordmark Suffix

- **Font:** Avenir Next, Avenir, Montserrat, Trebuchet MS, sans-serif
- **Weight:** 700 (bold)
- **Size:** 0.92em relative to the h2 container
- **Letter-spacing:** 0.10em (wide tracking)
- **Gap from ℤ:** 4px (margin-left)
- **Vertical offset:** 1px below ℤ baseline (`position: relative; top: 1px`)
- **Color:** Berry-to-teal gradient (slightly darker start than ℤ)
  - Dark mode: `#8b3aaa` → `#2dd4bf`
  - Light mode: `#8b2a9a` → `#0d9488`

## Color Palette

### Primary Colors

| Name | Hex | Usage |
|------|-----|-------|
| Berry | `#b32ca8` | Gradient start (ℤ), brand primary |
| Teal (dark) | `#2dd4bf` | Gradient end on dark backgrounds |
| Teal (light) | `#0d9488` | Gradient end on light backgrounds |
| Berry-dark | `#8b3aaa` / `#8b2a9a` | Gradient start for "iya" text |

### Header Backgrounds

| Mode | Value | Description |
|------|-------|-------------|
| Dark | `linear-gradient(90deg, #18121b 0%, #0d2220 100%)` | 8% teal-heavy berry→teal |
| Light | `#f5f3f5` | Flat warm gray |

### Color Positioning

The Ziya palette intentionally sits between:
- **T-Mobile magenta** (`#e20074`) — too pink/corporate
- **Kiro purple** (`#7b2cf5`) — too blue-violet

Berry (`#b32ca8`) occupies the red-violet middle ground, distinct from both.

## Version Badge

- **Position:** Fixed, bottom-right corner (2px bottom, 4px right)
- **Font:** 9px monospace
- **Opacity:** 16.5% (`rgba` alpha 0.165)
- **Interaction:** `pointer-events: none; user-select: none`
- **Format:** `v{version}` (e.g., `v0.5.0.4`)

## CLI Prompt

The CLI uses the same ℤ character in its interactive prompt:

```
FormattedText([('bold magenta', 'ℤ'), ('cyan', 'iya'), ('', ' '), ('bold cyan', '› ')])
```

The web UI's berry-to-teal gradient is the visual equivalent of the CLI's magenta-to-cyan color scheme.
