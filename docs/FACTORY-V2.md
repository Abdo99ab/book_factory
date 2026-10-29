# Raised Ready Book Factory v2

Built from **brand guidelines** + **TJ printer specs** + **locked launch stories** (see `brand/stories/00`). Old Magical/v1 code lives in `legacy_v1/`.

## Operator flow

1. **Create** — FINAL title only, child, carer, giver, home, comfort object, order id, photo.
2. **Content** — load the locked story JSON as written (placeholders only: `[Name]`, `[their]`, `[Carer]`, `[Giver]`…). **No AI manuscript, no UK-English pass, no humanizer.**
3. **Character sheet** — from the photo (reference for all art).
4. **Preview** — front cover + spreads 1–2 (art + text overlays) + logo on cover.
5. **Build** — remaining spreads, back cover, title/parent spots, full TJ PDF. Existing assets are never regenerated.
6. **Edit** — per spread or cover: instruction → regenerate **only that asset** → rebuild PDF from disk.

## Story docs (source of truth)

- `brand/stories/00 · Read first — …docx` — process rules for all ten titles.
- `brand/stories/01 · … FINAL CHECK (v2).docx` — **standard** for locked JSON; stories 02–10 APPROVED v2 / v2.1.
- Factory loads `brand/stories/<title_id>.json` for all ten launch titles. Re-import: `python scripts/import_locked_stories.py`.
- Logos: `assets/brand/B1b_*.png`. In-world lettering: Caveat on Ocean Map spread 1 banner only (Nunito fallback).

Each spread carries: **text** (print as-is), **art** (illustrator brief), **anchor**, **calm_zone** (`left`|`right`) + **calm_zone_note**.

## Calm zone

Not a separate image. One continuous landscape spread; the calm zone is the **low-detail area** (sky / lawn / canopy) where the story text plate is printed.

## Page map (locked)

| Pages | Content |
|---|---|
| Cover flat | Back \| spine \| front (TJ wrap) |
| 1 | Title page + spot from story |
| 2–29 | 14 story spreads |
| 30–31 | Words for the real moment (from story parent blocks) |
| 32 | Dedication + read-it-again + logo + order code |

## Printer (TJ overrides brand)

- Interior: 210² + **3 mm** bleed → 216² mm pages.
- Cover: 216 + **8** spine + 216 + **16** wrap each side.
- Single PDF, cover flat first, plain endpapers not in the 32.

## No upscaler

Native model pixels only. PPI is logged; not a hard stop in v2.
