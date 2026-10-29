# Raised Ready brand fonts (locked 22 Oct 2026)

Drop this `assets/fonts/` folder into book_factory. File names match `pipeline/brand.py` FONT_FILES.

| File | Role | Source |
|---|---|---|
| NotoSerif-SemiBold.ttf | Titles, cover, spine, headings | Google Fonts Noto Serif, static instance wght 600 / wdth 100 cut from the variable font with fontTools 4.66 |
| Nunito-Regular.ttf | Story text, blurb, parent page body | Google Fonts Nunito, static instance wght 400 |
| Nunito-SemiBold.ttf | Emphasis in body text | Google Fonts Nunito, static instance wght 600 |
| DMMono-Medium.ttf | Bands, labels, order code | Google Fonts DM Mono Medium (static file as published) |
| Caveat/static/Caveat-Regular.ttf | **In-world lettering only** (Ocean Map banner name) | Google Fonts Caveat, OFL — never story text, titles, bands, covers, or ads |

All are SIL Open Font License 1.1, free for print and commercial use.
Verified: each registers with ReportLab `pdfmetrics.registerFont(TTFont(...))` via static TTFs (no variable/`fvar` files in the live stack).
If Caveat looks pasted-on when curved to a banner, fall back to Nunito for that overlay only.
