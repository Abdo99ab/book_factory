# Book Factory

Internal A-to-Z studio: take a **drafted story** + **one child photo**, rebuild the manuscript for a Magical Children’s Book–style **theme**, illustrate **square 210×210 mm** pages (art without text), **overlay** story copy, assemble a **TJ wraparound** print cover, and download a single-page PDF.

Every pipeline step and every natural-language edit is logged. Durable preferences are extracted into memory and injected into future runs.

## Themes (v1)

Adventure · Fairy Tales · Activities · Worlds · Holidays · Family · Educational · Feelings

Plus purpose: `educational` | `entertaining` | `both`.

## Pipeline

1. **Character** — photo → locked hero sheet (`gpt-image-2.5-flare` edit)
2. **Story rebuild** — draft + theme + purpose + age → title, moral, outfit lock, rebuilt prose  
   (no invented mascots/toys unless in the draft)
3. **Humanize story** — `blader/humanizer` via OpenAI Skills API (`POST /v1/skills`, then Responses shell mount) rewrites the manuscript so it reads less AI-generated
4. **Page compile** — **32** content pages with scene, text, text placement (no letters in art); page overlay text is humanized the same way
5. **Optional preview** — first **3 content pages only** (no cover). Review, then continue.
6. **Validate** — structural + AI QA on story/pages/prompts; auto-repair once; block build if still failing
7. **Cover panels** — front + back art (no typography in pixels)
8. **Illustrate remaining pages** — skips any preview pages already on disk
9. **PDF** — overlay text panels, stitch wraparound cover + interiors
10. **NL changes** — queueable requests with live action progress → log → learn preferences
11. **Self-revise** — after the PDF is built, vision-QA reviews every composited page and proposes a plan (text-over-face, composition, face drift). You edit / add / remove actions, then click **Apply approved plan**. **Self revise** / **Re-run review** regenerates a new plan anytime.
12. **Templates** — on a finished job, **Make template** snapshots the approved story/pages (no art). **Reuse** asks only for a new child photo + name (age/gender/font), swaps the hero name in the manuscript, then regenerates character, cover, pages, and PDF.

On create, the job always **prepares content first** and stops for review. Edit supporting characters, outfit, and page briefs, save, then choose **Preview 3 pages** or **Validate & build**. After a preview, **Validate & build** keeps unchanged pages.

## Setup

```text
cd book_factory
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Set `_OPENAI_API_KEY` and `FLASK_SECRET_KEY` in `.env`.

```text
flask --app app run --debug --port 5001
```

Open `http://127.0.0.1:5001`.

## Job layout

```text
book_assets/jobs/<job_id>/
  inputs.json
  job.json
  logs/events.jsonl
  character/reference.*  character.png
  story/rebuilt_story.json  pages.json
  cover/front_art.png  back_art.png
  pages/page-01/art.png
  print/cover.jpg  pages/*.jpg
  final_book.pdf

book_assets/templates/<template_id>/
  template.json
  inputs_base.json
  story/rebuilt_story.json  pages.json
```

Global prefs: `data/preferences/global.json`.

## Print spec (TJ)

- **Inner pages:** 210 × 210 mm trim + **3 mm** bleed (PDF page = 216 × 216 mm). Single pages, no colour profile.
- **Content count:** 32 pages on **170 gsm** (locks the **8 mm** spine). Plain **endpapers** are added in the PDF and are **not** counted in the 32.
- **Cover flat:** 16 mm wrap + 216 back + 8 mm spine + 216 front + 16 mm wrap (height 16 + 216 + 16).
- **Back cover:** keep **bottom-right** clear (~28 mm) for TJ QR / batch code (exact corner/size pending Meda).

## Humanizer (blader/humanizer)

After story rebuild (and again on page texts), Book Factory runs [blader/humanizer](https://github.com/blader/humanizer):

1. Uploads `skills/humanizer/` with `POST /v1/skills` (cached under `data/skills/humanizer_skill.json`, or set `HUMANIZER_SKILL_ID`)
2. Calls Responses API with hosted shell + `skill_reference` in **embedded mode** (final prose only)
3. If Skills/container mount fails, falls back to the same `SKILL.md` as chat instructions

Toggle with `HUMANIZE_STORY=0` or `HUMANIZE_VIA_SKILLS_API=0` in `.env`. Pre-humanize manuscript is kept as `story/rebuilt_story.ai.txt`.

## Notes

- English only in v1.
- Text is **not** painted by the image model; Pillow overlays it, then ReportLab builds the PDF.
- Story text fonts: pick one of 8 styles on create (or re-apply on a finished job). TTFs live in `assets/fonts/`.
- OpenAI stack: `gpt-image-2.5-flare` (images), `gpt-6-luna` (planning / humanize).
