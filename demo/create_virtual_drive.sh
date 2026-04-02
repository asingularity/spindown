#!/usr/bin/env bash
#
# Creates a realistic virtual drive directory structure for testing hdtool.
# Usage: ./create_virtual_drive.sh [target_dir]
#
set -euo pipefail

TARGET="${1:-/tmp/hdtool-demo-source}"

echo "Creating virtual drive at: $TARGET"
rm -rf "$TARGET"
mkdir -p "$TARGET"

# --- Documents ---
mkdir -p "$TARGET/Documents/Work"
echo "Q3 revenue report draft" > "$TARGET/Documents/Work/Q3 Report.txt"
echo "Meeting notes from Tuesday" > "$TARGET/Documents/Work/meeting-notes.txt"
dd if=/dev/urandom of="$TARGET/Documents/Work/spreadsheet.xlsx" bs=1024 count=50 2>/dev/null

mkdir -p "$TARGET/Documents/Personal"
echo "Dear diary..." > "$TARGET/Documents/Personal/journal.txt"
echo "Groceries: milk, eggs, bread" > "$TARGET/Documents/Personal/shopping-list.txt"

# --- Photos ---
mkdir -p "$TARGET/Photos/2020-vacation"
for i in $(seq 1 5); do
    dd if=/dev/urandom of="$TARGET/Photos/2020-vacation/IMG_${i}.jpg" bs=1024 count=$((100 + i * 20)) 2>/dev/null
done

mkdir -p "$TARGET/Photos/2021-holidays"
for i in $(seq 1 3); do
    dd if=/dev/urandom of="$TARGET/Photos/2021-holidays/DSC_${i}.raw" bs=1024 count=$((200 + i * 50)) 2>/dev/null
done

mkdir -p "$TARGET/Photos/Thumbnails"
for i in $(seq 1 8); do
    dd if=/dev/urandom of="$TARGET/Photos/Thumbnails/thumb_${i}.jpg" bs=256 count=1 2>/dev/null
done

# --- Music ---
mkdir -p "$TARGET/Music/Rock"
dd if=/dev/urandom of="$TARGET/Music/Rock/song1.mp3" bs=1024 count=300 2>/dev/null
dd if=/dev/urandom of="$TARGET/Music/Rock/song2.mp3" bs=1024 count=250 2>/dev/null

mkdir -p "$TARGET/Music/Jazz"
dd if=/dev/urandom of="$TARGET/Music/Jazz/track01.flac" bs=1024 count=500 2>/dev/null

# --- Code ---
mkdir -p "$TARGET/Code/my-project/src"
cat > "$TARGET/Code/my-project/src/main.py" << 'PYEOF'
#!/usr/bin/env python3
"""Sample project."""

def hello():
    print("Hello, world!")

if __name__ == "__main__":
    hello()
PYEOF

cat > "$TARGET/Code/my-project/README.md" << 'MDEOF'
# My Project

A sample project for demo purposes.
MDEOF

mkdir -p "$TARGET/Code/my-project/.git"
echo "ref: refs/heads/main" > "$TARGET/Code/my-project/.git/HEAD"

# --- Hidden files ---
echo "secret config" > "$TARGET/.config_file"
mkdir -p "$TARGET/.hidden_dir"
echo "hidden data" > "$TARGET/.hidden_dir/data.txt"

# --- Edge cases ---
mkdir -p "$TARGET/Edge Cases"

# File with spaces
echo "content" > "$TARGET/Edge Cases/file with spaces.txt"

# Unicode filename
echo "unicode" > "$TARGET/Edge Cases/café-résumé.txt"

# Very long filename
LONGNAME=$(python3 -c "print('a' * 200)")
echo "long name" > "$TARGET/Edge Cases/${LONGNAME}.txt"

# Empty file
touch "$TARGET/Edge Cases/empty.dat"

# Empty directory
mkdir -p "$TARGET/Edge Cases/empty_folder"

# Symlink (valid)
ln -sf "../Documents/Personal/journal.txt" "$TARGET/Edge Cases/journal_link"

# Symlink (dangling)
ln -sf "/nonexistent/path/file.txt" "$TARGET/Edge Cases/broken_link"

# --- Set specific timestamps ---
# Make some files look old
touch -t 201501150930.00 "$TARGET/Documents/Work/Q3 Report.txt"
touch -t 201805201400.00 "$TARGET/Photos/2020-vacation/IMG_1.jpg"
touch -t 202012251200.00 "$TARGET/Music/Rock/song1.mp3"
touch -t 200301010000.00 "$TARGET/Code/my-project/src/main.py"

# Set directory timestamps
touch -t 201501150930.00 "$TARGET/Documents/Work"
touch -t 201805201400.00 "$TARGET/Photos/2020-vacation"

# --- Summary ---
echo ""
echo "Virtual drive created:"
echo "  Location: $TARGET"
TOTAL=$(du -sh "$TARGET" | cut -f1)
FILES=$(find "$TARGET" -type f | wc -l)
DIRS=$(find "$TARGET" -type d | wc -l)
echo "  Size: $TOTAL"
echo "  Files: $FILES"
echo "  Directories: $DIRS"
echo ""
echo "To test hdtool:"
echo "  python -m hdtool --source $TARGET --dest /tmp/hdtool-demo-dest"
