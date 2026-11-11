"""Game plugins, looked up by the "plugin" key in games.json."""

from ..core.events import GamePlugin
from .cs2_gsi import Cs2Plugin
from .lol_live import LolPlugin
from .rocket_league import RocketLeaguePlugin
from .valorant_vision import ValorantPlugin

PLUGINS: dict[str, type[GamePlugin]] = {p.name: p for p in (Cs2Plugin, LolPlugin, RocketLeaguePlugin, ValorantPlugin)}
