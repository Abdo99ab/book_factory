# Raised Ready — Book Factory specification

**What this is.** Everything the Book Factory (repo `Abdo99ab/book_factory`, deployed at books.tweezgroup.com) must know to produce a Raised Ready book that matches the locked brand: story text, illustration, page layout, covers, print file, QA gates and data rules. Compiled 26 Oct 2026 from the Brand Guidelines v1 (live doc, rev 51), Brand Identity v1 PDF (22 Oct snapshot), the never-say and book-design-rules files, the story concepts (14–15 Oct), the humanize-pass spec (26 Oct), the data-protection pack (draft, 22 Oct), the project decision log, and a read of the Book Factory code at commit `7f67f96` (26 Oct 2026).

---

## 0. Where the current code stands (commit 7f67f96) against this spec

The Factory is a Flask app (`app.py`) with a `pipeline/` package: `character.py` (photo → hero sheet via `images.edit`), `story.py` (draft + theme + purpose → rebuilt manuscript), `humanize.py` (blader/humanizer via the OpenAI Skills API), `pages.py` (LLM compiles 32 single pages), `validate.py` (structural + LLM QA, one auto-repair), `illustrate.py` (one 1024 × 1024 image per page and per cover panel), `pdf_build.py` (Pillow overlays text, stitches a TJ wraparound cover, ReportLab writes one PDF), `self_revise.py` (vision QA of composited pages), `changes.py` / `content_edit.py` / `cast_rebuild.py` (operator edits), `book_templates.py` (snapshot a finished story, reuse with a new child), `memory.py` (learned operator preferences injected into every prompt), `fonts.py` (8 "Magical-style" display fonts), `constants.py` (TJ template numbers, themes, prompts).

| Area | Current code | Spec requires | Gap |
|---|---|---|---|
| Product model | `THEMES` (Adventure, Fairy Tales …) + `PURPOSES` + free-text `design_rules` + pasted draft story; book title invented by the LLM | 10 fixed titles, each with family, band, question form, route, anchor object, beats, locked parent spread (§2) | Replace theme/purpose with `title_id`; title string and bands come from the catalogue |
| Page structure | `pages.py` prompt: 32 single pages, "Page 1 title, Page 2 may be a dedication"; LLM decides | Deterministic page map: p1 title, pp 2–29 = 14 spreads, pp 30–31 parent spread, p32 dedication (§3) | Build the map in code; LLM writes 14 spread texts only |
| Image unit | One 1024 × 1024 image per page (`PAGE_IMAGE_SIZE`), `quality="low"`, Pillow LANCZOS resize to 2,551 px (≈ 120 ppi real) | One image per spread at 5,055 × 2,575 px effective 300 ppi; title spot, cover art (§3, §8) | Landscape generation + real upscaler; quality high; spread split into two pages |
| Bleed / page size | `BLEED_MM = 3.0` → 216 mm / 2,551 px, hard-coded TJ | 4 mm / 218 mm / 2,575 px per brand; value from printer profile (§3) | Printer profile config |
| Text | Pillow draws text into the JPEG inside a cream rounded box (`fill=(255,250,240,225)`), font from an 8-font picker, size auto-fit 48–110 px | Live vector text in ReportLab, Nunito 22–24 pt / 16–18 pt, ink navy, calm zone, soft paper plate only when contrast fails (§5) | Rewrite `overlay_text_on_image` → ReportLab text layer; drop font picker |
| Fonts | Merriweather, Nunito-Bold, Fredoka, DancingScript, Montserrat, Baloo2, Quicksand, PatrickHand, LuckiestGuy; fallbacks to Arial/DejaVu/`load_default` | Noto Serif SemiBold, Nunito (Regular + SemiBold), DM Mono Medium only; embedded; fail on fallback (§5) | Replace `fonts.py`, bundle TTFs |
| Cover | Front: title + subtitle + "starring {name}" in a cream box at the top; spine = flat colour (70,110,100) with nothing on it; back = art with a flattened QR corner, no text; one cover flat first in the PDF | Front: 13 mm family band in caps, Noto Serif title, logo; spine: family colour, title 8 pt, mini R; back: question form, blurb ≤ 45 words, honesty line, URL, logo (§4) | Rebuild `build_wraparound_cover` |
| Character | One full-body front sheet from the photo; `design_rules` free text as art direction; `age` used | Route-specific sheet (front, three-quarter, profile), approved by a person before spreads; plus set sheet and anchor sheet (§8.3) | Extend `character.py`; add sheets |
| Story prompt | Generic editor for ages 1–10, "match the theme", outputs `moral`, `subtitle`, `synopsis`, `story_beats`, `rebuilt_story`; US spelling not prevented | Beats from the title's concept, length by age band, voice/tells/claim rules, UK English, no stated moral, locked last line (§11) | Rewrite `story.py` SYSTEM and schema |
| Humanize | blader/humanizer SKILL.md uploaded to `/v1/skills`, run in a hosted shell; default ON; also rewrites page texts; no guard checks | Prompt-based pass (`humanize_v1`) inside the pipeline, no third-party humanizer, deterministic guards, flag default OFF until the blind test (§11.7) | Replace `humanize.py` internals; keep the event names |
| Validation | Structural (count, placement, "paint text" regex) + LLM judge keyed to theme | Add brand guards: banned phrases, UK spelling, caps, name spelling, locked text byte-identical, band/question match (§13.2) | Extend `validate.py` |
| Memory | Learned preferences injected into character, story, page and image prompts "apply unless the brief contradicts" | Preferences must never override brand rules; brand presets are the brief | Scope memory to a per-title allow-list or disable for RR |
| Self-revise | Vision QA on composited pages, operator approves a plan | Keep; add face check against the reference photo, anchor-state check, read-aloud checklist, reviewer record; block send until pass (§13) | Extend `self_revise.py` + `job.html` |
| Templates | `save_job_as_template` snapshots story/pages; reuse swaps the hero name | Good fit for the 10 titles: an approved title = a template with personalisation slots, not just a name swap (§12) | Extend `book_templates.py` |
| Data | Photo kept in `book_assets/jobs/<id>/character/reference.*` forever (archive keeps it); no consent record; no deletion jobs; `job_created` event logs all inputs | Two consent ticks, order ID on files, scheduled deletion with a log, no photo in logs or backups (§14) | Add consent + deletion scheduler |
| PDF packaging | One PDF: cover flat, blank endpaper, 32 pages, blank endpaper | Per printer: TJ = cover PDF + interior PDF (or as TJ specifies); Prodigi = one PDF, front cover first, back cover last, no endpapers (§3) | Printer profile |

Everything else in this document is the target state.

**Status words used below.** LOCKED = signed off by Taoufik, do not change. WORKING = provisional until the printer confirms its template. OPEN = decision not yet taken; build it configurable, never hard-code a guess. RECOMMENDED = Claude's suggestion, Taoufik confirms.

**Sources of truth, in order.** 1. Brand Guidelines v1 (live Claude Doc, https://claude.ai/artifact/75Xsqj9v2YUgktKaaGs7ss). 2. Raised-Ready-Brand-Identity-v1.pdf (snapshot). 3. `claude/parenting-guru/never-say.md` and `book-design-rules.md`. 4. Story concepts 1–12. 5. Humanize-pass spec. 6. Data-protection pack. If this file and the live doc ever disagree, the live doc wins; tell the team.

---

## 1. The brand in one screen

- **Name:** Raised Ready (two words). Domain raisedready.co.uk. GB launch only, 16 Nov 2026, UK English only.
- **What a book is:** a personalised picture book in which the child (illustrated from one photo) is the hero of a story about a real step they are about to take — potty, nursery, own bed, a new home — or a human skill (honesty, worries, asking "is it real?"). The child practises the step in the story first, then for real.
- **Promise (four parts, every book must deliver all four):** (1) It's really them — their face on every page, checked by a person before print. (2) It's their step — a story about what is happening in their house now. (3) It's your script — the words to use in the real moment, printed in the book. (4) It's made to be read again — built for night after night.
- **Voice:** an experienced nursery key worker who is also good fun. Warm not sugary, calm not preachy, specific not vague, honest not hype, playful with the child but never silly with the parent.
- **What we are not:** not a quick fix, not a lecture, not anti-tech, not a gift gimmick, not a toy shop. Never "a world run by AI" in GB copy.
- **AI honesty:** inside the book and on the product page we say the illustrations are made with AI from the child's photo and checked by a person before every print. Never "hand-painted by our artists".
- **Price (LOCKED for the GB test):** £32 including free UK delivery, every title. Not printed anywhere on the book.
- **Bad-output policy (LOCKED):** up to 2 free redraw rounds before print; still wrong → full refund, nothing printed. Print faults, damage, loss, or a face error the check missed → free reprint.

---

## 2. Catalogue — 10 launch titles, 4 families (LOCKED structure; titles are working titles until Taoufik approves each story)

| # | Family (ad group) | Family colour | Front-cover band (capitals) | Back/spine question form | Working title | Age | Illustration route | Anchor object (drawn identically on every spread) | Target behaviour / quiet skill |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Next steps | Rust #A9502F | READY FOR THE POTTY | Ready for the potty, [Name]? | [Name] and the Big-Kid List | 2–3½ | Morning Light | The paper list on the fridge (5 pictures: shoes, coat, cup, hands, potty; ticks added spread by spread) + comfort object | Potty: willingness, child decides |
| 2 | Next steps | Rust | READY FOR NURSERY | Ready for nursery, [Name]? | [Name]'s Nursery Day — and Then You Come Back | 2–5 | Morning Light | Comfort object (on ≥ 8 spreads); locked nursery interior reference sheet | Drop-off: predictable return |
| 3 | Next steps | Rust | READY FOR THE BIG BED | Ready for the big bed, [Name]? | The Night [Name]'s Bed Became a Boat | 2–6 | Morning Light | The bed/room (one set built from bed type, night-light, door) + comfort object as first mate | Own bed: the routine and the exit line |
| 4 | Adventures | Teal #0F5257 | READY FOR ADVENTURE | Ready for adventure, [Name]? | [Name] and the Night Rocket | 4–8 | OPEN (spans both bands; set when the story is written) | The rocket and the helmet (visor never covers the face) | Quiet skill: asking good questions (no claim) |
| 5 | Adventures | Teal | READY FOR ADVENTURE | Ready for adventure, [Name]? | [Name] and the Lost Dinosaur Egg (gift title) | 3–6 | Morning Light | Pip the baby dinosaur (design locked on spread 2); the shiny shell from spread 12 | Quiet skill: kindness, helping someone smaller (no claim) |
| 6 | Adventures | Teal | READY FOR ADVENTURE | Ready for adventure, [Name]? | [Name]'s Ocean Map | 5–8 | Field Guide | The turtle guide and the sea-glass compass | Quiet skill: trying again (no claim) |
| 7 | Growing skills | Sage #8FA58B | READY TO TELL THE TRUTH | Ready to tell the truth, [Name]? | [Name] Tells the Truth | 4–8 | OPEN (spans both bands) | The broken object the parent chose (spreads 2–9) | Honesty: praise the confession (Lee 2014) |
| 8 | Growing skills | Sage | READY TO SHARE A WORRY | Ready to share a worry, [Name]? | [Name]'s Worry Jar | 5–9 | Field Guide | The jar; grey stones that never look like faces | Worries: say it, sort it, jar it |
| 9 | Growing skills | Sage | READY FOR A NEW HOME | Ready for a new home, [Name]? | [Name]'s New Front Door | 4–8 | OPEN (spans both bands) | The first-night box; two home reference sheets | Moving: rehearse the sequence, end with relief |
| 10 | Tomorrow's world | Butter #F2C94C | READY TO ASK "IS IT REAL?" | Ready to ask "is it real?", [Name]? | [Name] and the Too-Good-To-Be-True Picture | 7–10 | Field Guide | The truth-detective notebook; the same flying cat on spreads 1–10 | Telling true from fake: four checks |

Rules that follow from the table:
- Band grammar: "Ready for" + a step (an event); "Ready to" + a skill (a habit of mind). Adventure titles carry "READY FOR ADVENTURE" and nothing more — no skill claim anywhere on or in an adventure book.
- One family colour per book; never mix families on one cover. Never number the families or show them as levels or an age order.
- The illustration route belongs to the TITLE, not to the child's age entered in the form. A 5-year-old ordering title 1 still gets Morning Light.
- Alternate bands held in reserve if ads show discomfort: "Ready to Be Brave and Honest" (title 7), "Ready for Big Feelings" (title 8). Build bands as data, not code.
- Shopify currently lists "The Day I Told the Truth" for title 7; the brand book says "[Name] Tells the Truth". Treat as unresolved; the Factory takes the title string from the order, never invents one.

---

## 3. Book format and page map (LOCKED 22 Oct; printer template WORKING)

**Physical:** 210 × 210 mm trim. 32 interior pages. Case-bound hardcover, laminated printed cover, PUR binding. Interior 170 gsm uncoated (TJ Books quote; working). Bleed 4 mm on every edge. Endpapers plain, added by the printer, NOT counted in the 32 (confirmed in the Factory form, 26 Oct; printer to confirm).

**Safe zones:** text and faces ≥ 10 mm from the trim and ≥ 15 mm from the gutter. No face ever sits across the gutter.

**Page map (every title, never varies):**

| Interior page | Content | Layout notes |
|---|---|---|
| 1 | Title page: family band, full title, line "Illustrated from [Name]'s photo", one small spot illustration | Right-hand page (recto). Single page, no facing page. |
| 2–29 | 14 story spreads, one scene per spread | Spread n occupies pages 2n and 2n+1 (2–3, 4–5 … 28–29) so the two halves face each other. ONE illustration per spread (5,055 × 2,575 px), text set in code over a calm zone. |
| 30–31 | "Words for the real moment" spread | Same layout in every title (section 9). |
| 32 | Dedication ("For [Name], from [giver]"), read-it-again note, small logo (one-ink), order code | Last left-hand page (verso). |

**Why the order matters:** with the title page alone on page 1, every spread starts on a left-hand page. Putting the dedication on page 2 (as the 26 Oct Emma build did) shifts every scene across a page turn. The dedication is page 32.

**Page count must equal the PDF exactly** — Prodigi holds an order silently if they differ; TJ will query it. The Factory validates page count before upload.

**Pixel sizes at 300 ppi (LOCKED):**

| Item | Trim | With 4 mm bleed | Pixels |
|---|---|---|---|
| Single page | 210 × 210 mm | 218 × 218 mm | 2,575 × 2,575 px |
| Double-page spread | 420 × 210 mm | 428 × 218 mm | 5,055 × 2,575 px |
| Cover wrap | from printer template (back + spine + front + wrap) | — | 300 ppi at template size |

If the image model outputs under 2,500 px on the short side, add an upscaling step before the print file. The 26 Oct Emma build used 2,551 px / 216 mm (3 mm bleed): wrong until the printer says otherwise.

**Spine (WORKING):** 8 mm for 32 pp on 170 gsm (TJ figure in the Factory form). The printer's template gives the final value.

**Print PDF deliverable (WORKING, printer confirms):**
- Interior: one PDF, 32 pages in reading order (p1 … p32), each page 218 × 218 mm (trim + bleed), no printer's marks unless asked, CMYK via the printer's ICC profile (TJ prints HP Indigo), PDF/X-4 or X-1a as the printer specifies, all three fonts embedded, text as live vector text (never rasterised into the image).
- Cover: a separate PDF on the printer's case-wrap template (back cover | spine | front cover, plus wrap allowance), or — for Prodigi — a single PDF with the front cover first and the back cover last and the page count in the order equal to the PDF. Make the packaging mode a per-printer setting.
- File naming carries the order ID (data-protection rule: order ID on every file so child A never lands in child B's book).

---

## 4. Covers (LOCKED: full-bleed art; sizes from the brand size scale)

**Front cover hierarchy (in this order of visual weight):** 1. the family band, 2. the child's face (the biggest thing at thumbnail size), 3. the title, 4. the logo.

| Element | Spec |
|---|---|
| Band | Full width across the top, 13 mm deep below the trim (plus bleed), ground = family colour, text in capitals, DM Mono Medium 10 pt, +12 % tracking. Text colour by family: white on rust (Next steps), white on teal (Adventures; oat on teal allowed), ink navy on sage (Growing skills), ink navy on butter (Tomorrow's world). |
| Art | Full-bleed, text-free, generated with a planned calm zone for the title. The child's face large. |
| Title | Noto Serif SemiBold. "[Name] and the" (or "[Name]'s" / "The Night [Name]'s") on its own line at 22–26 pt; the rest at 44–52 pt, max 3 lines. Ink navy, or oat/paper on a dark calm area. Never in a white box; never Nunito. |
| Logo | Full-colour lockup bottom centre on a calm area (≥ 30 mm wide; full colour only at ≥ 60 mm wide, else one-ink ink navy). On busy art use the one-ink version or a small paper plate. Never straight full-colour on busy art. |
| Never on the front | "starring [Name]", the question form, a tagline, a seal, stars or decorations, price. |

**Spine:** ground = family colour. Title reads top to bottom, Noto Serif SemiBold 8 pt; mini R (steps + R) 4–5 mm at the foot, rotated with the text. On spines under 8 mm: mini R only. Never a blank spine.

**Back cover (ground = oat #F3EBDD or the art continued with a calm zone):**
1. The question form at the top: "Ready for the potty, [Name]?" — Noto Serif SemiBold 22–24 pt.
2. A blurb of ≤ 45 words: what happens in the story, no outcome claim, no timeline. Nunito 12 pt on 16 pt leading.
3. One line: "Illustrated from [Name]'s photo and checked by a person before print."
4. raisedready.co.uk and the full-colour logo (on oat/paper).
5. Small print ≥ 8 pt. No price, no seal, no barcode unless the printer requires one. No "Printed in the UK" until a UK printer contract is signed (feature flag, default off).

**Cover art consistency:** the cover child is the same reference as the interior; the cover counts in the face check. Back-cover art, if any, shows the anchor object (e.g. the list fully ticked).

---

## 5. Typography (LOCKED 22 Oct)

| Role | Font | Notes |
|---|---|---|
| Wordmark | Artwork from the 06A master file only | Never retyped in a font |
| Titles and headlines (cover, title page, spine, back-cover question, "Words for the real moment" heading) | Noto Serif SemiBold | One serif across both routes so ten titles read as one shelf |
| Story text, parent page body, dedication, blurb | Nunito (Regular/SemiBold) | For ages 5–7 reading alone, Andika is an option (single-storey a and g) — OPEN, default Nunito |
| Bands and labels (cover band, page furniture, order code) | DM Mono Medium, capitals, wide tracking | |

**Size scale (fixed):**

| Where | Size |
|---|---|
| Front cover title | 44–52 pt, max 3 lines; "[Name] and the" 22–26 pt |
| Front cover band | 10 pt caps, +12 % tracking, band 13 mm deep |
| Back cover | Question 22–24 pt; blurb 12/16 pt, ≤ 45 words; small print ≥ 8 pt |
| Spine | Title 8 pt top to bottom; mini R 4–5 mm |
| Inside, ages 2–5 (Morning Light) | Story 22–24 pt, leading 1.35; page furniture 9 pt |
| Inside, ages 5–10 (Field Guide) | Story 16–18 pt, leading 1.4; parent page 12 pt |

Text colour is ink navy #22324A on a light calm zone (contrast ≥ 4.5 : 1 against the art behind it; measure it). RECOMMENDED: no white speech-bubble boxes; if the art behind the calm zone fails contrast, lay a soft-edged paper plate (#FAF6EF, ~85–90 % opacity) under the text rather than a hard white rectangle. Taoufik confirms.

All fonts are free Google Fonts licensed for print. Embed all three in every PDF; nothing ships with system fallbacks (Helvetica appearing in the PDF's font list = failure).

**UK English only, everywhere:** personalised, colour, practise (verb), practice (noun), nappies, nursery, reception, key worker, big bed, bedtime, drop-off, grown-up, mum, loo, jumper, garden, wellies, pants (underwear), trousers, dummy, sweets, pavement, torch. Never: diapers, preschool, kindergarten, mom, color, pacifier, candy, sidewalk, flashlight. Curly quotes and a spaced en dash or an em dash are fine; be consistent within a book.

---

## 6. Colour (LOCKED 22 Oct; also the master logo values)

| Name | Hex | RGB | Role |
|---|---|---|---|
| Ink navy | #22324A | 34 50 74 | All text, wordmark, buttons |
| Oat | #F3EBDD | 243 235 221 | Main ground: covers, gift card |
| Paper | #FAF6EF | 250 246 239 | Inside pages, cards, Field Guide covers, text plates |
| White | #FFFFFF | | Product photos, email only |
| Rust | #A9502F | 169 80 47 | Next steps family; links |
| Teal | #0F5257 | 15 82 87 | Adventures family |
| Sage | #8FA58B | 143 165 139 | Growing skills family |
| Butter | #F2C94C | 242 201 76 | Tomorrow's world family; fills only, never text |

Proportions on any page: about 60–70 % ground, 20–25 % ink navy, ≤ 10 % family colour.

**Contrast table (WCAG, body text needs 4.5 : 1):** ink navy on paper 12.0, ink navy on oat 10.9, oat on ink navy 10.9, white on teal 8.9, ink navy on butter 8.2, oat on teal 7.5, white on rust 5.4, ink navy on sage 4.9, rust on oat 4.6. **Never for text:** white on sage (2.7), butter on oat (1.3), the lighter Route-A clay #C8664A on oat (3.3). Note: the Morning Light palette in the early options board used clay #C8664A; the LOCKED rust is #A9502F — use #A9502F for anything printed as a brand colour; the art itself may contain warm clay tones.

Print: convert to CMYK with the chosen printer's ICC profile; approve a printed swatch strip with the first samples before the family colours are final on paper.

---

## 7. Logo (LOCKED; master files v1, 22 Oct)

06A Balanced: four separated rounded steps rising on a diagonal to the left of the wordmark "Raised Ready" in ink navy. Steps bottom to top: rust → teal → sage → butter (= the four families; the order never changes). Geometry at cap height 100: steps 51 × 29, corner radius 7, each step offset 55 across and 33 up, symbol-to-wordmark gap 42, bottom step on the baseline. Clear space = cap height of the R on every side.

Versions and where they go in the book:

| Version | Use | Minimum |
|---|---|---|
| Full colour | Back cover, (front cover if the calm area is light) | ≥ 60 mm wide in print; below that one-ink |
| One ink, ink navy | Title page, page 32, front cover on busy art | ≥ 30 mm wide lockup |
| Reverse (all white) | Only on ink-navy or teal panels | |
| Mini R | Spine foot (4–5 mm), favicon | ≥ 64 px on screen |

Never: recolour, reorder or number the steps; rotate (except down a spine); stretch, outline, add shadow, stars or a tagline. Master files (SVG, PDF, PNG; full colour, one ink, black, reverse; mini R and favicon set) are in the v1 zip delivered in the Brand chat — to be uploaded to the shared Drive. The Factory must load these files, never redraw the mark.

---

## 8. Illustration

### 8.1 The two routes (LOCKED 18/22 Oct; likeness test still to pass)

| | Morning Light (ages 2–5: titles 1, 2, 3, 5) | Field Guide (ages 5–10: titles 6, 8, 10) |
|---|---|---|
| Medium | Matte gouache, visible brush strokes and paper texture | Fine charcoal ink line + translucent watercolour wash |
| Light and colour | Warm daylight; oat, rust/clay, sage, butter | Paper white ground; teal, sage, butter accents |
| Settings | Real UK homes: terraced houses, nursery coat pegs, wellies by the door, rain on windows, small back gardens | Real UK places, observed: classroom, kitchen table, park, library, high street |
| Viewpoint | Child's eye level; an adult's hand or legs often in frame | Slightly wider, observational; room for noticing details |
| Details | Soft, few, large shapes | Small maps, labels, arrows, "spot it" details — never worksheets |
| Proportions | Toddler / preschool | Older, less rounded faces and bodies |
| Avoid | Glossy 3D, plastic skin, toy-shop saturation | Anime styling, glossy colour, cluttered pages |

Titles 4, 7, 9 span both bands: route OPEN, set when each story is written. Store route per title, default by age band.

**Prompt-ready style preambles (RECOMMENDED wording, keep as editable presets):**

Morning Light — "Children's picture-book illustration, matte gouache on textured paper, visible brush strokes, soft edges, warm morning daylight, muted palette of oat cream, warm clay, sage green, butter yellow and ink navy, real British terraced-house interior with coat pegs, wellies and rain on the window, drawn from a small child's eye level, large simple shapes, calm uncluttered composition with a quiet empty area reserved for text, no text, no letters, no logos, no glossy 3D rendering, no plastic skin."

Field Guide — "Children's book illustration in fine charcoal ink line with translucent watercolour wash on white paper, observed real British setting, slightly wide observational viewpoint, older child proportions, small hand-drawn labels, arrows and map details as decoration only, accents of deep teal, sage and butter yellow, calm composition with an open area reserved for text, no readable text, no logos, no anime styling, no glossy colour, no clutter."

### 8.2 Shared rules (LOCKED, from the evidence)

1. The child is human, realistic and central on every spread; other characters appear whom the child helps or understands.
2. Real settings and a real-world solution the child can copy. No magic solves the problem (adventures keep the imaginative frame inside a real one: the bed never leaves the room).
3. Show the wanted behaviour and its reward; never punishment, never the unwanted behaviour in detail.
4. One target step or behaviour per book.
5. Never introduce the fear the book is about (no monsters in a bedtime or worry book; no catastrophes drawn).
6. Plain pages: no clutter, no flaps; a calm zone for the text on every spread.
7. One fixed anchor object per title, drawn the same way on every spread; it is the reference for the face and style check.
8. No text, letters, numbers or logos inside the art. All words are set in code.
9. Screens show nothing readable; no child is ever shown using an AI tool.
10. Faces always visible: no helmet visor, diving mask, hood or hat covering the face; carers never shown with an unhappy face at an accident or wobble.

### 8.3 Consistency method (RECOMMENDED pipeline)

1. **Character sheet first.** From the parent's photo, generate one reference sheet of the child in the route's style (front, three-quarter, profile; neutral everyday outfit chosen for the book; skin tone, hair colour and texture, eye colour, glasses, marks). The human check approves the sheet before any spread is generated. Every spread generation references this sheet (image reference / edit mode), never the raw photo alone.
2. **Set sheet.** One reference image per recurring set (the kitchen, the nursery interior, the bedroom, old and new home) generated before the spreads; reuse it.
3. **Anchor object sheet.** The title's anchor object drawn once; reused. For stateful anchors (the fridge list) generate one base image and add state (ticks) in code or with a controlled edit, so the state progression is deterministic: ticks 0 → 1 → 2 → 3 → 4 → 5 exactly on the spreads the story says.
4. **Carer signature item** (e.g. a yellow scarf, glasses) constant on every spread; carer face consistent via the same sheet method if a carer photo is supplied, else a generic adult kept identical.
5. **Outfit:** keep the child's outfit identical across the book unless the story requires a change (the concepts allow daily changes; constancy is safer for QA — RECOMMENDED constant).
6. **Lighting grades** (night scenes, underwater) are applied after face QA at full brightness; the realistic anchor (real pyjamas, real bed) stays in frame in imaginative scenes.
7. **One image per spread** at 5,055 × 2,575 px (or generated at the model's max and upscaled), composed so that no face crosses the gutter (centre 15 mm each side) and the calm zone sits on one page half.
8. **Cover art** generated from the same sheet, with the face as the largest element and a calm zone where the title will sit; back-cover art optional.
9. **Age:** the child is drawn at the age in the order (age band), never older or younger.

### 8.4 Per-title "must not show" (binding, from the concepts)

- 1 Big-Kid List: no deadline or countdown; no unhappy adult face at an accident; no child "ahead" of another; no reward chart, sticker or present; no nappy shaming; no toilet humour making the potty the joke. RECOMMENDED for Taoufik's decision: spread 11 (the wee that works) drawn from the waist up or with the potty partly hidden, since this is a real child's likeness.
- 2 Nursery Day: no crying child alone; no parent looking back sadly through the window; no "big boys/girls don't cry"; no late parent.
- 3 Bed Became a Boat: no monsters, no dark corners drawn scary, no parent sleeping in the child's bed, no child climbing out to the parents' room, no threats.
- 4 Night Rocket: no scary aliens, no being lost, no fire or crash, no real space-agency logos; helmet visor never covers the face.
- 5 Lost Dinosaur Egg: no predators, no fights, no chase; gentle dinosaurs only; Pip's design locked on spread 2.
- 6 Ocean Map: no drowning peril, no attacking sharks, nobody stuck in distress; mask off or transparent.
- 7 Tells the Truth: no punishment, no angry face, no time-out, no lying shown succeeding; the "hide" path is a thought bubble only.
- 8 Worry Jar: no catastrophe drawn (the worry never comes true on the page); no "don't be silly" adult; no diagnosis words; stones never look like faces.
- 9 New Front Door: no crying alone, no lost pet, no arguments, no "you'll forget your old friends".
- 10 Too-Good-To-Be-True: no deepfakes of real people, no scams, no adults losing money, no named apps or platforms; the child never uses an AI image tool; screen content is simple art with no real interface.

### 8.5 Likeness test (gate before production, OPEN until run)

3 children × 4 spreads × 3 raters per route, scored blind "is this the same child as the photo?"; pass = 33/36 per route. Owner: Book Factory. Run on fictional/consented children only.

---

## 9. "Words for the real moment" spread (pages 30–31, LOCKED)

Heading, Noto Serif SemiBold: **WORDS FOR THE REAL MOMENT**. Four blocks in this order, about 80 words in total. Body in Nunito 12 pt (the brand scale's "parent page 12 pt"; the same size on both routes), block labels in DM Mono Medium capitals:

| Block | What it holds | Example (nursery title) |
|---|---|---|
| YOU COULD SAY | One or two sentences the adult can say word for word, in the story's own vocabulary | "We can feel excited and unsure at the same time. I will come back after nursery." |
| TRY TOGETHER | One question to ask the child | "Who could you ask if you needed help?" |
| IN REAL LIFE | One small action for the real day | Choose a simple goodbye phrase to practise together. |
| READ IT AGAIN | Fixed line, identical in every title | Read this story a few nights in a row, and once more just before the day itself. |

Rules: no outcome claims ("this will help them settle"), no timelines, no deficit words about the child, no diagnosis words. Event titles (potty, nursery, big bed, new home): the action points to the real day. Adventure titles: one light question only ("What would you have packed for Pip?") — the other blocks are short or omitted per Taoufik. Worry and honesty titles are reviewed by someone with early-years experience. The spread is approved with the story and passed to the Factory as LOCKED text: the humanize pass never edits it. Readiness signs, "see your GP" lines and school advice (as in the concept parent-script pages) live in the product page/FAQ or a separate parent note if Taoufik wants them — the 80-word spread stays short.

Layout: calm art or a plain paper ground with the anchor object as a small spot illustration; text never over a busy image.

---

## 10. Title page (p. 1) and dedication page (p. 32)

**Page 1 (recto):** family band at the top (same spec as the cover band); full title in Noto Serif SemiBold ("[Name] and the" on its own line); the line "Illustrated from [Name]'s photo" in Nunito; one small spot illustration (the anchor object or the child); paper ground. No "starring".

**Page 32 (verso, last page):** dedication in Nunito — the giver's own words if supplied, else "For [Name], from [giver]"; a read-it-again note (the fixed line, may repeat); the one-ink logo small; the order code in DM Mono 9 pt in the lower corner inside the safe zone; raisedready.co.uk. Where the batch code sits in the trim area — printer to confirm.

---

## 11. Story text rules

### 11.1 Structure (LOCKED)
- 14 spreads, one scene per spread, in the order of the approved story concept's page beats. The last spread ends on an action or image, with the concept's approved last line; no stated moral.
- Fixed back matter: pages 30–31 and 32 as above.
- The child's name spelled exactly as entered, every time; pronouns as chosen by the parent.
- Text is composed in code over the art and stays live vector text in the PDF.

### 11.2 Length (brand doc values; guard-check caps in brackets)

| Age band of the TITLE | Words per spread | Sentence length |
|---|---|---|
| 2–3½ | 20–45 [cap 45] | 4–12 words, present tense, one sentence per line |
| 3–6 | 30–50 [cap 50] | 4–12 words, present tense |
| 5–8 | 50–70 [cap 70] | up to 18 words; keep the draft's tense |
| 7–10 | 70–90 [cap 90] | up to 18 words |

The humanize spec (26 Oct) set tighter caps of 40 (2–5) and 70 (5–10). OPEN: Taoufik picks one set; store caps in config, not code. Readability: Flesch–Kincaid grade ≤ 2.0 for 2–5 titles, ≤ 4.0 for 5–10, and never higher than the draft's.

### 11.3 Voice (story register)
Short sentences, sound and rhythm, the child always acting. Concrete nouns and verbs a child can picture ("the cold tap", "the blue potty by the door"). At most one adjective per noun. Sound words welcome ("splash", "click", "shhh"). A short refrain may repeat across spreads (the three script lines in title 1: "It's your turn to choose." "Sitting counts." "Oops is fine."). One rhetorical question to the reader per book at most (2–3 band: the repeated line appears ≥ 4 times; older bands: one question every three spreads is allowed by the concepts — OPEN, reconcile). No rhyme unless the whole draft is rhymed.

### 11.4 Tells to cut (humanize pass)
Opening or closing a spread with a summary of the feeling ("It was a big day", "she felt so proud"); a moral stated as a sentence on the last spread; stacked adjectives; "little did she know", "suddenly", "a wave of", "couldn't help but", "with a twinkle", "magical", "journey", "adventure awaits", "brave little", "special"; every character smiling/giggling/beaming; perfect problem → one try → success symmetry (let one try be clumsy).

### 11.5 Banned phrases (guard check returns zero hits, case-insensitive): fix, fixes, cure, cures, stop(s) your, train(s/ed) (as in toilet-trained), "by [month]", "in 3 days", "dry by", behind, delayed, struggling (about the child), meltdown, naughty, magical, "little did", suddenly, "couldn't help but", journey, "no more nappies", "ready for September", "sleeps through the night", "stays in bed", "no more tears", "ends separation anxiety", "stops hitting", "cures anxiety", "no more worries", "stress-free", "protects your child from misinformation", any number of days, "Printed in the UK" (until the flag is on), "starring", "hand-painted", AI-tool brand names, real space-agency or app names.

### 11.6 Claim rules (binding, from never-say)
No toilet-training dates or timelines. Never say or imply a child is behind or has a problem. Never claim a book fixes, cures or stops a behaviour. Never assume mum (the carer is whoever the parent names; "Mum" only when the order says so). Never issue or interpret a religious ruling. No invented reviews or counts anywhere. Only title 7 may reference evidence (Lee 2014) and only on the product page, not in the story.

### 11.7 Humanize pass contract (26 Oct spec, proposed — behind flag `HUMANIZE_PASS`, default off until the 3-story blind test passes)
Input JSON: `book_id, age_band ("2-5"|"5-10"), family, child_name, locked {front_band, back_question, parent_spread, dedication}, spreads[{n, text}]`. Only `spreads[].text` is editable; everything under `locked` is echoed byte-identical. Guard checks after every pass (code, no LLM): spread count and `n` sequence unchanged; locked fields identical; child name at least as frequent and spelled identically; per-spread word count within ±20 % of the draft and under the cap; banned-phrase list zero hits; UK-spelling dictionary (color→colour, diaper→nappy, mom→mum, pacifier→dummy, candy→sweets …); no new proper nouns vs the draft; readability thresholds. Fail → retry once → fall back to the draft and flag "humanize skipped" for the human reader. Optional tell-score judge (small model, temp 0, pass ≥ 8). Prompt versioned (`humanize_v1`), version stored on the book record.

---

## 12. Personalisation slots

**Standard in every title:** child's name; the photo (face, skin, hair); pronoun; one carer (name + role word, e.g. "Mum", "Dad", "Nana", "Grandad"); home type (flat or house); one comfort object named by the parent; dedication text from the giver.

**Per title (from the concepts):** 1 potty — school/nursery name, optional sibling/friend, potty vs toilet seat, underwear colour/pattern (spreads 11–14). 2 nursery — second carer for pick-up, nursery and teacher names, goodbye ritual (6 drawn variants or described), peg symbol, anchor event for the return, climate variant, transport. 3 big bed — bed type, night-light yes/no, door open/closed, curtain colour, pyjama colour, the exit line in the parent's words, sibling in room, routine order (5 icons re-orderable). 4 rocket — initial on the rocket, city skyline set, pyjama colour. 5 dinosaur — garden vs park, Pip's name, family song title, snack. 6 ocean — beach vs bathtub opening, swimsuit colour, turtle's name, optional second diver. 7 truth — the broken object, the praise words, sibling/friend, school name. 8 worry — school name, up to three worries (vetted list or moderated free text), calm-down move, jar design. 9 new home — carers, siblings, pets, old and new home type, transport, new place name, first-night box items, old front-door photo. 10 picture — school, two friends' names, city and landmark, tablet vs computer, the real surprising animal fact.

Free-text inputs (worries, dedication, praise words, exit line) pass moderation; a worry suggesting harm or crisis pauses the order for a human. Nothing a parent types is ever used to train anything.

---

## 13. QA gates (every book, before the preview goes to the parent)

1. **Structure check (code):** 32 interior pages; page map as section 3; cover file matches the template; page size 218 × 218 mm at 300 ppi; fonts embedded, text live; order ID in file names and on p. 32.
2. **Text guard checks (code):** section 11.7 list; band and question form match the title table; "Words for the real moment" byte-identical to the approved text; UK spelling.
3. **Anchor-object check (human + code where the state is deterministic):** the anchor is present and identical on every spread it belongs to; stateful anchors progress exactly as the story says (e.g. the fridge list: 0 ticks on spread 1, 1 after spread 2, 2 after 3, 3 after 4, 4 after 5, 4 on spreads 6–11, 5 from spread 12; the Emma build of 26 Oct reset to 0 on five spreads — this is the failure the check exists for).
4. **Face check (human, the promise):** face shape, skin tone, eye colour, hair colour and texture, glasses or marks match the photo on every spread and the cover; the anchor matches; hands, fingers and eyes have no artefacts; no face across the gutter. One failed spread is redrawn (regenerated, never patched) before the preview.
5. **Read-aloud check (human, 30 s):** read spreads 1, 7 and 14 aloud — sounds like one person; no lesson sentence at the end; the name correct every time; nothing in the text that is not in the picture (e.g. "goes back to her blocks" with no blocks drawn).
6. **Procedure check (title 5 dentist/doctor only, not in the GB launch):** spreads match the selected procedure.
7. **Preview to the parent;** up to 2 redraw rounds; approval recorded; then print file to the printer.

"Checked by a person before print" may only be printed while every book really gets check 4.

---

## 14. Data and privacy rules the software must enforce (draft pack, 22 Oct; counsel to confirm the bracketed numbers)

- Only a parent or guardian uploads the photo; two required unticked ticks at upload (parental responsibility; AI-made + checked + not used to train + deleted after [X] days). The upload button stays inactive until both are ticked. Store who ticked, the exact text shown, and when.
- One photo; child's first name only; age band, not date of birth; no surname.
- The photo and generated images are used only to make that child's book. Never to train models; never in ads, social posts, ClickUp, Google Drive, Slack, email attachments or any AI chat. Test runs use fictional children unless written consent exists.
- Image provider: OpenAI API only. Send any available storage-off / zero-retention setting; record the ZDR or Modified Abuse Monitoring approval status; until approved, OpenAI may keep inputs up to 30 days.
- Order ID on every file; face check against the reference photo before preview and before print.
- Deletion jobs (automatic, logged weekly): original photo [7] days after the print file is sent, or at once on cancel/refund before print; abandoned uploads [24 h]; generated illustrations and print PDF [30] days after dispatch; preview link on approval or after [14] days; photos excluded from backups where possible; backups rolling [30] days.
- Preview emails carry a link, never an image attachment.
- Access to photo storage limited to named roles; encrypted at rest; host and country documented (the pack lists "where Book Factory stores photos during a job" as unknown — document it).
- Controller: Tweezgroup EU OÜ, Ahtri 12, 10151 Tallinn, Estonia, reg. no. 16260453. UK representative to be appointed. Privacy contact [privacy@raisedready.co.uk].

---

## 15. Open items the Factory must not decide (build as config, surface to the team)

| Item | Owner | Note |
|---|---|---|
| Printer: TJ Books vs Prodigi | Meda; Taoufik approves | Bleed, spine, cover template, PDF packaging, endpapers, batch code all follow |
| Bleed 3 mm vs 4 mm | Printer template | Brand says 4 mm; Emma build used 3 mm |
| Route for titles 4, 7, 9 | Set when the story is written | |
| Words-per-spread caps (brand table vs humanize spec) | Taoufik | |
| Text plate style (none / soft paper plate) | Taoufik | RECOMMENDED soft paper plate only when contrast fails |
| Spread 11 of title 1 (potty framing) | Taoufik | |
| Andika for 5–7 story text | Taoufik | Default Nunito |
| Humanize pass on/off | Taoufik after the 3-story blind test | Flag default off |
| Deletion periods [7], [30], [14], [24 h] | Taoufik + counsel | Config values |
| Tagline | GB ad test | Never under the logo until chosen |
| "Printed in the UK" line | After a UK printer contract | Flag default off |
| Title 7 name (Shopify vs brand book) | Taoufik | Factory takes the string from the order |

---

## 16. What the 26 Oct Emma build got wrong, and where in the code it comes from

1. Fridge-list anchor reset to zero ticks on PDF pp. 9, 11, 13, 21, 25 after ticks had been added — every page is an independent `images.edit` call from the character sheet (`illustrate.generate_page_image`); there is no anchor sheet or state schedule. 2. Dedication on page 2 — `pages.py` SYSTEM prompt says "Page 2 may be a dedication". 3. 28 single-page images instead of 14 spreads — `PAGE_IMAGE_SIZE = "1024x1024"`, one call per page. 4. Front cover: question form, Nunito title in a cream box, "starring Emma", no logo, blank spine, empty back — `pdf_build.build_wraparound_cover` (`cover_text += "\nstarring {child_name}"`, `spine = Image.new(... (70,110,100))`). 5. Bleed 3 mm — `constants.BLEED_MM = 3.0`. 6. Text rasterised, Helvetica in the font list — `overlay_text_on_image` draws with Pillow and saves JPEG; ReportLab only places images. 7. No "Words for the real moment" heading; placeholder dedication — no page-map code; the LLM wrote "For Emma, with love". 8. White speech-bubble boxes — `draw.rounded_rectangle(... fill=(255,250,240,225))`. 9. Soft print — 1024 px art at `quality="low"` upscaled 2.5× by LANCZOS. 10. Minor: text mentions blocks that are not drawn (no text-vs-art check); child drawn as 5 for a 2–3½ title (input error).
