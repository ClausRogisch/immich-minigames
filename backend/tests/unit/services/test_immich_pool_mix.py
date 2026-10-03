"""services/immich/assets.py's shared_album_share - pure, no DB."""

import pytest

from services.immich.assets import SHARED_ALBUM_MAX_SHARE, shared_album_share


class TestSharedAlbumShare:
    def test_no_shared_photos_means_none_are_drawn(self):
        assert shared_album_share(500, 0) == 0.0

    def test_no_home_photos_means_all_are_shared(self):
        assert shared_album_share(0, 500) == 1.0

    def test_proportional_while_under_the_cap(self):
        assert shared_album_share(900, 100) == pytest.approx(0.1)

    def test_a_huge_shared_album_is_capped(self):
        assert shared_album_share(100, 10_000) == SHARED_ALBUM_MAX_SHARE

    def test_nothing_at_all(self):
        assert shared_album_share(0, 0) == 0.0
