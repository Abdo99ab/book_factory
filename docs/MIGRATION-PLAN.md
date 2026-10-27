# Raised Ready — Book Factory migration plan

**Branch:** `raised-ready-migration`  
**Spec:** `docs/RAISED-READY-BOOK-FACTORY-SPEC.md`  
**Prompt revision:** 26 Oct 2026 evening (Step 12 parked; humanize kept; no upscaler; TJ 3 mm bleed LOCKED)  
**Rule:** one commit per step; pytest under `tests/`; do **not** push until review says so.  
**Status words:** LOCKED / WORKING / OPEN / RECOMMENDED — OPEN and WORKING values live in config with the spec default and an owner comment; never hard-code a guess.

This file is **Step 0 (revised)**. Stop here for review before Step 1.

---

## What we keep (do not rip out)

| Keep | Why |
|---|---|
| Job model `book_assets/jobs/<id>/` (`inputs.json`, `job.json`, `logs/events.jsonl`) | Operator audit trail |
| Event names where possible (`job_created`, `story_*`, `pages_compile_*`, `humanize_*`, `pdf_*`, …) | Logs / UI / ops continuity |
| Flow prepare → review → preview → validate & build | Operator UX |
| `changes.py`, `content_edit.py`, `cast_rebuild.py` (adapted) | NL + form edits |
| `self_revise.py` (extended in Step 10) | Vision QA + approval |
| `book_templates.py` (extended in Step 11) | Title reuse |
| `humanize.py` **as is** (blader/humanizer via Skills API, on by default) | Spec revision: do not replace with `humanize_v1`; adapt call site to spreads + re-guard |

Operator flow after migration: **prepare → approve hero → review text → preview → validate & build → human check → send**.

---

## Explicitly out of scope (parked)

**Do not implement** data protection from spec §14. Leave `# PARKED: data protection (spec §14)` comments at:

- photo upload / `create_job`
- `job_log.append_event`
- `openai_util` client creation
- `archive_job` / `delete_job`

Parked items: consent ticks, retention/deletion jobs, log redaction, `store=False` / ZDR flags, `docs/DATA-MAP.md`.

**Do** add `order_id` to the form (printer file naming).

**Do not** implement an upscaler. Config only: `UPSCALER=none  # OPEN after the printed proof`. Resolution = native model pixels placed in the 216 mm box; warn &lt; 300 ppi, fail &lt; 150 ppi.

---

## Prerequisites (outside code; fail closed if missing when a step needs them)

| Asset | Path | Needed by |
|---|---|---|
| Spec | `docs/RAISED-READY-BOOK-FACTORY-SPEC.md` | All |
| Logo masters | `assets/brand/` | Steps 1, 9 |
| Brand fonts | `assets/fonts/NotoSerif-SemiBold.ttf`, `Nunito-Regular.ttf`, `Nunito-SemiBold.ttf`, `DMMono-Medium.ttf` | Step 2 |
| Story concepts → beats | `brand/stories/<title_id>.md` | Step 1 |
| Existing humanizer skill | `skills/humanizer/` | Keep (Step 5 adapts callers only) |

---

## Step 0 — Plan file *(this document)*

**Files:** `docs/MIGRATION-PLAN.md` (this revision).

**Tests:** none.

**Commit:** `docs: revise Raised Ready migration plan (Step 0).`

---

## Step 1 — Brand as data

**Files**
- Create: `pipeline/brand.py` — `COLOURS` (§6), `BAND_TEXT_COLOUR`, `FONT_FILES`, `SIZE_SCALE` (§5), `LOGO_FILES`, `READ_IT_AGAIN_LINE`, `HONESTY_LINE`, `UK_SPELLING`, `BANNED_PHRASES` (§11.5), `TELLS` (§11.4), `WORDS_PER_SPREAD` (§11.2; cap = upper bound) + `WORD_CAP_SOURCE` (OPEN, Taoufik), `ROUTE_PRESETS` (§8.1–8.2), `SAFE_MM`
- Create: `pipeline/catalogue.py` — load `brand/catalogue.json`
- Create: `brand/catalogue.json` — 10 titles (`big_kid_list` … `too_good_picture`) with all required keys + `approved: false`
- Create: `brand/stories/<title_id>.md` × 10 (14 beats from concept docs)
- Edit: `pipeline/constants.py` — delete `THEMES` / `PURPOSES`; `PRINTER_PROFILES` (`tj_books` bleed **3.0 LOCKED by TJ template 26 Oct**, spine 8, wrap 16, gsm 170, `endpapers_in_pdf: True`, `colour_profile: None`, `packaging: "single_pdf_cover_flat_first"`, qr WORKING; `prodigi` WORKING); `PRINTER_PROFILE` env; derive **216 mm / 2551 px / cover 472×248 mm** (today’s TJ numbers, now from profile); `PRONOUNS`; flags `PRINTED_IN_UK_LINE`, `TEXT_PLATE`, `OUTFIT_CONSTANT`, `IMAGE_QUALITY`, `IMAGE_SIZE`, `MEMORY_SCOPE=off`, `UPSCALER=none` (parked)
- Edit: `requirements.txt` — add `pytest`
- Create: `tests/conftest.py`

**Tests**
- `tests/test_catalogue.py` — loads; every title has every required key
- `tests/test_brand.py` — colour hex list; band grammar; `state_schedule` length 14 for `big_kid_list`
- `tests/test_printer_profile.py` — under `tj_books`: `PAGE_PX == 2551`, cover 472 × 248 mm

**Commit:** `feat: brand catalogue, printer profiles, and feature flags`

> **Update (27 Oct 2026, preview defects):** the Step 1 story files in `brand/stories/*.md` are now **real beats** (14 `## Spread N` blocks plus `approved:`, `sets:`, `carer_signature:` and `last_line:` headers), not placeholders. They stay `approved: false` until Taoufik signs each story. `catalogue-patch.json` (repo root) was applied onto `brand/catalogue.json`: every title's `last_line` equals its story header, and `big_kid_list.parent_spread` was replaced. The story prompt treats the beats as binding (same events, same order, nothing added, last spread ends with the exact catalogue `last_line`).
>
> **Log only (not fixed this round) - likeness test:** the generated child can read older or younger than the order's age and drift from the photo (age and likeness). `scripts/likeness_test.py` should gain an apparent-age item next to hair, skin tone, eyes and glasses/marks, and the likeness test should be run on a 2-3-year-old photo for `big_kid_list`.

---

## Step 2 — Fonts

**Files**
- Edit: `pipeline/fonts.py` — brand only; ReportLab register four TTFs; `RuntimeError` if missing; no system fallbacks; `normalize_font_id` → `"brand"`
- Edit: `templates/create.html` — remove font picker (full form is Step 3)
- Edit: `app.py` — `rebuild_overlays` = re-render text only (no font choice)
- Delete unused Magical TTFs under `assets/fonts/`
- Ensure the four brand TTFs are present

**Tests**
- `tests/test_fonts.py` — registered names; missing file raises naming the TTF

**Commit:** `feat: register Raised Ready fonts only`

---

## Step 3 — Input contract

**Files**
- Rewrite: `templates/create.html` — `title_id`, name, age, pronouns, carer, home_type, comfort_object, slots from schema, dedication, `order_id` (required `[A-Za-z0-9-]+`), photo, `start_mode`; Advanced `story_override` / `art_notes`. **No consent ticks** (parked). Remove Magical fields.
- Edit: `app.py` `create_job` / `create_form` — new `inputs.json`; resolve `route` / `age_band`; ignore legacy `theme` etc.; age-band non-blocking warning; `# PARKED: data protection` at photo upload
- Edit: `templates/job.html` — show title / band / order_id / age warning
- Create: shared slot-renderer helper if needed (JS or server partial)

**Tests**
- `tests/test_create_job.py` — valid post writes new shape; `theme` ignored; missing / invalid `order_id` rejected

**Commit:** `feat: Raised Ready create-job input contract`

---

## Step 4 — Character, set, anchor sheets

**Files**
- Edit: `pipeline/character.py` — route preset + identity paragraph + outfit + multi-view + anchor beside child; `IMAGE_QUALITY`; size via Step 8 helper (or temporary square max); `awaiting_approval`; `generate_set_sheets`, `generate_anchor_sheet`
- Edit: `pipeline/runner.py` — stop at `character_ready`; continue after approval
- Edit: `app.py` — `POST /jobs/<id>/approve-character`
- Edit: `templates/job.html` — sheets + Approve hero sheet

**Tests**
- `tests/test_character_gate.py` — gate holds; approval continues

**Commit:** `feat: character/set/anchor sheets with hero approval gate`

---

## Step 5 — Story + text_guards (+ humanize call-site adapt)

**Files**
- Rewrite: `pipeline/story.py` — new SYSTEM; 14 spreads JSON; locked title/band/question/parent/dedication; cast lock; outfit from catalogue; run guards → retry once → raise
- Create: `pipeline/text_guards.py` — full `check_spreads` (tests fleshed in Step 7)
- Edit: `pipeline/humanize.py` — **keep** Skills/blader path; change to operate on spreads JSON (`humanize_story_fields` adapted); **remove** `humanize_page_texts`; after humanize, re-run guards → on failure fall back to pre-humanize spreads + `humanize_skipped=true` + reasons in event log
- Edit: `pipeline/cast_rebuild.py`, `content_edit.py`, `templates/job.html` — spreads schema
- Edit: `requirements.txt` — add `textstat`

**Tests**
- `tests/test_story.py` — title never from LLM; locked keys; guard retry; humanize fallback on guard failure

**Commit:** `feat: story from catalogue beats with guards and humanize re-check`

---

## Step 6 — Page map

**Files**
- Rewrite: `pipeline/pages.py` — deterministic 32-page map; `calm_zone`; `anchor_state`; `pages_compile_*` events; art paths `spreads/spread-NN/art-left.png` + `art-right.png`
- Edit: `content_edit.py`, `changes.py`, `self_revise.py` — spread addressing; `text_placement` → `calm_zone` adapter in `normalize_action`
- Edit: `templates/job.html`, `runner.py` — spread-oriented UI / preview

**Tests**
- `tests/test_page_map.py` — shape; no LLM call

**Commit:** `feat: deterministic 32-page map from 14 spreads`

---

## Step 7 — Guard fixtures + validation

**Files**
- Create: `tests/test_text_guards.py` — banned phrase, US spelling, locked change, renamed child, +30% words, missing last_line
- Edit: `pipeline/validate.py` — beats + `must_not_show`; structural = guards + band/question match + scene-vs-text; `_apply_repair` never touches `locked`

**Tests:** as above + `tests/test_validate.py`

**Commit:** `feat: text-guard fixtures and catalogue-aware validation`

---

## Step 8 — Illustration at native quality (no upscaler)

**Files**
- Edit: `pipeline/openai_util.py` — `largest_image_size(...)`; `IMAGE_QUALITY` / `IMAGE_SIZE=auto`
- Rewrite: `pipeline/illustrate.py` — **per-page** pair generation (`art-left` then `art-right` with left as ref); `_spread_prompt` / side-aware composition; multi-ref or board; cover + `generate_title_spot`; no Theme/Purpose; memory only if `MEMORY_SCOPE != off`; record `effective_ppi` in `spread.json`; `print_resolution_warning` (&lt;300) / `print_resolution_fail` (&lt;150); never resize up
- Edit: `pipeline/runner.py` — 14×2 page images
- Edit: `pipeline/memory.py` — return `""` when `MEMORY_SCOPE=off`
- **No** `pipeline/upscale.py` implementation; config `UPSCALER=none` only
- Catalogue `post_grade` applied later in `pdf_build` (Step 9), not at generation

**Tests**
- `tests/test_illustrate_prompt.py` — calm zone, must-not-show, no Theme/Purpose
- `tests/test_image_size.py` — fallback order; ppi recorded

**Commit:** `feat: native-resolution page-pair illustration`

---

## Step 9 — Text layer + PDF

**Files**
- Rewrite: `pipeline/pdf_build.py` — ReportLab vector text; soft paper plate; p1 / 30–31 / 32; `build_cover` (no starring / cream / front question); packaging from profile; place images at **native** px in 216 mm box; assert fonts / page map; log min `effective_ppi`; file `print/{order_id}-final.pdf`; apply `post_grade` if set
- Edit: `app.py` download routes
- Edit: `templates/job.html` — download link
- `# PARKED` comments where §14 would touch downloads if needed

**Tests**
- `tests/test_pdf_layout.py` — page order; no cream `(255,250,240)`; brand fonts; `pdftotext`; both packaging modes

**Commit:** `feat: ReportLab text layer and printer-profile PDF`

---

## Step 10 — QA + human check

**Files**
- Create: `pipeline/qa.py` — structure, text_guard, anchor_state, face_position
- Edit: `pipeline/self_revise.py` — reference photo in batches; face/anchor/artefacts; `review_record`
- Edit: `templates/job.html` + `app.py` — Human-check panel; Send preview gated; show min ppi
- Create: `scripts/likeness_test.py`

**Tests**
- `tests/test_qa.py` — structure fail; send blocked until pass

**Commit:** `feat: QA reports and human-check gate`

---

## Step 11 — Templates

**Files**
- Edit: `pipeline/book_templates.py` — store spreads with `{slot}` placeholders + slots schema; reuse re-asks every slot; regenerate sheets/art/PDF; never store photo/character sheet
- Edit: `templates/reuse_template.html`, `templates/templates.html` as needed

**Tests**
- `tests/test_templates_privacy.py` — no `reference.*` / `character.png` in template dir
- Slot fill unit test

**Commit:** `feat: catalogue templates with slot personalisation`

---

## Step 12 — PARKED (data protection)

**Do not implement.** Insert `# PARKED: data protection (spec §14)` at the four sites listed above.

**Optional micro-commit (comments only):** `chore: mark data-protection hook sites as PARKED`

---

## Step 13 — Emma-build regressions

**Files**
- Create: `tests/test_emma_regressions.py` + `tests/fixtures/`
- Mocked OpenAI fixed PNGs; fictional order

**Assert**
1. Anchor tick count per spread = schedule  
2. Dedication p32; spread n on pages 2n / 2n+1  
3. **28 page images** as 14 pairs; right generated with left as reference  
4. Front: band caps + Noto Serif title; no starring; no question form; spine text; back: question, blurb, honesty, URL  
5. Page **216 mm**, cover **472 × 248 mm** under `tj_books`  
6. Brand fonts only; text extractable  
7. `WORDS FOR THE REAL MOMENT` + byte-identical body  
8. No cream boxes  
9. `effective_ppi` recorded; `print_resolution_warning` when &lt; 300  

**Commit:** `test: Emma-build regressions with mocked OpenAI`

---

## Suggested commit subjects (quick list)

| Step | Subject |
|---|---|
| 0 | `docs: revise Raised Ready migration plan (Step 0)` |
| 1 | `feat: brand catalogue, printer profiles, and feature flags` |
| 2 | `feat: register Raised Ready fonts only` |
| 3 | `feat: Raised Ready create-job input contract` |
| 4 | `feat: character/set/anchor sheets with hero approval gate` |
| 5 | `feat: story from catalogue beats with guards and humanize re-check` |
| 6 | `feat: deterministic 32-page map from 14 spreads` |
| 7 | `feat: text-guard fixtures and catalogue-aware validation` |
| 8 | `feat: native-resolution page-pair illustration` |
| 9 | `feat: ReportLab text layer and printer-profile PDF` |
| 10 | `feat: QA reports and human-check gate` |
| 11 | `feat: catalogue templates with slot personalisation` |
| 12 | `chore: mark data-protection hook sites as PARKED` (optional) |
| 13 | `test: Emma-build regressions with mocked OpenAI` |

---

## Diff vs prior plan (26 Oct afternoon)

| Topic | Was | Now |
|---|---|---|
| Bleed / page px | 4 mm → 218 mm / 2575 px | **3 mm LOCKED** → 216 mm / **2551 px**; cover **472×248** (today’s TJ) |
| Upscaler | Real-ESRGAN / LANCZOS step | **None**; ~~per-page square gen; left→right pair refs~~ → one landscape image per spread (27 Oct), cropped never resized |
| Art files | one `art.png` per spread | `art-left.png` + `art-right.png` (28 images) — **superseded 27 Oct: back to ONE wide `art.png` per spread (14 images), split at PDF time; see "27 Oct — true spreads"** |
| Humanize | Replace with `humanize_v1`, flag off | **Keep** blader Skills path; re-guard; drop page-text humanize |
| Data protection | Full Step 12 | **PARKED**; comments only; still collect `order_id` |
| Consent | Two ticks required | Not in this pass |
| `text_guards.py` | Step 7 | Created in **Step 5**; fixtures in Step 7 |
| Packaging | cover_plus_interior etc. | `single_pdf_cover_flat_first` (default) / Prodigi `single_pdf_cover_first_back_last` |

---

## Still OPEN (owners) — do not resolve in code

| Item | Owner | Config / note |
|---|---|---|
| Upscaler after printed proof | Taoufik / Factory | `UPSCALER=none` |
| TJ template confirmation (QR corner, endpapers) | Meda | `qr_*`, `endpapers_in_pdf` |
| Prodigi bleed / packaging | Meda | `prodigi` profile WORKING |
| Word caps brand vs humanize table | Taoufik | `WORD_CAP_SOURCE` |
| Text plate style | Taoufik | `TEXT_PLATE=auto` |
| `MEMORY_SCOPE` off | Taoufik confirms | default `off` |
| Route titles 4, 7, 9 | Story author | `route: "open"` |
| Data protection (§14) | Taoufik + counsel | **PARKED** |
| Printed in the UK | After UK contract | `PRINTED_IN_UK_LINE=0` |
| Title 7 Shopify vs brand name | Taoufik | catalogue / order string |
| Likeness gate | Book Factory | `scripts/likeness_test.py` |

---

## Changelog

| Date | What |
|---|---|
| 26 Oct 2026 (afternoon) | Initial Step 0 plan (4 mm bleed, upscaler, replace humanize, full data-protection step). |
| 26 Oct 2026 (evening) | **Revised** Step 0: TJ 3 mm LOCKED; no upscaler (page-pair native res); keep humanize.py; Step 12 PARKED; `order_id` kept; update file/test map. **Awaiting review before Step 1.** |
| 26 Oct 2026 (night) | **Steps 1-13 complete** on `raised-ready-migration` (not pushed). Step 7 guard fixtures + validation; Step 8 native-resolution page-pair art (no upscaler; warn <300 ppi, fail <150); Step 9 ReportLab vector text + printer-profile PDF (`print/{order_id}-final.pdf`); Step 10 `pipeline/qa.py` + Human-check panel + Send gate; Step 11 catalogue templates with `{slot}` placeholders; Step 12 PARKED comments only (no consent/retention built); Step 13 Emma-build regressions with mocked OpenAI. Full suite: 117 passed. |
| 27 Oct 2026 | **Preview defects fixed** (local, not pushed): (1) real story beats + catalogue patch, `carer_signature` / `sets` in the story payload, binding-beats system prompt; (2) two different pictures per spread (`scene_left` / `scene_right`, text only on the calm-zone half, `pair_duplicate` QA check); (3) front-cover layout rules, one-shot layout regen, `cover_layout_warning`, composed `cover/front_preview.png` on the job page. |
| 27 Oct 2026 — true spreads | **True spreads** (supersedes the "two pictures per spread" and "page-pair" decisions above). ONE wide picture per spread (`art.png`, ~2:1), split at PDF time. Size ladder landscape-only (`2048x1024`, `1792x1024`, `1536x1024` — **never** `1024x1024`); square frames are rejected (`spread_size_rejected` / `SpreadSizeError`); `fit_to_spread` refuses >15 % height crop; step-downs log `image_size_stepdown`. |
| 27 Oct 2026 — round 3 (Emma PDF) | **From PDF Emma-d0cc5422 / RR-TEST-0003.** (1) Landscape-only spreads + crop cap; card shows `size_received` + `effective_ppi`. (2) Local OpenCV Haar face detect on the full frame before crop; empty faces = `no_face` failure + one regen then `no_face_warning`; `head_cut` heuristic on top 3 %. (3) Optional `must_include:` per beat in `brand/stories/*.md` (big_kid_list 6 + 11); text/scene guards; PROPS rule + musical instruments in shared negatives. (4) Cover: deterministic reframe (≤15 % shift + zone soften) before any model regen; one regen max; then `force_soft_plate` + `cover_layout_warning` (job continues — no `cover_title_overlap` stop); back layout check one regen; QR keep-clear uses mean art colour. (5) Story text centred on the calm page (H+V, `align=center`); calm-zone stats measure the centred 60×45 % box; contrast target 7:1; sentence wrap rebalances orphans; 2–3½ band sentences ≤ 9 words. Minors: dedication placeholder/help (no auto "For {name}"), parent body 14 pt / label 10 / spot 90 mm. **Reuse:** regenerate all 14 spreads of RR-TEST-0003 after deploy. Events for the size drop were not in this workspace — pull `logs/events.jsonl` from the server job. |

---

## Status (end of Step 13)

| Step | Commit | Status |
|---|---|---|
| 7 | `feat: text-guard fixtures and catalogue-aware validation` | done |
| 8 | `feat: native-resolution page-pair illustration` | done |
| 9 | `feat: ReportLab text layer and printer-profile PDF` | done |
| 10 | `feat: QA reports and human-check gate` | done |
| 11 | `feat: catalogue templates with slot personalisation` | done |
| 12 | `chore: mark data-protection hook sites as PARKED` | done (comments only) |
| 13 | `test: Emma-build regressions with mocked OpenAI` | done |

Known limits (carry into review): brand TTFs and logo PNGs are still placeholders (swap the real masters in `assets/`); templates keep the source text's pronouns (reuse with other pronouns is refused); the likeness script is a stub; Send only records `sent_at` (no outbound email); real OpenAI output sizes must reach ~2520 px across the whole spread (426 mm) to clear the 150 ppi fail line (a 2048 px frame is ~122 ppi and will log `print_resolution_fail`; the gate stays off by default).

