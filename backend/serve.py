#!/usr/bin/env python3
"""Bootstrap the seed score and run the API.

Usage: python -m backend.serve
Imports data/seed_musicxml/seed.xml as score id "seed" on first run, then
serves the API (and, in development, the Vite-built static frontend if present).
"""
from __future__ import annotations

import os
import uvicorn

from .musicxml_io import import_musicxml
from .store import Store

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data", "scores")
SEED_XML = os.path.join(HERE, "data", "seed_musicxml", "seed.xml")
SEED_ID = "seed"


def bootstrap():
    store = Store(DATA)
    if not store.exists(SEED_ID):
        xml = open(SEED_XML).read()
        score = import_musicxml(xml, score_id=SEED_ID)
        score.title = "Triplet + cross-bar G4 (4/4)"
        store.create(score)
        print(f"seeded score '{SEED_ID}'")
    else:
        print(f"score '{SEED_ID}' already present")


def main():
    bootstrap()
    uvicorn.run("backend.app:app", host="0.0.0.0",
                port=int(os.environ.get("PORT", 8000)), reload=False)


if __name__ == "__main__":
    main()
