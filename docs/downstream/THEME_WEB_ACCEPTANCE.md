# Theme archive browser acceptance — 2026-11-17

[简体中文](THEME_WEB_ACCEPTANCE_CN.md) · [Usage](WEB_WORKSPACE.md) · [Source acceptance](THEMES_ACCEPTANCE.md) · [Backlog](backlog.json)

## Delivered

The local `http://127.0.0.1:5177/#/ashare-paper` page now includes a read-only **Theme members** tab. Select an archived board to view members, collection time, source and manifest hash, provider total, supported code-scope count and exclusions, in pages of 100. Refresh reads existing archives only: no provider fetch, research preference/watchlist change, daily review, strategy approval or order submission occurs.

Actual browser acceptance viewed **人工智能 / BK0800**, received at 22:08:51: 730 supported members, 758 provider members, 28 exclusions. Page 2 displayed 101–200; refresh returned 1–100. The dark-mode board-heading color was corrected and rebuilt. Missing archives are explicitly unobserved, not an empty real universe.

Human-JWT read APIs are `/api/ashare-paper/themes` and `/themes/{board_code}`. The list includes at most 20 latest distinct boards, revalidating manifests, raw pages and every database member before return; any corruption rejects the whole list. Viewers may read; Agent tokens are denied. Sessions are read-only and repeatable-read. Invalid, duplicate and unknown query fields are rejected while official `_t` compatibility is retained. Local evidence paths are omitted, evidence errors use a fixed safe 503 response, and the contract is version 0.5.0.

## Checks this round

| Check | Result |
| --- | --- |
| Python 3.11 / 3.12 full A-share suite | 307 passed each with isolated PostgreSQL |
| Focused theme/API group, included above | 89 passed each; authentication, real read-only SQL, corrupt evidence, paging and route contract |
| Frontend Node state tests | 19 passed, including 9 theme response-order/cleanup/paging/disposal cases |
| Official Vue production build and ESLint | Passed; existing upstream large-chunk warning retained |
| Deployed HTTP acceptance | 20 passed; 730 unique members across two pages, anonymous/Agent and invalid-query rejection, release HTML match |
| Data and settings | Nine account/fill/lot/session/audit/theme table fingerprints and model-settings hash unchanged |
| Browser | Tab, actual members/provenance, next page, refresh and final dark-mode rendering verified |

Four existing pandas/calendar deprecation warnings remain. A private pre-deployment backup is retained as `pre-theme-web-20261117`. No new schema migration was needed; the previous round's isolated restore is not claimed as newly executed here. No model call, extra Feishu test, new theme acquisition or order was performed. Windows, remote CI and future natural days were not verified.

## Remaining work

This is a provider current-membership observation, not realtime prices, certified historical constituents or trading authorization. Robotics and new energy are not archived. Preference mapping, periodic themes, original news provenance, automatic research/recommendation and approved-strategy automatic paper execution remain open.

Service health and the known historical-sync failure are reported separately. November 16 BaoStock data is still missing and its last sync task remains failed; passing page acceptance does not complete every system requirement. Live execution and automatic AI promotion remain prohibited.

Local evidence is under the workspace parent's `work/quantdinger-theme-web-acceptance/`: JUnit, frontend tests/build logs, HTTP proof and `theme-panel.png`. Source, frontend assets and deployment are checked separately. Private settings, credentials and runtime data are not committed.
