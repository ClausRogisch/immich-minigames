"""Service-layer exceptions shared by more than one services/*.py module - kept in their own
module (rather than living in whichever service defined them first) specifically to avoid a
circular import: services/daily_games_service.py's DailyGamesService.create_daily_game delegates
challenge generation to services/daily_challenge_service.py's DailyChallengeService, which needs to
raise the very same NotEnoughContentError services/game_factory.py already raises for a live game's
start() failures.

Also holds every game-lifecycle exception the games/daily/scores services raise (GameNotFoundError
and friends below) - living here (rather than in whichever service raises it) is what lets
api/error_handlers.py's exception->status table import them without importing those services'
own dependencies just to read a table of exception types. Every service and DTO module that raises
or catches one of these imports it from here directly."""


class UnsupportedGameError(Exception):
    pass


class NotEnoughContentError(Exception):
    """Raised when the Immich library doesn't have enough named people/faces/located assets to
    start a game or play a round - the friendly ValueError each game's start()/create_next_round()
    already raises for that case (see games/more_or_less/game.py, games/immichdle/game.py,
    games/whos_that_person/game.py, games/geoguessr/game.py, games/dateguessr/game.py,
    games/trivium/game.py), re-raised here by services/game_factory.py and
    services/games_service.py so main.py can map it to a 422 instead of it reaching the client as a
    bare 500. Also raised by services/daily_challenge_service.py when a daily challenge can't be
    generated at all."""


class GameNotFoundError(Exception):
    pass


class GameOwnershipError(Exception):
    pass


class RoundNotPendingError(Exception):
    pass


class DailyNotEnabledError(Exception):
    """Raised by create_daily_game when the (game_type, mode) isn't in today's daily rotation
    (either genuinely unsupported, or a real mode the admin hasn't enabled) - main.py maps this to
    a 404."""


class DailyAlreadyPlayedError(Exception):
    """Raised by create_daily_game when the caller already has a game for today's challenge of
    this (game_type, mode) - one attempt per day. main.py maps this to a 409."""


class ImmichNotLinkedError(Exception):
    """Raised when a request needs the caller's own Immich account (anything that reads photos,
    people or albums - see services/immich/_scope.py) but they haven't linked one yet, or the key
    they linked can't be decrypted anymore. api/error_handlers.py maps this to a 409 the frontend
    recognizes by its detail and turns into a "connect your Immich account" prompt."""

    DETAIL = "immich_not_linked"

    def __init__(self) -> None:
        super().__init__(self.DETAIL)


class ImmichKeyRejectedError(Exception):
    """Raised when linking an Immich API key fails validation (unreachable Immich, an invalid key,
    or one missing a required permission) - the message says which, and is shown to the player.
    api/error_handlers.py maps this to a 400."""
