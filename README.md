# Raised Ready Book Factory (v2)

Personalised children’s books from catalogue beats → TJ Books print PDF.

**Design:** [`docs/FACTORY-V2.md`](docs/FACTORY-V2.md)  
**Brand:** [`brand/Raised Ready — Brand Guidelines v1.md`](brand/Raised%20Ready%20—%20Brand%20Guidelines%20v1.md)  
**Stories:** ten locked JSONs under `brand/stories/` (from FINAL/APPROVED docx; re-import with `scripts/import_locked_stories.py`)  
**Printer:** [`brand/chosen_printer_specs.txt`](brand/chosen_printer_specs.txt)  
**Previous system:** [`legacy_v1/`](legacy_v1/)

## Flow

1. Create job (form + photo) → one-shot manuscript + character sheet  
2. **Preview** → front cover + spreads 1–2  
3. **Build** → remaining art + full PDF (never regenerates existing files)  
4. **Edit** → per spread/cover art or text; that asset only; PDF rebuilds when complete  

No validation/repair loops. No upscaler. Calm half alternates left/right.

## Run

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
# set _OPENAI_API_KEY in .env
python app.py
```

Open `http://127.0.0.1:5000/create`.
