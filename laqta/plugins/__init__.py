"""Game plugins, looked up by the "plugin" key in games.json."""

from ..core.events import GamePlugin
from .cs2_gsi import Cs2Plugin

PLUGINS: dict[str, type[GamePlugin]] = {Cs2Plugin.name: Cs2Plugin}
