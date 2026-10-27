# Cursor prompt v3 — migrate `book_factory` to the Raised Ready brand and print spec

Scope decided 26 Oct 2026 (Abdo): brand compliance and print-ready output first. Data-protection and security changes are parked. The blader/humanizer stays on as today. No upscaler; generate at the model's highest native quality and size and record the effective ppi for a printed proof.

Before pasting: `docs/RAISED-READY-BOOK-FACTORY-SPEC.md` in the repo; logo master files (full colour, one-ink ink navy, reverse, mini R; SVG + PNG) in `assets/brand/`; `NotoSerif-SemiBold.ttf`, `Nunito-Regular.ttf`, `Nunito-SemiBold.ttf`, `DMMono-Medium.ttf` in `assets/fonts/`. Written against commit `7f67f96`.

---

You are working in `book_factory` (Flask + `pipeline/`, OpenAI images + chat, Pillow, ReportLab). It produces "Magical"-style books today; it must produce **Raised Ready** books as `docs/RAISED-READY-BOOK-FACTORY-SPEC.md` describes. Read the spec in full; §0 maps every module to its gap. LOCKED values are fixed; WORKING/OPEN/RECOMMENDED values go in config with the spec default and an owner comment. Update `docs/MIGRATION-PLAN.md` to match this prompt (Step 12 parked; humanize unchanged; no upscaler), then stop for review before Step 1.

Keep: the job model, event names, prepare → review → preview → validate & build flow, `changes.py`, `content_edit.py`, `cast_rebuild.py`, `self_revise.py`, `book_templates.py`, `humanize.py` (as is). One commit per step, pytest under `tests/` (add `pytest` to requirements). Do not push.

**Out of scope for now (do not implement, leave a `# PARKED: data protection` comment where the spec §14 would apply):** consent ticks, retention/deletion jobs, log redaction, `store=False`/ZDR flags, DATA-MAP. Do add `order_id` to the form, because the printer files are named with it.

## Step 1 — Brand as data: `pipeline/brand.py`, `pipeline/catalogue.py`, `brand/catalogue.json`, `brand/stories/*.md`, `pipeline/constants.py`

- `brand.py`: `COLOURS` (§6), `BAND_TEXT_COLOUR` (white on rust, white on teal, ink navy on sage, ink navy on butter), `FONT_FILES`, `SIZE_SCALE` (§5), `LOGO_FILES`, `READ_IT_AGAIN_LINE`, `HONESTY_LINE = "Illustrated from {name}'s photo and checked by a person before print."`, `UK_SPELLING`, `BANNED_PHRASES` (§11.5), `TELLS` (§11.4), `WORDS_PER_SPREAD` (§11.2, cap = upper bound; `WORD_CAP_SOURCE=brand|humanize`, OPEN Taoufik), `ROUTE_PRESETS` (`morning_light`, `field_guide`: §8.1 preambles, §8.2 negatives, typography per route), `SAFE_MM = {"trim": 10, "gutter": 15}`.
- `catalogue.json`: the 10 titles of §2 keyed `big_kid_list`, `nursery_day`, `bed_boat`, `night_rocket`, `dinosaur_egg`, `ocean_map`, `tells_truth`, `worry_jar`, `new_front_door`, `too_good_picture`, each with `family`, `colour`, `band` (caps), `question_form`, `title_template` (`{name}`), `age_band`, `route` (`"open"` → default by age band + warning), `outfit`, `anchor` (description, optional `state_schedule`, e.g. `big_kid_list` ticks `[0,1,2,3,4,4,4,4,4,4,4,5,5,5]`), `must_not_show` (§8.4), `personalisation_slots` (§12), `blurb` (≤ 45 words), `parent_spread` (§9 four blocks), `last_line`, `approved: false`. `brand/stories/<title_id>.md`: the 14 beats copied from the project concept docs.
- `constants.py`: delete `THEMES`, `PURPOSES`; replace the TJ block with `PRINTER_PROFILES = {"tj_books": {bleed_mm: 3.0  # LOCKED by TJ template 26 Oct, spine_mm: 8.0, wrap_mm: 16.0, paper_gsm: 170, endpapers_in_pdf: True, colour_profile: None, packaging: "single_pdf_cover_flat_first"  # as today, qr_safe_mm: 28, qr_corner: "bottom_right"  # WORKING, Meda confirms, barcode_required: False}, "prodigi": {bleed_mm: 3.0  # WORKING, endpapers_in_pdf: False, packaging: "single_pdf_cover_first_back_last"}}`, chosen by env `PRINTER_PROFILE` (default `tj_books`); derive `PAGE_WITH_BLEED_MM` (216), `PAGE_PX` (2551), cover geometry (472 × 248 mm) from it — these equal today's hard-coded TJ numbers, which are correct; the change is only that they come from the profile. Replace `GENDER_DETAILS` with `PRONOUNS = {"she", "he", "they"}` sets. Flags: `PRINTED_IN_UK_LINE=0`, `TEXT_PLATE=auto`, `OUTFIT_CONSTANT=1`, `IMAGE_QUALITY=high`, `IMAGE_SIZE=auto`, `MEMORY_SCOPE=off  # RECOMMENDED, Taoufik confirms`.

Tests: catalogue loads with all keys; band grammar; colour hex list; schedule length 14; `PAGE_PX == 2551` and cover 472 × 248 mm under `tj_books`.

## Step 2 — Fonts: `pipeline/fonts.py`, `assets/fonts/`, `templates/create.html`

- Brand families only; register the four TTFs with ReportLab at import; `RuntimeError` naming any missing file; delete every system fallback (`segoeui`, `arial`, `DejaVuSans`, `load_default`); `normalize_font_id` returns `"brand"`. Remove the font picker from `create.html` and the font re-apply in `app.py` `rebuild_overlays` (keep the route as "re-render text"). Delete the unused Magical TTFs.

Tests: registered names; missing TTF raises.

## Step 3 — Input contract: `app.py` `create_job`, `templates/create.html`, `templates/job.html`

- Fields: `title_id` (select showing family, band, age band), `name`, `age`, `pronouns`, `carer_name`, `carer_role`, `home_type`, `comfort_object`, per-title slots rendered from the catalogue schema, `dedication_text`, `order_id` (required, `[A-Za-z0-9-]+`), `reference_image`, `start_mode`; Advanced `<details>`: `story_override`, `art_notes` (stored and logged as overrides). Remove `theme`, `purpose`, `page_count`, `design_rules`, `font_id` (ignore if posted).
- `inputs.json`: `title_id`, `route` (resolved), `age_band`, `pronouns`, `carer{name, role}`, `home_type`, `comfort_object`, `slots{}`, `dedication_text`, `order_id`. Non-blocking warning when `age` is outside the title's age band.

Tests (Flask client): valid post writes the new shape; legacy `theme` ignored; missing `order_id` rejected.

## Step 4 — Character, set and anchor sheets: `pipeline/character.py`, `pipeline/runner.py`, `templates/job.html`

- `generate_character`: prompt = route preset character-sheet text + identity-preservation paragraph (keep the current one) + catalogue `outfit` + age for proportions + pronouns; ask for front, three-quarter and profile views with the anchor object beside the child; `quality=IMAGE_QUALITY`, `size` = largest square the model supports (Step 9 helper). Save `character/character.png|json` with `status: "awaiting_approval"`.
- Add `generate_set_sheets(...)` → `sets/<set_id>.png` for the sets listed in the title's story file, and `generate_anchor_sheet(...)` → `anchor/anchor.png`.
- `runner.prepare_content` stops at `status="character_ready"`; `POST /jobs/<id>/approve-character` records `character_approved_by/at` and continues to story/pages; `job.html` shows the sheets and the approve button.

Tests: gate holds without approval; approval continues.

## Step 5 — Story: `pipeline/story.py` (+ `pipeline/text_guards.py` created here)

- New `SYSTEM`: UK picture-book author/editor. Inputs: the title's 14 beats, child details and slots, length rule for the title's age band, voice rules (§11.3), tells (§11.4), claim rules (§11.6), UK English, locked `last_line`. Output `{"spreads":[{"n":1..14,"text":"...","scene":"illustrator brief, no text in art"}], "blurb_draft":"≤45 words"}`. Drop `moral`, `subtitle`, `synopsis`, `story_beats`, `rebuilt_story`; keep `_cast_lock` logic (cast = child + carer + slot characters only); `recurring_outfit` = catalogue outfit when `OUTFIT_CONSTANT=1`.
- `title`, `band`, `question_form`, `parent_spread`, `dedication` are copied from the catalogue/inputs into `story` under `locked` — never generated.
- Create `pipeline/text_guards.py` now (full tests in Step 7): `check_spreads(draft, candidate, locked, caps)` → `[{code, spread, message}]` for spread count/`n` sequence, locked byte-identical, child name count ≥ draft and identical spelling, per-spread word count ≤ cap and within ±20 % of draft, banned phrases (word-boundary, case-insensitive), UK spelling, no new proper nouns, Flesch–Kincaid (`textstat`) ≤ 2.0 (2–5) / ≤ 4.0 (5–10), ≤ 1 rhetorical question, last spread ends with `last_line`. Run guards on the story output; retry once with the messages appended; then raise.
- Humanize: leave `humanize.py` as it is (blader/humanizer via Skills API, on by default). Call `humanize_story_fields` on the spreads JSON instead of `rebuilt_story`, then **run the same guards on its output**; a guard failure after humanize falls back to the pre-humanize spreads and sets `humanize_skipped=true` with the reasons in the event log. Remove `humanize_page_texts` (page text is now the spread text).

Tests: title never from the LLM; locked keys present; guard retry path; humanize fallback on guard failure.

## Step 6 — Page map: `pipeline/pages.py`, `content_edit.py`, `changes.py`, `self_revise.py`, `templates/job.html`

- `compile_pages` becomes deterministic, no LLM: `p1 {kind:"title"}`, `p2..p29 {kind:"spread_half", spread:n, side:"left"|"right"}` with spread n → pages `2n, 2n+1`, `p30 {kind:"parent_left"}`, `p31 {kind:"parent_right"}`, `p32 {kind:"dedication"}`. Each spread entry: `text`, `scene`, `calm_zone` (alternate left/right or from the story file), `anchor_state` from the schedule. Keep writing `story/pages.json` and the `pages_compile_*` events. Art lives at `spreads/spread-NN/art-left.png` and `art-right.png` (see Step 9). Replace `text_placement` with `calm_zone`; keep an adapter in `self_revise.normalize_action`.

Tests: page map shape; no LLM call.

## Step 7 — Guards tests and validation: `tests/test_text_guards.py`, `pipeline/validate.py`

- Guard fixtures: banned phrase, US spelling, changed locked block, renamed child, +30 % words, missing last line → correct codes.
- `validate.py`: judge prompt uses the title's beats and `must_not_show` instead of "theme"; structural errors include the guards, `band`/`question_form` equality with the catalogue, and scene-vs-text consistency (an object named in the text must appear in the scene brief); `_apply_repair` never touches `locked`.

## Step 8 — Illustration at native quality: `pipeline/illustrate.py`, `pipeline/openai_util.py`, `pipeline/runner.py`, `pipeline/memory.py`

- `openai_util.py`: add `largest_image_size(client, aspect="square"|"landscape")` that queries the model's supported sizes if the API exposes them, else tries a descending list (`4096x4096`, `2048x2048`, `1536x1536`, `1024x1024`; landscape `2048x1024`, `1536x1024`) and caches the first accepted; `quality` default from `IMAGE_QUALITY` (`high`). `IMAGE_SIZE=auto` uses the largest; an explicit value overrides.
- Resolution policy (decided 26 Oct): **no upscaler**. Generate **per page**, not per spread, at the largest square size, because a 1536 × 1024 spread split in two gives 768 px per page (90 ppi) while a 1536² page gives 179 ppi. Each spread is two page images generated as a pair: `art-left.png` first, then `art-right.png` with the left image passed as an extra reference and the prompt stating "right-hand half of the same scene, same light, same set, continue the composition across the fold". Record `effective_ppi = px / 8.504` (216 mm ÷ 25.4) in `spread.json`; log a `print_resolution_warning` event below 300 and a `print_resolution_fail` event below 150; never resize up — `pdf_build` places the image at its native pixel size in the 216 mm box. Keep a parked config key `UPSCALER=none  # OPEN after the printed proof` with no implementation.
- `_spread_prompt`: route preset preamble → character reminder → set sheet → anchor sheet + `anchor_state` sentence ("the paper list shows exactly 3 ticks") → scene → composition: "the {side} page of a double-page spread; keep every face ≥ 15 mm from the inner edge (the fold) and ≥ 10 mm from the outer edges; leave a calm open area on the {calm_zone} page for text" → carer signature item → shared negatives + `must_not_show` → `NO_TEXT_RULE`. Remove `Theme`, `Purpose`, `design_rules`; include `preference_memory` only when `MEMORY_SCOPE != off` (`memory.py` returns `""` when off).
- References: pass `[character.png, anchor.png, set.png]` to `images.edit` when the SDK accepts a list, else composite a reference board.
- Cover: `generate_cover_panels` front prompt = face as the largest element, calm zone across the top 13 mm band area and below it for the title, calm bottom-centre for the logo; back prompt = anchor object in its end state, calm top half; same references; largest square size. Add `generate_title_spot` (small anchor-object illustration for p1).
- Night/underwater titles: `post_grade` per title in the catalogue, applied in `pdf_build` after QA, never at generation.

Tests: prompt contains calm zone, must-not-show, no Theme/Purpose; size helper falls back in order; ppi recorded.

## Step 9 — Text layer and PDF: `pipeline/pdf_build.py`, `app.py` download routes

- Remove the Pillow text drawing and cream rounded boxes entirely. Text is **ReportLab vector text** over the placed image: `Nunito-Regular`, Morning Light 22–24 pt / leading 1.35, Field Guide 16–18 pt / 1.4, ink navy `#22324A`, wrapped in the calm-zone box (page minus `SAFE_MM` trim and gutter). Contrast: sample mean luminance of the art under the box; if < 4.5 : 1 and `TEXT_PLATE != never`, draw a soft-edged paper plate first (`#FAF6EF`, alpha 0.85–0.9, radius ≈ 6 mm, padding ≈ 6 mm, as an RGBA PNG with mask), then the text.
- Page 1: band rectangle 13 mm + bleed in the family colour, band text DM Mono 10 pt caps +12 % tracking in `BAND_TEXT_COLOUR`; title Noto Serif SemiBold ("{Name} and the" 22–26 pt on its own line, rest 44–52 pt, ≤ 3 lines); "Illustrated from {Name}'s photo" Nunito 12 pt; title spot; paper ground.
- Pages 30–31: heading `WORDS FOR THE REAL MOMENT` (Noto Serif SemiBold), DM Mono labels, Nunito 12 pt body from `story.locked.parent_spread`, READ IT AGAIN line byte-identical; paper ground + anchor spot.
- Page 32: dedication (Nunito), read-it-again note, one-ink logo ≥ 30 mm, `order_id` DM Mono 9 pt inside the safe zone, raisedready.co.uk.
- Cover `build_cover` (replaces `build_wraparound_cover`): geometry from the printer profile. Front: art full-bleed; band 13 mm + bleed with band text; title Noto Serif SemiBold on the calm zone (ink navy or oat by luminance); logo bottom centre (full colour if ≥ 60 mm on a light area, else one-ink). **Delete** `starring {child_name}`, the cream box and any question form on the front. Spine: family colour fill; title Noto Serif SemiBold 8 pt reading top-to-bottom; mini R 4–5 mm at the foot (mini R only if `spine_mm < 8`). Back: oat ground or art with a paper plate; question form 22–24 pt Noto Serif SemiBold at the top; blurb ≤ 45 words Nunito 12/16; `HONESTY_LINE`; raisedready.co.uk; full-colour logo; small print ≥ 8 pt; QR/batch safe corner from the profile; "Printed in the UK" only when `PRINTED_IN_UK_LINE=1`. All cover text vector.
- `build_pdf` packaging from the profile: `single_pdf_cover_flat_first` (today's order: cover flat, endpapers if `endpapers_in_pdf`, 32 pages, endpaper) or `single_pdf_cover_first_back_last` (Prodigi: front cover, 32 pages, back cover, no endpapers). Fonts embedded; file `print/{order_id}-final.pdf`. Assert before save: 32 interior pages; p32 is the dedication; spread n on pages 2n/2n+1; fonts used ⊆ brand set; log the minimum `effective_ppi` of the book.

Tests (fixture PNGs): page order; no `(255,250,240)` pixels in sampled text regions; `pdffonts`-style font list; text extractable with `pdftotext`; both packaging modes.

## Step 10 — QA and human check: `pipeline/qa.py`, `pipeline/self_revise.py`, `templates/job.html`

- `qa.py`: `structure_check` (Step 9 assertions), `text_guard_report`, `anchor_state_report` (vision question per spread "how many ticks on the list?" vs the schedule, for titles with a schedule), `face_position_report` (face boxes vs fold/trim bands; OpenCV Haar is enough).
- `self_revise.py`: include the reference photo in every vision batch and ask the judge to compare each spread's face to it (face shape, skin tone, eyes, hair, glasses/marks), plus anchor-object and hands/eyes artefact questions; keep the operator-approved plan; store `review_record {reviewer, timestamp, result_per_spread}` in `job.json`.
- `job.html`: Human-check panel — photo beside each spread and the cover, anchor crop strip, pass/fail per spread, read-aloud boxes for spreads 1, 7, 14 (sounds like one person / no lesson sentence / name correct / nothing in the text that isn't drawn), minimum ppi shown; "Send preview" disabled until structure, guards, anchor, face position and human check pass.
- `scripts/likeness_test.py`: 3 fictional children × 4 spreads per route → CSV score sheet (pass 33/36).

## Step 11 — Templates: `pipeline/book_templates.py`

- A template stores the approved spreads with `{slot}` placeholders and the `slots` schema; reuse re-asks every slot (not only name/age), fills placeholders deterministically, regenerates sheets, art and PDF. Templates must never contain `reference.*` or `character.png` (test).

## Step 12 — PARKED (data protection, security)

Do not implement. Leave `# PARKED: data protection (spec §14)` comments at: photo upload, `job_log.append_event`, `openai_util` client creation, `archive_job`/`delete_job`.

## Step 13 — Emma-build regressions: `tests/test_emma_regressions.py`

Mocked OpenAI (fixed PNGs), fictional order, assert: (1) anchor tick count per spread = schedule; (2) dedication on p32, spread n on 2n/2n+1; (3) 28 page images grouped as 14 pairs, each pair generated with the left image as reference; (4) front cover has the band in caps and a Noto Serif title, no "starring", no question form; spine text present; back has question form, blurb, honesty line, URL; (5) page = 216 mm and cover 472 × 248 mm under `tj_books`; (6) only brand fonts, text extractable; (7) `WORDS FOR THE REAL MOMENT` heading + byte-identical body; (8) no cream boxes; (9) `effective_ppi` recorded per spread and a `print_resolution_warning` event when below 300.

## Working rules

- Brand values live in `pipeline/brand.py` / `brand/*.json`; code reads them; cite the spec section in a comment.
- UK English everywhere.
- Never invent copy: titles, bands, blurbs, parent spreads come from the catalogue; a missing value fails loudly with `title_id` and field.
- Keep the operator flow working at every step: prepare → approve hero → review text → preview → validate & build → human check → send.
- Do not push. End by updating `docs/MIGRATION-PLAN.md` (changelog + still OPEN, including: upscaler decision after the printed proof; TJ template numbers; data protection parked).
