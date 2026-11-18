"""Build dist/DiskReport-<version>.dmg from dist/DiskReport.app with dmgbuild.

    python packaging/build_dmg.py            (after packaging/build_app.sh)

The disk image shows the app, an Applications shortcut to drag it onto, and a short text
file explaining how to open an unsigned app the first time.
"""

import os
import sys

import dmgbuild

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from diskreport import __version__  # noqa: E402

APP = os.path.join(ROOT, "dist", "DiskReport.app")
OUT = os.path.join(ROOT, "dist", "DiskReport-%s.dmg" % __version__)
FIRST_OPEN = os.path.join(ROOT, "build", "If macOS blocks DiskReport.txt")

FIRST_OPEN_TEXT = """\
DiskReport is not signed with an Apple Developer ID, so the first time you open it
macOS says it "cannot be opened" or "cannot verify the developer".

1. Drag DiskReport into the Applications folder.
2. In Applications, right-click (or Control-click) DiskReport and choose Open.
3. Click Open in the dialog.

On recent macOS versions that dialog may only offer "Done" or "Move to Trash". Then open
System Settings > Privacy & Security, scroll down to the message about DiskReport and click
"Open Anyway".

You only need to do this once. DiskReport then lives in your menu bar (it shows how full
your disk is, e.g. "82%"); it has no Dock icon.
"""


def main():
    if not os.path.isdir(APP):
        sys.exit("dist/DiskReport.app not found: run packaging/build_app.sh first")
    os.makedirs(os.path.dirname(FIRST_OPEN), exist_ok=True)
    with open(FIRST_OPEN, "w") as f:
        f.write(FIRST_OPEN_TEXT)
    if os.path.exists(OUT):
        os.remove(OUT)   # our own build output from a previous run
    settings = {
        "format": "UDZO",
        "files": [APP, FIRST_OPEN],
        "symlinks": {"Applications": "/Applications"},
        "icon_locations": {
            "DiskReport.app": (140, 150),
            "Applications": (400, 150),
            os.path.basename(FIRST_OPEN): (270, 320),
        },
        "window_rect": ((200, 200), (540, 440)),
        "icon_size": 96,
        "text_size": 13,
        "show_status_bar": False,
        "show_toolbar": False,
        "show_pathbar": False,
        "default_view": "icon-view",
    }
    dmgbuild.build_dmg(OUT, "DiskReport %s" % __version__, settings=settings)
    print(OUT)


if __name__ == "__main__":
    main()
