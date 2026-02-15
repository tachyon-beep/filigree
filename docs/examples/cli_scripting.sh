#!/usr/bin/env bash
# =============================================================================
# CLI Scripting with Filigree
# =============================================================================
#
# This script demonstrates how to use filigree's CLI with --json output
# for scripting and automation. It shows:
#
#   - Initializing a project from scratch
#   - Creating issues and capturing their IDs with jq
#   - Listing and filtering issues
#   - Querying project stats
#   - Session resumption pattern (saving timestamps for incremental changes)
#   - Cleaning up temporary state
#
# Prerequisites:
#   - filigree (pip install filigree)
#   - jq (https://jqlang.github.io/jq/)
#
# How to run:
#   bash docs/examples/cli_scripting.sh
#
# =============================================================================

set -euo pipefail

# --- Prerequisites check ---

if ! command -v filigree &>/dev/null; then
    echo "ERROR: 'filigree' not found. Install with: pip install filigree"
    exit 1
fi

if ! command -v jq &>/dev/null; then
    echo "ERROR: 'jq' not found. Install with: apt install jq / brew install jq"
    exit 1
fi

echo "=== Filigree CLI Scripting Demo ==="
echo ""

# --- Set up a temporary project ---

TMPDIR=$(mktemp -d -t filigree_cli_demo_XXXX)
trap 'rm -rf "$TMPDIR"' EXIT

cd "$TMPDIR"
filigree init --prefix demo
echo "Initialized project in: $TMPDIR"
echo ""

# --- Create issues and capture IDs ---

echo "--- Creating Issues ---"

# Create issues with --json and extract the ID using jq
TASK_ID=$(filigree create "Set up database schema" --type=task --priority=1 --json | jq -r '.id')
echo "Created task: $TASK_ID"

BUG_ID=$(filigree create "Fix null pointer on login" --type=bug --priority=0 --json | jq -r '.id')
echo "Created bug:  $BUG_ID"

FEAT_ID=$(filigree create "Add dark mode support" --type=feature --priority=2 --json | jq -r '.id')
echo "Created feature: $FEAT_ID"

# Create a task that depends on the first one
DEP_ID=$(filigree create "Write migration scripts" --type=task --priority=1 --json | jq -r '.id')
filigree add-dep "$DEP_ID" "$TASK_ID"
echo "Created task: $DEP_ID (depends on $TASK_ID)"
echo ""

# --- List and filter ---

echo "--- Listing Issues ---"

echo "All open issues (sorted by priority):"
filigree list --status=open --json | jq -r '.[] | "  P\(.priority) [\(.id)] \(.type): \(.title)"'
echo ""

echo "Ready issues (no blockers):"
filigree ready --json | jq -r '.[] | "  P\(.priority) [\(.id)] \(.title)"'
echo ""

echo "Blocked issues:"
filigree blocked --json | jq -r '.[] | "  [\(.id)] \(.title) -- blocked by: \(.blocked_by | join(", "))"'
echo ""

# --- Stats ---

echo "--- Project Stats ---"
filigree stats --json | jq '{
    total_issues: (.by_status | to_entries | map(.value) | add),
    by_type: .by_type,
    ready: .ready,
    blocked: .blocked
}'
echo ""

# --- Work on issues ---

echo "--- Working Through Issues ---"

# Complete the database schema task
filigree update "$TASK_ID" --status=in_progress --actor=dev-1
filigree add-comment "$TASK_ID" "Schema designed and reviewed. Creating tables."
filigree close "$TASK_ID" --reason="Schema deployed to staging" --actor=dev-1
echo "Closed: $TASK_ID"

# Now the dependent task should be unblocked
echo ""
echo "Ready after closing dependency:"
filigree ready --json | jq -r '.[] | "  P\(.priority) [\(.id)] \(.title)"'
echo ""

# --- Session Resumption Pattern ---
# Save a timestamp, do more work, then retrieve only what changed.

echo "--- Session Resumption Pattern ---"

# Save the current time as a resumption point
CHECKPOINT=$(date -u +"%Y-%m-%dT%H:%M:%S")
echo "Checkpoint saved: $CHECKPOINT"

# Simulate more work happening after the checkpoint
sleep 1
filigree update "$BUG_ID" --status=confirmed --fields='{"severity":"critical"}' --actor=triager
filigree update "$BUG_ID" --status=fixing --fields='{"root_cause":"Missing null check"}' --actor=dev-2
filigree close "$BUG_ID" --reason="Null check added and tested" --actor=dev-2

echo ""
echo "Changes since checkpoint:"
filigree changes --since "$CHECKPOINT" --json | jq -r '.[] | "  \(.created_at | split(".")[0]) \(.event_type): \(.issue_id)"'
echo ""

# --- Final state ---

echo "--- Final State ---"
filigree list --json | jq -r '.[] | "  [\(.id)] \(.status) P\(.priority) \(.type): \(.title)"'
echo ""

filigree stats --json | jq '.by_category'
echo ""

echo "Demo complete. Temp project cleaned up on exit."
