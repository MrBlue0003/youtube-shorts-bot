#!/usr/bin/env python3
"""save_log.py — Safely push uploaded.json without git conflicts.

Problem: if workflow is re-triggered manually while another run is in progress,
both try to push uploaded.json at the same time → conflict.

Solution: fetch remote → merge JSON arrays → reset to remote HEAD →
commit merged file → push. Retry up to 5× with back-off.
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

LOG       = Path("logs/uploaded.json")
PLAYLISTS = Path("data/playlists.json")
_KEEP_DAYS = 90


def git(*args, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        print(f"git {' '.join(args)} failed:\n{r.stderr}", file=sys.stderr)
        raise SystemExit(1)
    return r


def main() -> int:
    if not LOG.exists():
        print("No upload log found, nothing to save")
        return 0

    with open(LOG, encoding="utf-8") as f:
        local_data = json.load(f)

    local_uploads = local_data.get("uploads", [])
    if not local_uploads:
        print("Upload log is empty, nothing to save")
        return 0

    our_entry = local_uploads[-1]
    our_id    = our_entry.get("video_id", "")
    print(f"Saving entry: {our_id} — {our_entry.get('title', '')[:60]}")

    for attempt in range(1, 6):
        git("fetch", "origin", "main")

        # Read remote uploaded.json
        r = git("show", "origin/main:logs/uploaded.json", check=False)
        if r.returncode == 0:
            try:
                remote_uploads = json.loads(r.stdout).get("uploads", [])
            except json.JSONDecodeError:
                remote_uploads = []
        else:
            remote_uploads = []

        # Already pushed by another run?
        if our_id and our_id in {u.get("video_id") for u in remote_uploads}:
            print("Entry already in remote log — no push needed")
            return 0

        # Merge + trim to 90 days
        cutoff  = datetime.now(timezone.utc) - timedelta(days=_KEEP_DAYS)
        combined = remote_uploads + [our_entry]
        combined = [
            u for u in combined
            if datetime.fromisoformat(
                u.get("timestamp", "2000-01-01T00:00:00+00:00")
            ) > cutoff
        ]

        with open(LOG, "w", encoding="utf-8") as f:
            json.dump({"uploads": combined}, f, indent=2)

        git("reset", "--soft", "origin/main")
        git("add", str(LOG))
        if PLAYLISTS.exists():
            git("add", str(PLAYLISTS))

        diff = git("diff", "--staged", "--quiet", check=False)
        if diff.returncode == 0:
            print("Nothing staged — already up to date")
            return 0

        git("commit", "-m", "chore: update upload log [skip ci]")

        push = git("push", check=False)
        if push.returncode == 0:
            print(f"Upload log pushed (attempt {attempt})")
            return 0

        wait = attempt * 3
        print(f"Push failed (attempt {attempt}/5) — retrying in {wait}s …")
        time.sleep(wait)

    print("ERROR: could not push upload log after 5 attempts")
    return 1


if __name__ == "__main__":
    sys.exit(main())
