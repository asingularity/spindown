#!/usr/bin/env bash
#
# End-to-end demo: creates a virtual drive and runs hdtool against it.
# Usage: ./run_demo.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

SOURCE="/tmp/hdtool-demo-source"
DEST="/tmp/hdtool-demo-dest"

echo "=== Hard Drive Backup Tool Demo ==="
echo ""

# Step 1: Create virtual drive
echo "Step 1: Creating virtual drive..."
bash "$SCRIPT_DIR/create_virtual_drive.sh" "$SOURCE"

# Step 2: Clean destination
echo ""
echo "Step 2: Preparing destination..."
rm -rf "$DEST"
mkdir -p "$DEST"

# Step 3: Run hdtool
echo ""
echo "Step 3: Running hdtool..."
echo "  Source: $SOURCE"
echo "  Dest:   $DEST"
echo ""

cd "$PROJECT_DIR"
python -m hdtool --source "$SOURCE" --dest "$DEST"

echo ""
echo "=== Demo complete ==="
echo "Backup destination: $DEST"
echo ""
echo "To verify timestamps were preserved:"
echo "  ls -la $SOURCE/Documents/Work/"
echo "  ls -la $DEST/Documents/Work/"
echo ""
echo "To test resume, run again:"
echo "  python -m hdtool --source $SOURCE --dest $DEST"
