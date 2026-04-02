# Hard Drive Backup Tool - Tutorial

A terminal-based backup tool for safely copying old hard drives to a UGREEN NAS (or any Linux system with Docker).

## Quick Start (Local, No Docker)

```bash
# Install
pip install -e .

# Create a virtual drive to try it out
bash demo/create_virtual_drive.sh

# Run the tool
python -m hdtool --source /tmp/hdtool-demo-source --dest /tmp/hdtool-demo-dest
```

## Quick Start (Docker, for UGREEN NAS)

```bash
# Build the image (do this once)
docker build -t hdtool .

# Run against a USB drive mounted at /mnt/@usb/sdc2
docker run -it --rm \
  -v /mnt/@usb/sdc2:/source:ro \
  -v /home/asingularity/old-drives-archive/drive-name:/dest \
  hdtool
```

The source is mounted **read-only** (`:ro`) — the tool physically cannot modify your original drive.

## How It Works

The tool runs in 5 phases:

### Phase 1: Scan

Walks the source drive and builds a complete file tree. If you point it at a destination that already has some files from a previous run, it detects what's already been copied.

### Phase 2: Pre-Check

Analyzes the source for problems that will cause copy failures:

- **Disk space**: Is there enough room on the destination?
- **Filesystem detection**: Identifies source/dest filesystem types (ext4, NTFS, FAT32, HFS+, exFAT) and warns about known limitations
- **Permissions**: Files/directories that can't be read
- **Symlinks**: Dangling or external symlinks
- **Special files**: Device files, sockets (can't be copied)
- **Filenames**: Characters that may cause problems on certain filesystems
- **Path length**: Paths or filenames that exceed filesystem limits

The report uses severity levels:
- `ERR` — will definitely fail
- `WARN` — may cause problems
- `INFO` — informational, unlikely to cause issues

**Filesystem-specific notes:**
- **FAT32**: The tool automatically uses `--modify-window=1` in rsync to handle FAT32's 2-second timestamp resolution
- **NTFS**: Permissions may not be preserved accurately; this is reported in the pre-check
- **HFS+ (Mac)**: Resource forks are not copied (only the data fork)
- **exFAT**: No permissions or symlinks

### Phase 3: Select

An interactive tree selector opens in your terminal:

```
[x] Documents/  (5.2 GB, 234 files)  [COPIED]
[x] Photos/     (15.3 GB, 567 files) [NEW]
    [x] 2020/   (3.1 GB, 120 files)  [NEW]
    [ ] Thumbs/  (0.5 GB, 200 files)  [NEW]
[~] Music/      (10.1 GB, 300 files) [PARTIAL]
```

**Controls:**
| Key | Action |
|-----|--------|
| Arrow keys | Navigate |
| `Space` | Toggle selection |
| Right arrow | Expand directory |
| Left arrow | Collapse directory |
| `a` | Select all |
| `n` | Deselect all |
| `Enter` | Confirm and start copy |
| `q` | Cancel |

**Status labels:**
- `[NEW]` — not yet copied
- `[COPIED]` — already exists at destination with matching size and timestamp
- `[PARTIAL]` — directory is partially copied
- `[MODIFIED]` — exists at destination but size or timestamp differs

### Phase 4: Copy

Files are copied using `rsync` with these flags:
- `-a` (archive: preserves permissions, timestamps, symlinks, owner, group)
- `-H` (preserve hard links within each directory tree)
- `-S` (handle sparse files efficiently — important for VM images, databases)
- `--partial` (keep partial transfers for resume)
- `--ignore-errors` (continue on failures)

You'll see a progress bar:

```
Copying ━━━━━━━━━━━━━━━━━━━━ 45.2% 2.1 GB/4.7 GB 15.6 MB/s 0:05:23
```

- Press `Ctrl+C` to safely interrupt — state is saved for resume
- Already-copied files are skipped automatically (rsync compares size + timestamp)
- Partial files are kept and completed on resume

### Phase 5: Report

After copying, you get:
- Success/failure summary
- Error report grouped by type (permission denied, I/O error, etc.)
- Full error log saved as JSON at `<dest>/.hdtool-errors.jsonl`

## Fixing Timestamps From a Previous Bad Copy

If you previously copied files with a tool that didn't preserve timestamps (e.g., plain `cp` without `-p`), you can fix the timestamps without re-copying any data:

```bash
python -m hdtool --fix-timestamps --source /mnt/@usb/sdc2 --dest /path/to/backup
```

Or in Docker:
```bash
docker run -it --rm \
  -v /mnt/@usb/sdc2:/source:ro \
  -v /path/to/backup:/dest \
  hdtool --fix-timestamps
```

This will:
1. Walk both source and destination
2. For each file that exists in both with **matching size**, copy the timestamp from source to destination
3. Fix directory timestamps bottom-up
4. Report how many timestamps were fixed

Files with different sizes are skipped (they may have been modified and need a real copy). No file data is transferred — only metadata is updated.

**Example output:**
```
Timestamps fixed!

  Files checked:         1,234
  File timestamps fixed:   892
  Dir timestamps fixed:     45
  Files not in dest:        12
  Size mismatches:           3
  Errors:                    0
```

## Resuming an Interrupted Copy

If the copy is interrupted (Ctrl+C, SSH disconnect, power loss), just run the same command again:

```bash
python -m hdtool --source /tmp/hdtool-demo-source --dest /tmp/hdtool-demo-dest
```

The tool will detect the previous session and ask:
```
Previous session found
  Started: 2026-04-01T22:30:00
  Status:  interrupted
  Progress: 2.1 GB / 4.7 GB

Resume previous session? [Y]es / [N]ew / [D]elete:
```

- **Y** — Resume with the same file selection, skipping already-copied files
- **N** — Start fresh (rescan and reselect)
- **D** — Delete previous state and start fresh

Or use `--resume` to skip the prompt:
```bash
python -m hdtool --source ... --dest ... --resume
```

## CLI Options

```
python -m hdtool [OPTIONS]

Options:
  --source PATH       Source directory (default: /source for Docker)
  --dest PATH         Destination directory (default: /dest for Docker)
  --resume            Auto-resume without prompting
  --no-precheck       Skip pre-check analysis
  --dry-run           Show what would be copied without copying
  --python-copier     Use Python copier instead of rsync
  --fix-timestamps    Fix dest timestamps from source without re-copying data
  --help              Show help
```

## Using on UGREEN NAS

### Prerequisites

1. SSH access to your NAS
2. Docker installed (via UGOS App Center or manually)

### Finding Your USB Drive

SSH into the NAS and list USB devices:
```bash
ls /mnt/@usb/
```

USB drives typically appear as `sdc1`, `sdc2`, `sdf1`, etc. Check the contents to identify the right one:
```bash
ls /mnt/@usb/sdc2/
```

### Running the Backup

```bash
# Build once
cd /path/to/hard-drive-tool
docker build -t hdtool .

# Run for each drive
docker run -it --rm \
  -v /mnt/@usb/sdc2:/source:ro \
  -v /home/asingularity/old-drives-archive/my-old-drive:/dest \
  hdtool
```

### Using docker-compose

```bash
SOURCE_PATH=/mnt/@usb/sdc2 \
DEST_PATH=/home/asingularity/old-drives-archive/my-old-drive \
docker compose run --rm hdtool
```

## Verifying the Backup

After the copy, you can verify timestamps were preserved:

```bash
# Compare a file's timestamps
ls -la /mnt/@usb/sdc2/some/file.txt
ls -la /home/asingularity/old-drives-archive/my-old-drive/some/file.txt
```

The modification times should match.

## Files Created in Destination

The tool creates a few metadata files in the destination directory:

| File | Purpose |
|------|---------|
| `.hdtool-state.json` | Session state for resume |
| `.hdtool-errors.jsonl` | Error log (one JSON object per line) |
| `.hdtool.lock` | Lock file (removed after run) |
| `.hdtool-partial/` | Temporary directory for partially transferred files |

These can be safely deleted after a successful backup is complete.

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## Trying the Demo

```bash
# Full demo: creates virtual drive and runs the tool
bash demo/run_demo.sh

# Or step by step:
bash demo/create_virtual_drive.sh /tmp/my-test-drive
python -m hdtool --source /tmp/my-test-drive --dest /tmp/my-test-backup
```

## Known Limitations

- **Hard links across top-level directories**: Hard links are preserved within each top-level directory tree (`-H` flag), but not across separate top-level items, since each gets its own rsync invocation.
- **Extended attributes (xattrs)**: Not copied by default. Most old drives don't use these, but if needed you can extend the rsync flags.
- **HFS+ resource forks**: Only the data fork is copied. Resource forks (used by older Mac apps for metadata like custom icons) are not preserved.
- **Docker networking**: The Docker container doesn't need networking — all operations are local file I/O.
