#!/usr/bin/env python3
"""Apply additive schema migrations; back up an existing SQLite DB first."""
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
from db import engine
import models  # Register all tables with Base.metadata.
from services.schema import initialize_database

if __name__ == "__main__":
    if engine.dialect.name == "sqlite" and engine.url.database not in (None, ":memory:"):
        database = Path(engine.url.database).resolve()
        if database.exists():
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = database.with_name(f"{database.stem}.backup-{timestamp}.db")
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source:
                with sqlite3.connect(backup) as target:
                    source.backup(target)
            print(f"SQLite backup: {backup}")
    initialize_database(engine)
    print("Additive Pantry Keeper migrations completed.")
