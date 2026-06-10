#!/bin/bash
###############################################################################
# REVERT SCRIPT — SMTCD Trips API trip-entry / write feature  [TRIPENTRY-0610]
# (2026-06-10)
#
# Restores the Flask app (app.py, requirements.txt, trips.json) to its
# pre-change, read-only state. The "before" copies were saved at change time in:
#   _backup_tripentry_0610/
#
# What this undoes:
#   - SQLite-backed store (trips.db) + init/seed-from-trips.json on boot
#   - GET  /trip-entry  HTML data-entry page
#   - POST /trips       write endpoint (create a trip; JSON or form)
#   - /health + /riders reading from SQLite instead of in-memory trips.json
#
# After reverting, the API is back to serving trips.json read-only (GET only).
#
# Idempotent: safe to re-run. Also deletes the local trips.db so the next boot
# of the (restored) app reloads cleanly from trips.json.
#
# NOTE: this only reverts the LOCAL working copy. To revert what's running on
#       Render, commit + push the restored files (Render autoDeploy is on) or
#       redeploy manually. This script does NOT push.
#
# USAGE:  bash REVERT_TRIPENTRY_0610.sh
###############################################################################
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
BACKUP="$HERE/_backup_tripentry_0610"

echo "==> Reverting SMTCD Trips API trip-entry changes [TRIPENTRY-0610]"

if [ ! -d "$BACKUP" ]; then
  echo "ERROR: backup dir not found: $BACKUP"
  echo "Cannot revert — original files are unavailable."
  exit 1
fi

echo "--- Restoring backed-up source files"
cp "$BACKUP/app.py"          "$HERE/app.py"
cp "$BACKUP/requirements.txt" "$HERE/requirements.txt"
cp "$BACKUP/trips.json"      "$HERE/trips.json"
echo "    files restored from $BACKUP"

echo "--- Removing local SQLite database (will not exist in read-only app)"
rm -f "$HERE/trips.db"

echo "==> Revert complete. app.py is back to its read-only, trips.json-backed"
echo "    behavior. Commit + push (or redeploy) to apply the revert on Render."
