# Design QA

## Comparison target

- Source visual truth: `/Users/stavrmoris/.codex/attachments/4d2bfb98-cde5-44fc-bb08-486ecac5bab1/pasted-text.txt`, section `ОСНОВНАЯ КОНЦЕПЦИЯ НОВОГО UI` and its supplied ASCII workflow mock.
- Source capture: `output/playwright/source-mock-desktop.png`.
- Rendered implementation: `http://127.0.0.1:5176/chats/chat-d814a7a3b2fc4c5695d32feacc46fe0b`.
- Implementation capture: `output/playwright/conversation-desktop-vite8.png` (post dependency-upgrade regression); the normalized comparison uses the visually identical `output/playwright/conversation-desktop-final.png` capture.
- Combined comparison: `output/playwright/design-qa-comparison.png`.
- Desktop viewport: 1200 x 800 CSS px, device scale factor 1. Source and implementation captures are both 1200 x 800 pixels; no density normalization was required.
- Mobile viewport: 390 x 844 CSS px, device scale factor 1. Capture: `output/playwright/conversation-mobile.png`.
- State: imported project with two chronological runs, completed SAST/sandbox verification, tool call, Evidence, finding, Gate WARN, and a grounded follow-up answer.

## Full-view comparison evidence

The combined comparison confirms the intended two-column desktop structure: persistent projects/chats sidebar, compact project header, conversation as the dominant surface, and a bottom composer. The implementation preserves the source topology while replacing the schematic boxes with compact, expandable run events. It does not introduce dashboard charts, marketing cards, quick-reply chips, or a second report area below the chat.

The mobile capture confirms that the sidebar leaves the chat canvas and reappears as a dismissible overlay. Persistent header and composer controls remain visible without horizontal overflow.

## Focused region comparison evidence

- `output/playwright/tool-expanded-final.png`: command, purpose, stderr, exit code, duration, capability, target, environment, working directory, sandbox session, and action id are readable in one expandable block.
- `output/playwright/evidence-expanded-final.png`: Evidence is visually distinct from agent interpretation and exposes source, observation, reliability, action, scope, and raw facts.
- `output/playwright/sidebar-mobile.png`: the responsive sidebar overlays rather than compressing or destroying the conversation.

## Required fidelity surfaces

- Fonts and typography: system sans-serif for conversation/UI and system monospace for commands and metadata; weights, line height, truncation, and wrapping remain legible at both tested viewports.
- Spacing and layout rhythm: 272 px desktop sidebar, narrow header, centered 820 px conversation/composer, compact event rhythm, restrained 7-12 px radii, and no oversized cards.
- Colors and tokens: neutral near-black surfaces with subtle borders; blue is reserved for live/tool state, green for success/reliability, yellow for uncertainty/WARN, and red for technical/security failure.
- Image and asset quality: no raster decoration is required by the source. Interface icons use the Phosphor library; there are no emoji, handcrafted SVGs, CSS illustrations, or placeholder imagery.
- Copy and content: labels describe real project/run state. User and agent messages remain conversational; Evidence, finding status, technical failure, and Gate copy are explicitly separated.

## Findings

No actionable P0, P1, or P2 differences remain.

- [P3] Long scanner titles are necessarily truncated in collapsed rows.
  Evidence: the Docker rule description exceeds a single compact row at desktop and mobile widths.
  Classification: acceptable because the complete text is available after expansion and compact density is an explicit source requirement.

## Comparison history

### Iteration 1

- [P2] Desktop-only chrome showed the mobile sidebar open/close controls because `.icon-button` overrode their hidden display rule.
- Fix: moved the explicit `.sidebar-close, .mobile-menu { display: none; }` rule after the shared icon-button declaration while retaining the mobile media override.
- Post-fix evidence: `output/playwright/conversation-desktop-final.png` has no mobile controls; `output/playwright/conversation-mobile.png` retains the menu control; `output/playwright/sidebar-mobile.png` confirms the overlay and close action.

### Iteration 2

- Recompared source and implementation in `output/playwright/design-qa-comparison.png`.
- No actionable P0/P1/P2 mismatch remained.

## Interaction and console verification

- Tested Folder import from a real browser file chooser; only the relative filtered file tree reached the API.
- Tested automatic project/chat creation, analysis send, live SSE updates, completed-run reload restoration, reanalysis from follow-up, grounded follow-up response, tool/Evidence expansion, full-report link, and mobile sidebar.
- Application console errors: 0. React Router emitted two non-blocking v7 future-flag warnings in development mode.
- Post-upgrade Vite 8 / React Router 7 regression console errors: 0; the only console entry was the React DevTools development hint.

## Implementation checklist

- [x] Match the supplied sidebar/header/conversation/composer topology.
- [x] Keep pipeline events, tools, Evidence, findings, and Gate in one chronology.
- [x] Keep technical failures inline and visually distinct from security Gate state.
- [x] Verify desktop and mobile layouts in a real browser.
- [x] Verify expanded tool and Evidence states.

final result: passed
