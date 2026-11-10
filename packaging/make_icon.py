"""Make packaging/laqta.ico from the tray icon (Qt writes multi-purpose .ico files)."""

import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication, QImage

here = Path(__file__).parent
app = QGuiApplication(sys.argv)
image = QImage(str(here.parent / "laqta" / "ui" / "icon.png"))
if image.isNull() or not image.save(str(here / "laqta.ico"), "ICO"):
    sys.exit("could not write laqta.ico")
