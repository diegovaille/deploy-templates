#!/usr/bin/env python3
"""Exit nonzero when the live hourly backup is older than two hours."""
import json
from datetime import datetime, timezone
from pathlib import Path

report = json.loads(Path('/var/lib/primeira-backup/last-success.json').read_text())
age = (datetime.now(timezone.utc) - datetime.fromisoformat(report['completed_at'])).total_seconds()
if report['source'] != 'live-database' or age < 0 or age > 7200:
    raise SystemExit('Primeira hourly backup missing or stale; inspect primeira-backup.service')
print('Primeira hourly backup freshness OK')
