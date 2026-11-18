"""The menu bar app (PyQt6). Run from a checkout with:  python3 -m diskreport.app

Where something is worth acting on, the app offers "Reveal in Finder" and the reason. For
items in your own folders it also offers "Move to Trash", per item or for a ticked
selection, always behind a confirmation that lists exactly what will move (app/trash.py).
Nothing is ever erased: the Trash keeps it until you empty it. The engine (core/) and the
terminal version (cli/) stay read-only.

Files the app writes or moves, all of them on your request or in its own folder:
  ~/.diskreport/snapshots/*.json       scan snapshots (the only files it ever removes: its own
                                       snapshots beyond the newest 12)
  ~/.diskreport/settings.json          its preferences
  ~/.diskreport/latest-report.txt      the newest report as plain text, after each scan
  items you confirm in Move to Trash   moved to ~/.Trash, never erased
  ~/Library/LaunchAgents/<id>.plist    only while "Open at login" is turned on
  the export file you choose           only when you click Export

Not a file the app writes, but worth knowing: each scan runs `softwareupdate --list`, which
asks Apple's update service for a fresh check. That service (not DiskReport) then updates
its own record of the check in /Library/Preferences/com.apple.SoftwareUpdate.plist.
"""

APP_NAME = "DiskReport"
BUNDLE_ID = "io.github.kamiko-weeb.diskreport"
