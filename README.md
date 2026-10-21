# Book Factory

Internal A-to-Z studio: take a **drafted story** + **one child photo**, rebuild the manuscript for a Magical Children’s Book–style **theme**, illustrate portrait **3:4** pages (art without text), **overlay** story copy, assemble a **wraparound** print cover, and download a PDF.

Every pipeline step and every natural-language edit is logged. Durable preferences are extracted into memory and injected into future runs.

## Themes (v1)

Adventure · Fairy Tales · Activities · Worlds · Holidays · Family · Educational · Feelings

Plus purpose: `educational` | `entertaining` | `both`.

## Pipeline

1. **Character** — photo → locked hero sheet (`gpt-image-2` edit)
2. **Story rebuild** — draft + theme + purpose + age → title, moral, outfit lock, rebuilt prose  
   (no invented mascots/toys unless in the draft)
3. **Page compile** — 24–30 pages with scene, text, text placement (no letters in art)
4. **Optional preview** — first **3 content pages only** (no cover). Review, then continue.
5. **Validate** — structural + AI QA on story/pages/prompts; auto-repair once; block build if still failing
6. **Cover panels** — front + back art (no typography in pixels)
7. **Illustrate remaining pages** — skips any preview pages already on disk
8. **PDF** — overlay text panels, stitch wraparound cover + interiors
9. **NL changes** — queueable requests with live action progress → log → learn preferences
10. **Self-revise** — after the PDF is built, vision-QA reviews every composited page and proposes a plan (text-over-face, composition, face drift). You edit / add / remove actions, then click **Apply approved plan**. **Self revise** / **Re-run review** regenerates a new plan anytime.

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
```

Global prefs: `data/preferences/global.json`.

## Notes

- English only in v1.
- Text is **not** painted by the image model; Pillow overlays it, then ReportLab builds the PDF.
- Story text fonts: pick one of 8 styles on create (or re-apply on a finished job). TTFs live in `assets/fonts/`.
- Same OpenAI stack as `full_book_pdf` (`_OPENAI_API_KEY`, `gpt-image-2`, chat model for planning).
