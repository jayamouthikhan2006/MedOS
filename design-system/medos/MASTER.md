# MedOS design system (master)

Generated with UI/UX Pro Max (`hospital emergency queue dashboard healthcare staff`, density 7, variance 3, motion 3),
then adapted for a staff dashboard (the tool's landing-page pattern does not apply).

## Style
Minimalism & Swiss style, "Accessible & Ethical": clean, functional, high contrast, grid-based.
One primary action per screen. Every status uses colour **plus** an icon **plus** a word.

## Colour tokens (light is the default; dark is a designed pair, not an inversion)
| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | #F4F7F9 | #0B1120 | app background |
| `--surface` | #FFFFFF | #111827 | cards, sidebar |
| `--surface-2` | #F8FAFC | #0F172A | table heads, insets |
| `--border` | #E2E8F0 | #1F2A3C | dividers |
| `--text` | #0F172A | #E5E7EB | body |
| `--text-2` | #334155 | #CBD5E1 | secondary |
| `--muted` | #64748B | #94A3B8 | helper text (≥4.5:1) |
| `--primary` | #0E7490 | #22D3EE | medical teal; buttons, links |
| `--success` | #15803D | #4ADE80 | done, available |
| `--danger` | #DC2626 | #F87171 | emergencies, destructive |

Triage priority (Manchester-style), always shown with icon + label + target time:
| Level | Colour | Icon | Seen |
|---|---|---|---|
| Critical | red #DC2626 | alert octagon | immediately |
| High | orange #EA580C | double chevron up | within 15 min |
| Medium | amber #D97706 | dash | within 1 hour |
| Low | green #16A34A | chevron down | within 2 hours |

## Typography
Atkinson Hyperlegible Next for everything (designed for low-vision readers). Tabular figures for numbers.
Scale: 12 · 14 · 16 (body) · 18 · 20 · 24 (page title) · 32 · 48. Weights 400 / 600 / 700.
Atkinson Hyperlegible Mono only for the system log. The TV display may use large condensed numerals.

## Layout
- App shell: 248 px sidebar (Workspace / Screens / Learn) + page header (title, one-line purpose, page actions) + content.
- Below 1024 px the sidebar becomes a drawer opened from a top bar.
- Spacing on a 4/8 px rhythm: 8 · 12 · 16 · 24 · 32 · 48. Cards radius 12 px, controls 10 px, badges full.
- Touch targets ≥ 44 px. Visible 3 px focus ring. Content never scrolls horizontally at 375 px.

## Patterns
- Lists of patients are rows with fixed columns (position, token, patient, priority, waited, expected, action).
  Details and actions live in a side panel, never in unlabeled icon buttons.
- Forms are grouped into labelled sections; optional detail (vital signs) is collapsed.
- Destructive actions ask for confirmation and use the danger colour.
- Motion: 150–200 ms state transitions only; respect `prefers-reduced-motion`.
