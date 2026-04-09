#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
#  setup_cron.sh — Install a cron job to run main.py daily at 15:00 UTC
#
#  Usage:
#    chmod +x setup_cron.sh
#    ./setup_cron.sh
#
#  The cron job will run as the current user.
#  Logs are appended to logs/cron.log inside the project directory.
# ══════════════════════════════════════════════════════════════════

set -euo pipefail

# Resolve the absolute path to this script's directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$(which python3 || which python)"
LOG_FILE="${SCRIPT_DIR}/logs/cron.log"

# Ensure logs dir exists
mkdir -p "${SCRIPT_DIR}/logs"

CRON_CMD="0 15 * * * cd \"${SCRIPT_DIR}\" && \"${PYTHON}\" main.py >> \"${LOG_FILE}\" 2>&1"

echo "Installing cron job…"
echo "  Schedule : every day at 15:00 UTC"
echo "  Command  : python main.py"
echo "  Log file : ${LOG_FILE}"
echo ""

# Check for existing entry and avoid duplicates
EXISTING=$(crontab -l 2>/dev/null || true)

if echo "${EXISTING}" | grep -qF "main.py"; then
    echo "⚠️  A cron entry for main.py already exists:"
    echo "${EXISTING}" | grep "main.py"
    echo ""
    read -rp "Replace it? [y/N]: " CONFIRM
    if [[ "${CONFIRM,,}" != "y" ]]; then
        echo "Aborted."
        exit 0
    fi
    # Remove old entry
    EXISTING=$(echo "${EXISTING}" | grep -vF "main.py")
fi

# Install new cron entry
(echo "${EXISTING}"; echo "${CRON_CMD}") | crontab -

echo ""
echo "✅ Cron job installed successfully."
echo ""
echo "Current crontab:"
crontab -l
echo ""
echo "To remove the job later, run:  crontab -e"
