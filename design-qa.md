# Design QA — Markdown, localisation, timeline and Runs layout

## Evidence

- Source visual truth:
  - `/var/folders/9d/w3hn493s4hg299lxcxq81yc00000gn/T/TemporaryItems/NSIRD_screencaptureui_u1mNuV/Снимок экрана — 2026-08-24 в 4.47.51 PM.png`
  - `/var/folders/9d/w3hn493s4hg299lxcxq81yc00000gn/T/TemporaryItems/NSIRD_screencaptureui_ICLgYp/Снимок экрана — 2026-08-24 в 4.48.58 PM.png`
- Browser-rendered implementation:
  - `/private/tmp/cft-ui-chat-fixed.png`
  - `/private/tmp/cft-ui-runs-fixed.png`
- Side-by-side comparison:
  - `/private/tmp/cft-ui-chat-comparison.png`
  - `/private/tmp/cft-ui-runs-comparison.png`
- Viewport: 1600 × 900 CSS px, device scale factor 1.
- Source pixels: chat 1656 × 1074; Runs 2928 × 1642.
- Implementation pixels: 1600 × 900 for both captures.
- Normalisation: both images in each comparison were proportionally scaled to a maximum width of 1200 px and placed side by side without cropping.
- State: completed SberLab run with Gate WARN, 2 confirmed and 6 inconclusive findings.

## Comparison history

### Pass 1 — issues found

- P1: assistant Markdown markers were rendered literally instead of producing strong text, lists and inline code.
- P1: the final assistant summary appeared before deterministic result cards, making the completed run look like it continued after the answer.
- P1: the Runs page mixed Russian and English labels and finding descriptions.
- P1: long finding, tool and Evidence rows could expand past the available Runs content width.

### Fixes applied

- Added safe semantic Markdown rendering with `react-markdown` and dedicated typography styles.
- Forced all finding cards before Gate and the final assistant summary after Gate.
- Localised navigation, controls, run states, Gate labels, finding states, severities, known finding titles, tools and Evidence labels.
- Added min/max-width and overflow constraints to report cards, summaries and the Runs page.

### Pass 2 — post-fix evidence

- Markdown DOM contains semantic `strong`, `ul`/`ol`, `li` and `code` elements; no visible `**` markers remain.
- The final ten timeline entries end with all finding cards, then Gate, then the assistant summary. Later user follow-ups remain after that summary as expected.
- At 1600 px viewport: body and document scroll width are both 1600 px; the Runs page client and scroll width are both 1328 px, so there is no horizontal overflow.
- Browser console: no errors.
- Primary interactions checked: chat navigation, full run navigation, collapsible report presentation and responsive wide layout.

## Required fidelity surfaces

- Fonts and typography: existing product font stack preserved; Markdown now has visible hierarchy, lists and inline-code treatment without changing the base density.
- Spacing and layout rhythm: existing compact rhythm preserved; report width increased to 1100 px and long rows remain contained.
- Colors and tokens: existing dark theme and semantic green/yellow/red/blue tokens preserved.
- Image and icon quality: existing Phosphor icon system preserved; no new raster or substitute icons introduced.
- Copy and content: user-facing shell and current known security findings are Russian; technology names, file paths, identifiers and code terms remain unchanged where translation would be misleading.

## Residual P3

- Historical assistant messages retain a few English domain words that were already stored in the database. New summaries are prompted to use Russian terminology.
- Expanded historical deterministic report prose remains English because it is persisted report data; the collapsed primary Runs surface is localised.

## Final result

final result: passed
