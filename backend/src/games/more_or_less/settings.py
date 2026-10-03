"""Admin-configurable settings for MoreOrLess - see games/settings_spec.py for the contract,
games/settings_registry.py for how every game's specs get assembled into one registry and
services/game_settings_service.py for how they're read/written."""

from games.more_or_less.game import MODE_ALBUM_ASSETS, MODE_PERSON_ASSETS, MODE_PERSON_BIRTH_DATE
from games.settings_spec import CHAIN_LENGTH_SPEC, SettingSpec

# How many wrong guesses a game survives - the game ends on wrong guess number strike_count + 1.
# 0 (the default) keeps the classic rule: the first wrong guess ends it. Capped so a game can't be
# made effectively unlosable. Read by MoreOrLessGame.has_next_round; also part of every mode's daily
# settings (services/daily_settings.py inherits each mode's specs), snapshotted per day like the rest.
STRIKE_COUNT_SPEC = SettingSpec("strike_count", 0, "int", 0, 10)

SETTING_SPECS: dict[str, list[SettingSpec]] = {
    MODE_PERSON_ASSETS: [STRIKE_COUNT_SPEC],
    MODE_ALBUM_ASSETS: [STRIKE_COUNT_SPEC],
    MODE_PERSON_BIRTH_DATE: [STRIKE_COUNT_SPEC],
}

# MoreOrLess's daily build_spec pre-generates a fixed-length chain instead of avoiding repeats
# across days (it has no "asset/person" content of its own to avoid repeating within a single day -
# "ahí solo debe ser otra seed"), so it needs chain_length and not no_repeat_days, unlike every
# other game.
DAILY_SETTING_SPECS: list[SettingSpec] = [CHAIN_LENGTH_SPEC]
