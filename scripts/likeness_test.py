"""Likeness test stub (spec 8.1: the two routes must pass a likeness test before launch).

Usage:  python scripts/likeness_test.py <reference_photo> <generated_page_or_sheet> [more pages...]

STUB: prints the checklist a human (or the vision judge, once wired) must answer for each image.
It does not call any model and writes nothing. Wire `judge()` to the vision QA when the likeness
test protocol (Taoufik, OPEN) is agreed.
"""

from __future__ import annotations

import sys
from pathlib import Path

CHECKLIST = (
    "Hair colour, length and texture match the photo",
    "Skin tone matches the photo (not lightened or darkened)",
    "Eye colour and shape match",
    "Glasses / marks / birthmarks match",
    "Face shape and expression read as the same child",
)


def judge(reference: Path, candidate: Path) -> dict:
    """TODO: call the vision judge. Returns {item: None} (unanswered) for now."""
    return {item: None for item in CHECKLIST}


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    reference = Path(argv[1])
    if not reference.is_file():
        print(f"Reference photo not found: {reference}")
        return 2
    for candidate in map(Path, argv[2:]):
        if not candidate.is_file():
            print(f"Missing: {candidate}")
            return 2
        print(f"\n{candidate.name} vs {reference.name}")
        for item, answer in judge(reference, candidate).items():
            print(f"  [{'?' if answer is None else ('x' if answer else ' ')}] {item}")
    print("\nSTUB: nothing was scored. Answer each item by eye until the judge is wired.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
