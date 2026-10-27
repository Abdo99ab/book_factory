# Raised Ready brand fonts (locked 22 Oct 2026)

Drop this `assets/fonts/` folder into book_factory. File names match `pipeline/brand.py` FONT_FILES.

| File | Role | Source |
|---|---|---|
| NotoSerif-SemiBold.ttf | Titles, cover, spine, headings | Google Fonts Noto Serif, static instance wght 600 / wdth 100 cut from the variable font with fontTools 4.66 |
| Nunito-Regular.ttf | Story text, blurb, parent page body | Google Fonts Nunito, static instance wght 400 |
| Nunito-SemiBold.ttf | Emphasis in body text | Google Fonts Nunito, static instance wght 600 |
| DMMono-Medium.ttf | Bands, labels, order code | Google Fonts DM Mono Medium (static file as published) |

All four are SIL Open Font License 1.1 (OFL-*.txt), free for print and web embedding.
Verified: each registers with ReportLab `pdfmetrics.registerFont(TTFont(...))`; none is a variable font (no fvar table), so the weight embedded in the PDF is the one named.
ReportLab names to register: NotoSerif-SemiBold, Nunito-Regular, Nunito-SemiBold, DMMono-Medium.
