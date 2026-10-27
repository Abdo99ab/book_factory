# Raised Ready Book Factory v2

Built from **brand guidelines** + **TJ printer specs** only. Old Magical/v1 code lives in `legacy_v1/`.

## Operator flow

1. **Create** — title, child, carer, home, comfort object, slots, order id, photo.
2. **Content** — one OpenAI text call: full manuscript from catalogue beats (exact events/order), UK English, humanized brand voice. No validation / repair loop.
3. **Character sheet** — from the photo (reference for all art).
4. **Preview** — front cover + spreads 1–2 (art + text overlays) + logo on cover.
5. **Build** — remaining spreads, back cover, title spot, full TJ PDF. Existing assets are never regenerated.
6. **Edit** — per spread or cover: instruction → regenerate **only that asset** → rebuild PDF from disk. Instant for that piece.

## Page map (locked)

| Pages | Content |
|---|---|
| Cover flat | Back \| spine \| front (TJ wrap) |
| 1 | Title page |
| 2–29 | 14 story spreads (one continuous landscape art each, split in PDF) |
| 30–31 | Words for the real moment (paper, no soft text plate) |
| 32 | Dedication + read-it-again + logo + order code |

## Spread art rule

- One continuous double-page image (~2:1).
- **Calm half** alternates: spread 1 left, 2 right, 3 left… — background / texture only, no objects.
- Action / child on the other half. No face on the gutter.
- Story text overlaid on the calm half with a soft white plate.
- Front cover title: no white plate (calm art zone). Parent pages: no plate (paper ground).

## Printer (TJ overrides brand)

- Interior: 210² + **3 mm** bleed → 216² mm pages.
- Cover: 216 + **8** spine + 216 + **16** wrap each side.
- Single PDF, cover flat first, plain endpapers not in the 32.
- Back QR keep-clear: bottom-left inside the white copy plate.

## No upscaler

Native model pixels only. PPI is logged; not a hard stop in v2.
