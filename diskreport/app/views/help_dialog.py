"""Help: how to use DiskReport, in one scrollable window."""

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout

from ...core import snapshots
from ...core.formatting import CATEGORIES, tilde
from . import theme


def _chip(text, color):
    return ('<span style="color:%s; border:1px solid %s; font-weight:600;">&nbsp;%s&nbsp;</span>'
            % (color, color, text))


def help_html():
    cats = "".join(
        "<tr><td>%s</td><td>&nbsp;%s</td></tr>" % (_chip(c, theme.CATEGORY_COLORS[c]), meaning)
        for c, meaning in CATEGORIES.items())
    sev = "".join(
        "<tr><td>%s</td><td>&nbsp;%s</td></tr>" % (_chip(s.upper(), theme.SEVERITY_COLORS[s]), text)
        for s, text in (("warning", "Act on this soon."),
                        ("notice", "Worth a look when you have ten minutes."),
                        ("info", "For the record. Usually nothing to do.")))
    return """
<h2>DiskReport</h2>
<p>DiskReport shows where the space on your Mac went and what changed since the last scan.
When something is worth acting on, it shows you where it is and why, and you decide. It never
erases anything: the most it does is move items you pick to the Trash, after you confirm.</p>

<h3>The menu bar number</h3>
<p>The percentage is how full your disk is. It turns
<span style="color:%(warn)s"><b>amber</b></span> above 85%% and
<span style="color:%(crit)s"><b>red</b></span> above 90%%, where macOS starts running short
of room for updates and swap. A trailing &hellip; means a scan is running.
Click the number to open the panel; click anywhere else or press Esc to close it.</p>

<h3>Status</h3>
<ul>
<li><b>Free / used / total</b>: straight from <code>df</code> for your Data volume.</li>
<li><b>Growth</b>: how fast used space is growing per week, worked out from your saved scans,
and the date you would reach 95%% full at that rate. It needs two scans at least a day apart.</li>
<li><b>The line chart</b>: free space across your last scans.</li>
<li><b>Time Machine</b>: when your last backup was. If the backup drive is unplugged, it says
so rather than claiming you have no backup.</li>
<li><b>Scan Now</b>: measures everything again. It runs in the background and takes about
twenty seconds to two minutes. You can keep using the panel meanwhile. When it finishes, a
"Scan complete" line appears with a <b>Show Findings</b> button; if the panel was closed, you
also get a notification.</li>
</ul>

<h3>Changes</h3>
<p>Every folder that grew or shrank by more than 500 MB, biggest change first.
<span style="color:%(grow)s"><b>Red</b></span> grew,
<span style="color:%(shrink)s"><b>green</b></span> shrank.
Use <b>Compare with</b> to pick any earlier scan, for example to see what changed over the
last six weeks. Click a row to see its full path and a <b>Reveal in Finder</b> button.
Greyed rows are parent folders whose change is mostly the subfolders listed with them.</p>

<h3>Findings</h3>
<p>Ten checks for the things that most often fill a Mac: staged macOS updates, old Autodesk
Fusion builds, the Messages staging folder, old Music libraries, duplicate game launchers,
node_modules and virtual environments, APFS snapshots, backup age, Apple Intelligence models
and other user accounts. Only checks that found something are listed; tick
<b>Show all checks</b> to see the rest. Click a check to see what it found and what it means.</p>
<table>%(sev)s</table>

<h3>Labels</h3>
<p>Every item carries a label saying what kind of thing it is. When DiskReport is not sure,
it says <b>UNKNOWN</b> rather than guessing.</p>
<table>%(cats)s</table>

<h3>Reveal in Finder and Move to Trash</h3>
<p><b>Reveal in Finder</b> opens a Finder window with the item selected.</p>
<p><b>Move to Trash&hellip;</b> appears next to items in your own folders. Tick
<b>Select</b> on several items to move them together with the bar at the bottom of the
panel. Every move shows the exact list and total first, with Cancel as the default.
Items go to the Trash, not straight to deletion: Finder's <b>Put Back</b> restores them,
and the space only comes back when you empty the Trash.</p>
<p>System files, other accounts and caches have no Trash option. Before you move anything,
check the dates: only you know which of two identical-looking folders is the one you still
use (a launcher you open, a live Fusion build, a folder of saves).</p>

<h3>Getting complete numbers</h3>
<p>macOS hides some folders (Mail, Messages, Photos, the Trash and some system models) from
apps by default. Those show up as "could not check" or as partial sizes. To include them,
open <b>System Settings &rsaquo; Privacy &amp; Security &rsaquo; Full Disk Access</b> and
turn on DiskReport.</p>

<h3>Preferences</h3>
<ul>
<li><b>Automatic scan</b>: weekly by default; daily, every two weeks, monthly, or off.
DiskReport also scans when it starts if your last scan is more than a week old.</li>
<li><b>Notifications</b>: only when the disk crosses 85%% full, something grew by more than
5 GB since the last scan, or your backup is more than 14 days old. Nothing otherwise.</li>
<li><b>Open at login</b>: off until you turn it on.</li>
</ul>

<h3>Export</h3>
<p><b>Export&hellip;</b> saves the current report as a plain text file wherever you choose.</p>

<h3>Your data</h3>
<p>Scans are saved in <code>%(snapdir)s</code>; the newest %(keep)d are kept and older ones
are removed automatically. Nothing is sent anywhere.</p>

<h3>Reading the report elsewhere</h3>
<p>After every scan the full report is also saved as plain text in
<code>~/.diskreport/latest-report.txt</code>, so you can open it, share it, or ask Claude to
read it. From a copy of the source, <code>python3 -m diskreport.cli</code> runs a scan in
Terminal and <code>python3 -m diskreport.cli --last</code> prints the newest saved report
without scanning. The terminal version never moves or deletes anything.</p>
""" % {"warn": theme.CAPACITY_WARN, "crit": theme.CAPACITY_CRITICAL,
       "grow": theme.GROWTH_COLOR, "shrink": theme.SHRINK_COLOR,
       "sev": sev, "cats": cats, "snapdir": tilde(snapshots.SNAP_DIR), "keep": snapshots.SNAP_KEEP}


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DiskReport Help")
        self.resize(560, 680)
        lay = QVBoxLayout(self)
        text = QTextBrowser()
        text.setOpenExternalLinks(False)
        text.setHtml(help_html())
        lay.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)
