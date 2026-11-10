"""Valorant: the template for vision-based plugins (no official local API exists).

STATUS: not calibrated. The regions below are first guesses and there are no template
images yet, so this plugin detects nothing until templates cut from a real recording are
placed in assets/valorant/. Calibrate with:

    python -m laqta vision-test valorant "<recording.mkv>"

Signals:
  kill   the local player's rows in the killfeed (top-right). Template: the part of a row
         that only appears on your own kills, cut at 1080p. Files: kill_*.png
  win / lose   the end-of-match banner. Files: win_*.png / lose_*.png
"""

from __future__ import annotations

from pathlib import Path

from .vision import Signal, VisionPlugin, load_templates

ASSETS = Path(__file__).with_name("assets") / "valorant"
KILLFEED = (0.70, 0.05, 1.00, 0.35)  # guess: top-right corner
BANNER = (0.30, 0.10, 0.70, 0.35)  # guess: upper centre


class ValorantPlugin(VisionPlugin):
    name = "valorant"
    event_types = ("kill", "win", "lose", "match_end")

    def signals(self) -> list[Signal]:
        return [
            Signal("kill", KILLFEED, load_templates(ASSETS, "kill_*.png"), threshold=0.8, kind="count"),
            Signal("win", BANNER, load_templates(ASSETS, "win_*.png"), threshold=0.8, kind="present"),
            Signal("lose", BANNER, load_templates(ASSETS, "lose_*.png"), threshold=0.8, kind="present"),
        ]
