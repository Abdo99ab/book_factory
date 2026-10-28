#!/usr/bin/env python3
"""Generate or approve a recurring story character sheet (no customer data).

Examples:
  python scripts/generate_story_character.py dinosaur_egg pip
  python scripts/generate_story_character.py dinosaur_egg pip --approve "Aymen"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from pipeline.openai_util import create_client
from pipeline.story_characters import approve_character_sheet, generate_character_sheet, list_story_characters


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("title_id")
    parser.add_argument("character_id", nargs="?", help="Omit to list characters")
    parser.add_argument("--approve", metavar="WHO", help="Mark the existing sheet approved")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list or not args.character_id:
        for entry in list_story_characters(args.title_id):
            flag = "OK" if entry.get("approved") else "NEED APPROVAL"
            print(f"{entry.get('id')}: {entry.get('name')} [{flag}] → {entry.get('sheet')}")
        return 0

    if args.approve:
        entry = approve_character_sheet(args.title_id, args.character_id, args.approve)
        print(f"Approved {entry['id']} by {entry['approved_by']} at {entry['approved_at']}")
        return 0

    client = create_client()
    path = generate_character_sheet(client, args.title_id, args.character_id)
    print(f"Wrote {path} (approved=false — review then --approve)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
