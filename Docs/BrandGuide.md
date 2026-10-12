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

## Consent & Approval Panels

The in-chat prompt a turn parks on while a tool call awaits a human
decision (`ConsentPanel`, `consent-preflight.css`). Decided 2026-10-09
after comparing amber (inherited from the task-card Ask panel), neutral,
blue and teal variants.

**Structure.** A header band carries the `?` icon and the one-line
question ("Allow git push?"); the body holds a one-sentence summary of the
plan, a labelled detail grid (humanized keys, SHAs highlighted, booleans as
words, lists as lists), a raw-JSON toggle, and a footer split into an
Approve group (button + scope) on the left and a Reject group (reason +
button) on the right. The band is what makes a pending consent findable at
the bottom of a long scroll.

**Palette.** Teal, both themes — the same `#2dd4bf` / `#0d9488` pair as the
logomark, so the panel reads as Ziya asking rather than as content.

| Element | Dark | Light |
|---|---|---|
| Panel border | `rgba(45,212,191,.35)` | `#99e6dc` |
| Panel body | `rgba(255,255,255,.03)` | `#ffffff` |
| Header band | `rgba(45,212,191,.14)` | `#d9f5f0` |
| `?` icon, commit SHA | `#2dd4bf` | `#0d9488` |
| Detail grid | `rgba(0,0,0,.18)` | `#f6fcfb`, border `#d5f3ee` |
| Inline code | `rgba(255,255,255,.07)` | `rgba(13,148,136,.1)` |
| Footer rule | `rgba(45,212,191,.25)` | `#d5f3ee` |

**Not teal.** Hazard markers keep orange: the "outside the write policy"
alert and a checkout `warning` block carry a `3px solid #d48806` left rule.
Approve stays antd primary blue and Reject antd danger red so the buttons
do not compete with the panel chrome.

**Rejected.** Amber (`rgba(210,153,34,…)` / `#d29922`) is the task-card
Ask panel's *awaiting_input* status colour and read as brown here; a
consent prompt is not a paused run, so it must not borrow that palette.
Blue (`#1677ff` tint) was acceptable in dark but duplicated the Approve
button's hue; a neutral card was too quiet to notice.

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
