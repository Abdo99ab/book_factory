# Raised Ready — Book Factory migration plan

**Branch:** `raised-ready-migration`  
**Spec:** `docs/RAISED-READY-BOOK-FACTORY-SPEC.md` (against code at `7f67f96` + later main)  
**Rule:** one commit per step; pytest under `tests/`; do **not** push until review says so.  
**Status words:** LOCKED / WORKING / OPEN / RECOMMENDED — OPEN and WORKING values live in config with the spec default and an owner comment; never hard-code a guess.

This file is **Step 0**. Stop here for review before Step 1.

---

## What we keep (do not rip out)

| Keep | Why |
|---|---|
| Job model `book_assets/jobs/<id>/` (`inputs.json`, `job.json`, `logs/events.jsonl`) | Operator audit trail |
| Event names where possible (`job_created`, `story_*`, `pages_compile_*`, `humanize_*`, `pdf_*`, …) | Logs / UI / ops continuity |
| Flow prepare → review → preview → validate & build | Spec §0 / operator UX |
| `changes.py`, `content_edit.py`, `cast_rebuild.py` (adapted) | NL + form edits |
| `self_revise.py` (extended in Step 11) | Vision QA + approval |
| `book_templates.py` (extended in Step 12) | Title reuse |

Operator flow after migration: **prepare → approve hero → review text → preview → validate & build → human check → send**.

---

## Prerequisites (outside code commits; flag if missing)

| Asset | Expected path | Notes |
|---|---|---|
| Spec | `docs/RAISED-READY-BOOK-FACTORY-SPEC.md` | Present |
| Logo masters | `assets/brand/` (full colour, one-ink, black, reverse, mini R; SVG/PNG) | Step 1 / 10 fail loudly if absent |
| Brand fonts | `assets/fonts/NotoSerif-SemiBold.ttf`, `Nunito-Regular.ttf`, `Nunito-SemiBold.ttf`, `DMMono-Medium.ttf` | Step 2 registers at import |
| Story concepts | Source for `brand/stories/<title_id>.md` | Copy from project story-concept docs; `approved: false` until signed off |
| Humanize prompt | `brand/prompts/humanize_v1.md` | From humanize-pass spec (Step 7) |

---

## Step 0 — Plan file *(this document)*

**Files**
- Create: `docs/MIGRATION-PLAN.md`
- Branch: `raised-ready-migration`

**Tests:** none.

**Commit message (suggested):** `docs: Raised Ready migration plan (Step 0).`

---

## Step 1 — Brand as data

**Files**
- Create: `pipeline/brand.py` — `COLOURS` (§6), `BAND_TEXT_COLOUR`, `FONT_FILES`, `SIZE_SCALE` (§5), `LOGO_FILES`, `READ_IT_AGAIN_LINE`, `HONESTY_LINE`, `UK_SPELLING` (§5), `BANNED_PHRASES` (§11.5), `TELLS` (§11.4), `WORDS_PER_SPREAD` + `HUMANIZE_CAPS` + `WORD_CAP_SOURCE` (OPEN, Taoufik), `ROUTE_PRESETS` (§8.1–8.2), `SAFE_MM`
- Create: `pipeline/catalogue.py` — load `brand/catalogue.json`
- Create: `brand/catalogue.json` — 10 titles (`big_kid_list` … `too_good_picture`) with required keys from prompt § Step 1
- Create: `brand/stories/<title_id>.md` × 10 (beats; `approved: false` until ready)
- Create: `brand/prompts/.gitkeep` (humanize prompt lands in Step 7)
- Edit: `pipeline/constants.py` — remove `THEMES` / `PURPOSES` / free-text `design_rules` from character prompt; add `PRINTER_PROFILES` (`tj_books`, `prodigi`) via `PRINTER_PROFILE`; derive mm/px/cover geometry; rename gender map → `pronouns` + `they`; feature flags `HUMANIZE_PASS`, `PRINTED_IN_UK_LINE`, `TEXT_PLATE`, `OUTFIT_CONSTANT`, `IMAGE_QUALITY`, `MEMORY_SCOPE`
- Edit: `requirements.txt` — add `pytest`
- Create: `tests/conftest.py` (shared fixtures stub)

**Tests**
- `tests/test_catalogue.py` — catalogue loads; every `title_id` has every required key
- `tests/test_brand.py` — colours match §6 hex list; `BAND_TEXT_COLOUR` mapping; band grammar ("Ready for" / "Ready to" / adventures = `READY FOR ADVENTURE`); `big_kid_list` `state_schedule` length == 14
- `tests/test_printer_profile.py` — default `tj_books` → bleed 4 mm → `PAGE_PX == 2575`, `SPREAD_PX_W == 5055`

**OPEN/WORKING surfaced in config comments:** bleed 3 vs 4 (Meda/TJ); `endpapers_in_pdf`; packaging mode; `WORD_CAP_SOURCE`.

---

## Step 2 — Fonts

**Files**
- Edit: `pipeline/fonts.py` — brand-only catalogue; `normalize_font_id` → `"brand"`; ReportLab `pdfmetrics.registerFont` for four TTFs; `RuntimeError` if missing; delete system fallbacks
- Edit: `templates/create.html` — remove font picker (full form rewrite is Step 3; Step 2 can strip the picker early or wait — prefer strip here if form still Magical)
- Edit: `app.py` — `rebuild_overlays` keeps route but drops font re-apply options
- Edit: `pipeline/pdf_build.py` — stop using Magical font IDs (minimal; full rewrite Step 10)
- Delete: unused Magical TTFs under `assets/fonts/` (keep brand four + `.gitkeep`)
- Ensure brand TTFs present under `assets/fonts/`

**Tests**
- `tests/test_fonts.py` — registered names include Noto Serif / Nunito / DM Mono variants; removing one TTF raises `RuntimeError` naming the file

---

## Step 3 — Input contract

**Files**
- Rewrite: `templates/create.html` — `title_id`, name, age, pronouns, carer, home_type, comfort_object, slots (JS from catalogue schema), dedication, `order_id`, photo, two §14 consent ticks (JS disable submit), `start_mode`, Advanced `story_override` / `art_notes`
- Edit: `app.py` `create_job` / `create_form` — new `inputs.json` shape; consent record + text hash/version; age-band warning; ignore legacy `theme`/`purpose`/`page_count`/`design_rules`/`font_id`; `job_created` must not log photo
- Edit: `templates/job.html` — show title/band/order_id; age warning if stored
- Edit: `pipeline/job_log.py` — early redaction if needed (full redaction Step 12)
- Create: `brand/consent_copy.json` (or constants) — exact §14 checkbox wording + `text_version`

**Tests**
- `tests/test_create_job.py` (Flask test client) — missing ticks → error, no job dir; valid post → consent on `inputs.json`; posting `theme` is ignored; `order_id` required

---

## Step 4 — Character, set, anchor sheets

**Files**
- Edit: `pipeline/character.py` — route preset + outfit + multi-view sheet; `IMAGE_QUALITY`; `status: awaiting_approval`; add `generate_set_sheets`, `generate_anchor_sheet`
- Edit: `pipeline/runner.py` — `prepare_content` stops at `character_ready`; new mode/route to continue after approval
- Edit: `app.py` — `POST .../approve-character`; gate story/pages until approved
- Edit: `templates/job.html` — "Approve hero sheet" UI; show character / sets / anchor thumbs
- Edit: `pipeline/constants.py` — drop obsolete `CHARACTER_PROMPT` free `design_rules` path (if still present)

**Tests**
- `tests/test_character_gate.py` — prepare leaves `character_ready`; continue without approval fails; approve writes `character_approved_by/at`
- Mock OpenAI: character.json status + paths written

---

## Step 5 — Story

**Files**
- Rewrite: `pipeline/story.py` — new SYSTEM; 14 spreads JSON; locked title/band/question/parent/dedication from catalogue; outfit from catalogue when `OUTFIT_CONSTANT=1`; call text guards (Step 7 — stub/import; if Step 5 lands first, wire guards after Step 7 or land Steps 5–7 in dependency order: prefer **implement Step 7 modules before finishing Step 5 commit**, or Step 5 commit with a thin guard stub replaced in Step 7)
- Edit: `pipeline/cast_rebuild.py` — align with spreads schema / no invented cast
- Edit: `pipeline/content_edit.py` — edit spreads not `rebuilt_story`
- Edit: `templates/job.html` — story editor for 14 spreads + locked fields read-only

**Tests**
- `tests/test_story.py` — title never from LLM (forced from template); locked keys present; mock LLM + guard retry path

**Note:** Commit order in the prompt is Step 5 then 6 then 7; Step 5’s “run guards” needs `text_guards.py`. Plan: land Step 7’s `text_guards.py` as part of Step 5 if needed, or accept Step 5 without guard calls until Step 7. **Preferred:** implement `text_guards.py` early inside Step 5 commit, expand tests in Step 7.

---

## Step 6 — Page map

**Files**
- Rewrite: `pipeline/pages.py` `compile_pages` — deterministic 32-page map (no LLM); `calm_zone`; `anchor_state`; write `story/pages.json` + `pages_compile_*` events; art paths → `spreads/spread-NN/`
- Edit: `pipeline/content_edit.py`, `pipeline/changes.py`, `pipeline/self_revise.py` — spread-addressed edits; `text_placement` → `calm_zone` adapter
- Edit: `templates/job.html` — page cards / spread grouping
- Edit: `pipeline/runner.py` — preview/build iterate spreads

**Tests**
- `tests/test_page_map.py` — p1 title, spread n → pages `2n`/`2n+1`, p30–31 parent, p32 dedication; 32 entries; no LLM call

---

## Step 7 — Text guards + humanize

**Files**
- Create: `pipeline/text_guards.py` — `check_spreads(...)` codes per prompt
- Rewrite: `pipeline/humanize.py` — remove Skills API + `skills/humanizer/`; `humanize_spreads` behind `HUMANIZE_PASS`; prompt from `brand/prompts/humanize_v1.md`; draft/humanized JSON files; keep `humanize_*` events; drop `humanize_page_texts`
- Delete: `skills/humanizer/` (SKILL.md, LICENSE, SOURCE.txt)
- Create: `brand/prompts/humanize_v1.md`
- Edit: `requirements.txt` — add `textstat`; drop unused skill-upload deps if any
- Edit: `.env.example` — `HUMANIZE_PASS=0`, remove blader skill vars
- Edit: `pipeline/story.py` / `pages.py` callers as needed

**Tests**
- `tests/test_text_guards.py` — fixtures: banned phrase, US spelling, changed locked block, renamed child, +30% words → correct `code`s
- `tests/test_humanize.py` — flag off skips; mock pass; guard fail → retry → `humanize_skipped`

---

## Step 8 — Validation

**Files**
- Edit: `pipeline/validate.py` — title beats + `must_not_show` in judge; structural = Step 7 guards + band/question match; scene-vs-text object consistency; `_apply_repair` never touches `locked`

**Tests**
- `tests/test_validate.py` — locked field survives repair; band mismatch fails; scene missing a named object fails

---

## Step 9 — Illustration + upscale

**Files**
- Rewrite: `pipeline/illustrate.py` — `_spread_prompt`; multi-ref edit or composite board; landscape size; save `spreads/spread-NN/art.png`; cover + `generate_title_spot`; drop Magical theme/purpose/memory unless `MEMORY_SCOPE`
- Create: `pipeline/upscale.py` — Real-ESRGAN / `UPSCALER` HTTP / LANCZOS last resort + `upscale_fallback` event; face-aware crop to 5055×2575; `effective_ppi`; `recompose_needed`; post_grade night/underwater
- Edit: `pipeline/openai_util.py` — landscape size try/fallback; `quality` from `IMAGE_QUALITY`
- Edit: `pipeline/runner.py` — generate 14 spreads not 32 pages; upscale before PDF
- Edit: `pipeline/memory.py` — respect `MEMORY_SCOPE`
- Edit: `requirements.txt` — upscaler / opencv or mediapipe as chosen (document OPEN if deferred to HTTP-only)

**Tests**
- `tests/test_illustrate_prompt.py` — prompt contains calm_zone, must_not_show, no Theme/Purpose when memory off
- `tests/test_upscale.py` — output exactly 5055×2575; ppi recorded; LANCZOS path emits `upscale_fallback` (mock)

---

## Step 10 — Text layer + PDF

**Files**
- Rewrite: `pipeline/pdf_build.py` — ReportLab vector text; soft paper plate; spread split; p1 / p30–31 / p32; `build_cover`; packaging from printer profile; assertions; file names with `order_id`
- Edit: `app.py` download routes — cover + interior or single PDF
- Edit: `templates/job.html` — cover/interior download links
- Remove cream Pillow overlay path entirely

**Tests**
- `tests/test_pdf_layout.py` — with fixture art: 32 pages; dedication p32; spread halves; no pixel `(255,250,240)` in text region samples; fonts ⊆ brand (ReportLab font names)
- Packaging mode unit test for `tj_books` vs `prodigi` page order

---

## Step 11 — QA + human check

**Files**
- Create: `pipeline/qa.py` — `structure_check`, `text_guard_report`, `anchor_state_report`, `face_position_report`
- Edit: `pipeline/self_revise.py` — reference photo in vision batches; face/anchor/artefact questions; `review_record` on `job.json`
- Edit: `templates/job.html` — Human check panel; Send preview gated; template button preconditions
- Edit: `app.py` — routes for human-check toggles / send preview
- Create: `scripts/likeness_test.py` — 3×4 per route → CSV

**Tests**
- `tests/test_qa.py` — structure_check fails on wrong page map; send preview blocked until flags pass (Flask)
- Anchor schedule fixture for `big_kid_list`

---

## Step 12 — Data protection

**Files**
- Create: `pipeline/retention.py` — purge rules; `flask purge`; daily thread; `data/deletion_log.jsonl`
- Edit: `app.py` — register CLI; start thread; consent already Step 3
- Edit: `pipeline/job_log.py` — redact `reference_image` / base64
- Edit: `pipeline/openai_util.py` — `store=False` where supported; `OPENAI_ZDR_APPROVED` → `job.json`; no name-bearing prompt bodies at INFO
- Edit: `pipeline/book_templates.py` — slots schema; assert no photo/character sheet in template snapshot
- Create: `docs/DATA-MAP.md` — host path, country, backup behaviour (fill with WORKING unknowns)

**Tests**
- `tests/test_retention.py` — photo deleted after retention window (tmp job dirs)
- `tests/test_templates_privacy.py` — template dir has no `reference.*` / `character.png`
- `tests/test_job_log_redaction.py` — append_event strips photo payloads

---

## Step 13 — Emma regressions

**Files**
- Create: `tests/test_emma_regressions.py` — mocked OpenAI fixed PNGs; full pipeline assertions (1)–(9) from the Cursor prompt
- Create: `tests/fixtures/` — tiny PNGs, catalogue subset, fake order

**Tests:** that file only (integration).

---

## Suggested commit sequence

| Step | Suggested commit subject |
|---|---|
| 0 | `docs: Raised Ready migration plan (Step 0)` |
| 1 | `feat: brand catalogue, printer profiles, and feature flags` |
| 2 | `feat: register Raised Ready fonts only` |
| 3 | `feat: Raised Ready create-job input contract and consent` |
| 4 | `feat: character/set/anchor sheets with hero approval gate` |
| 5 | `feat: story from catalogue beats (14 spreads, locked fields)` |
| 6 | `feat: deterministic 32-page map from 14 spreads` |
| 7 | `feat: text guards and in-pipeline humanize_v1` |
| 8 | `feat: validate against catalogue, guards, scene-vs-text` |
| 9 | `feat: spread illustration, multi-ref, and upscale` |
| 10 | `feat: ReportLab text layer and printer-profile PDFs` |
| 11 | `feat: QA reports and human-check gate before preview send` |
| 12 | `feat: retention, redaction, ZDR, DATA-MAP` |
| 13 | `test: Emma-build regressions with mocked OpenAI` |

After Step 13 (or when stopping): update **this** file’s “Changelog / still OPEN” section.

---

## Cross-cutting risks (call out in review)

1. **Step 5 ↔ 7 dependency** — guards must exist before story commit is “done”; prefer early `text_guards.py`.
2. **Magical → RR schema break** — old jobs in `book_assets/` will not open cleanly; acceptable (internal tool); document “RR jobs only” or a read-only archive note.
3. **Assets not in repo yet** — fonts/logos/story beats; Steps 1–2/10 fail closed until files land.
4. **Upscaler weight** — Real-ESRGAN may be heavy; default `UPSCALER=lanczos` with event, or HTTP service (OPEN).
5. **`MEMORY_SCOPE=off` default** — `memory.py` must no-op inject into prompts.
6. **Printer packaging** — keep both TJ and Prodigi paths; Meda owns final TJ template numbers.

---

## Still OPEN (owners) — do not resolve in code

| Item | Owner | Config key / note |
|---|---|---|
| TJ vs Prodigi | Meda; Taoufik approves | `PRINTER_PROFILE` |
| Bleed 3 vs 4 mm | Printer template | `PRINTER_PROFILES[*].bleed_mm` (default 4.0) |
| Endpapers in PDF | Printer | `endpapers_in_pdf` |
| Route titles 4, 7, 9 | Story author | catalogue `route: "open"` |
| Word caps brand vs humanize | Taoufik | `WORD_CAP_SOURCE` |
| Text plate style | Taoufik | `TEXT_PLATE` |
| Humanize on/off | Taoufik after blind test | `HUMANIZE_PASS` default `0` |
| Deletion periods | Taoufik + counsel | `PHOTO_RETENTION_DAYS`, etc. |
| Printed in the UK | After UK contract | `PRINTED_IN_UK_LINE` |
| Title 7 Shopify vs brand name | Taoufik | order/catalogue string |
| Host country / backup | Ops + DPIA | `docs/DATA-MAP.md` |
| Likeness test pass | Book Factory | gate before production |

---

## Changelog

| Date | What |
|---|---|
| 26 Oct 2026 | Step 0 plan written; branch `raised-ready-migration`; **awaiting review before Step 1**. |
