# DiskReport

DiskReport shows where the space on your Mac went and what changed since the last time you
looked. It lives in the menu bar as a percentage ("82%") and opens a panel with three tabs:
Status, Changes and Findings. A terminal version prints the same report.

<!-- Screenshot placeholder: save a capture as docs/screenshot.png and replace this line with
![DiskReport panel](docs/screenshot.png) -->
*Screenshot coming soon.*

## What it will and won't do to your files

**DiskReport never erases anything.** It measures with `du` and `df` and shows you the
results. You decide what to do about them.

The menu bar app has one action that changes files: **Move to Trash**, on items inside your
own folders. You pick the items, read a list of exactly what will move and its total size,
and confirm. Cancel is the default. Finder's Put Back restores anything you moved. System
files, other accounts' folders and caches get no Trash option at all.

The engine (`diskreport/core`) and the terminal version (`diskreport/cli`) are read-only.

I built it this way because every real decision about what to delete on my Mac needed
knowledge the computer didn't have. Two of three identical Autodesk Fusion folders were
orphans and one was live. A 2.3 GB folder called `saves` held Minecraft worlds I built; an
8.4 GB folder called `tmp` was six months of garbage. A heuristic gets those wrong. Dates,
sizes and a reason get you to the right call in ten minutes.

## Install

1. Download `DiskReport-<version>.dmg` from [Releases](../../releases).
2. Open it and drag **DiskReport** onto **Applications**.
3. **The first launch needs one extra step**, because the app isn't signed with an Apple
   Developer ID. In Applications, **right-click DiskReport and choose Open**, then click
   **Open**. If macOS only offers "Done" or "Move to Trash", open
   **System Settings › Privacy & Security**, scroll to the message about DiskReport and
   click **Open Anyway**. You do this once.

DiskReport then shows up in the menu bar with no Dock icon. Opening it again from
Applications opens its panel.

If you open the app straight from the downloaded disk image, macOS runs it from a temporary
read-only copy. It still works, but it tells you to move it to Applications so it can
open at login.

For complete numbers, give it **Full Disk Access** in System Settings › Privacy & Security.
Without that, macOS hides Mail, Messages, Photos, the Trash and some system folders from it,
and the report marks those sizes as partial.

## Using the app

- **Menu bar:** how full your Data volume is. Amber above 85%, red above 90%.
- **Status:** free, used and total space, growth per week across your scans, the date
  you'd hit 95% at that rate, a chart of free space over the last 12 scans, and when your
  last Time Machine backup ran. **Scan Now** runs in the background. When it finishes, a
  "Scan complete" line points you to the Findings tab.
- **Changes:** every folder that grew or shrank by more than 500 MB since an earlier scan.
  Pick any saved scan to compare against.
- **Findings:** the ten targeted checks below, with a severity (warning, notice, info) and a
  category label on every item: CACHE, REBUILDABLE, SYSTEM, USER DATA, OTHER ACCOUNT or
  UNKNOWN. It says UNKNOWN when it isn't sure.
- **Preferences:** automatic scans (weekly by default, or off), notifications, open at login.
- **Help** explains all of it inside the app.

Notifications stay quiet unless the disk crosses 85%, a folder grows by more than 5 GB
between scans, or your backup is more than 14 days old. A Scan Now you started also tells
you when it's done if the panel is closed.

## Terminal version

You need Python 3.8 or newer and nothing else. The engine uses the standard library only.

```bash
git clone https://github.com/Kamiko-Weeb/DiskReport.git
cd DiskReport
python3 -m diskreport.cli
```

| Command | Does |
|---|---|
| `python3 -m diskreport.cli` | Scan, print the report, save a snapshot |
| `python3 -m diskreport.cli --last` | Print the newest saved report without scanning |
| `python3 -m diskreport.cli --compare 2026-10-01` | Diff against a specific snapshot |
| `python3 -m diskreport.cli --list` | List saved snapshots |
| `python3 -m diskreport.cli --sudo` | Also measure root-owned folders and other accounts; prints the exact `sudo du` commands first |

A scan takes 20 seconds to 2 minutes.

## Where your data goes

Nothing leaves your Mac. DiskReport keeps everything in `~/.diskreport/`:

- `snapshots/YYYY-MM-DD-HHMM.json`: one per scan. It keeps the newest 12 and removes older
  ones, the only files it ever deletes.
- `latest-report.txt`: the newest report as plain text, rewritten after every scan. Open
  it, send it to someone, or ask an assistant such as Claude to read it.
- `settings.json`: the app's preferences.

Turning on "Open at login" adds `~/Library/LaunchAgents/io.github.kamiko-weeb.diskreport.plist`;
turning it off removes it. Export writes a text file where you choose.

One side effect sits outside DiskReport: the staged-updates check runs
`softwareupdate --list`, and Apple's update service records that check in
`/Library/Preferences/com.apple.SoftwareUpdate.plist`.

## Why the targeted checks exist

A size listing buries the things that ate my disk. Each check names one of them, and the
reasoning matters more than the code, so it's written down here.

1. **Staged OS updates.** `/System/Volumes/Update` and `/Library/Updates` held 15.8 GB on my
   Mac. It had been failing to stage a major update for months because the disk was full,
   and the stuck download kept the disk full. SIP protects these folders, so `rm` can't
   touch them: install the update or remove it in System Settings.
2. **Autodesk Fusion orphaned builds.** Fusion keeps every 8–9 GB version it has downloaded
   and never removes the old ones. The `Autodesk Fusion.app` symlink in
   `webdeploy/production` points at the live build, so the check reads it and marks the
   rest as orphans. An old symlink date means Fusion hasn't managed to update, often for lack
   of space.
3. **Messages staging.** `~/Library/Containers/com.apple.MobileSMS/Data/tmp` should sit
   near empty. Mine held 8.4 GB. The check shows the newest item's date, so you can see
   whether anything is still in flight, and reports `~/Library/Messages/Attachments`
   separately because that one is your real message history.
4. **Orphaned Music library.** `Previous Libraries` keeps old database backups and, inside
   `Media.localized`, two different things: Apple Music downloads you can get again, and
   imported or purchased files that may exist nowhere else.
5. **Duplicate game launchers.** Minecraft, Modrinth, CurseForge, Lunar, Prism and the rest
   each keep a full copy of game versions, libraries and assets. Only you know which one you
   open, so the check shows sizes and the newest file in each and stops there.
6. **Virtual environments and dependency folders.** `node_modules`, venvs, `build`, `dist`
   and friends come back from a lockfile. The check looks for that lockfile or manifest next
   to each folder and marks a bare name match as UNVERIFIED, because a folder called
   `build` can hold your own work.
7. **APFS snapshots.** A Time Machine local snapshot keeps deleted files' space in use until
   the snapshot expires, so deleting things can free nothing for up to a day. The report
   says so at the top when one exists. Update snapshots live on the system volume and pin
   nothing of yours.
8. **Backup freshness.** I believed my backup was current and it was five and a half
   months old. Past 14 days the report warns. An unplugged drive reads as "not connected",
   which differs from having no backup.
9. **Apple Intelligence models.** Large `UAF_` folders in `/System/Library/AssetsV2`. SIP
   protects them, turning the feature off doesn't reliably release them, and Apple offers no
   supported way to remove them. The check reports them so you know where those gigabytes went.
10. **Other user accounts.** Other people's home folders use the same disk. The check lists
    them, measured as far as it can read, and labels them "not yours, do not act without
    asking".

The System Settings Storage pane on my Mac reported 69.9 GB of Applications when
`/Applications` held 18 GB, so DiskReport never reads Apple's categories. Every number
comes from `du` and `df`.

## Building

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-build.txt
packaging/build_app.sh                  # dist/DiskReport.app (PyInstaller, --onedir --windowed)
.venv/bin/python packaging/build_dmg.py # dist/DiskReport-<version>.dmg
.venv/bin/python -m diskreport.app      # or run the app from source
```

Releases come from GitHub Actions: pushing a tag like `v0.1.0` builds the `.dmg` on a macOS
runner and attaches it to a GitHub Release. Built apps and disk images never go into the git
history.

## Layout

```
diskreport/core/     the engine: scanner, checks, snapshots, formatting (standard library only)
diskreport/cli/      the terminal report
diskreport/app/      the PyQt6 menu bar app (views/, trash.py)
packaging/           build_app.sh, build_dmg.py, make_icon.py
```

Each targeted check is one function in `core/checks.py` returning the same result shape
(name, severity, total size, findings, note). The terminal and the app both render a new
check without other changes.
