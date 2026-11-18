"""diskreport: a disk usage report for macOS.

diskreport.core   the engine: scanning, the targeted checks, snapshots. Standard library only.
diskreport.cli    the terminal report:  python3 -m diskreport.cli
diskreport.app    the menu bar app (PyQt6)

Nothing in this package erases files. core and cli are read-only outside ~/.diskreport.
The app's one change to your files is Move to Trash, for items you pick and confirm.
"""

__version__ = "0.1.0"
