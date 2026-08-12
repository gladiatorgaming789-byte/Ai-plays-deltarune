"""Single source of truth for the standalone speed-mod release."""

from __future__ import annotations


VERSION = "1.2.0"
TARGET_VERSION = "1.05"
SUPPORTED_GAME_BUILD = "Steam build 24484059 / Chapter 5 v0.0.253"
SUPPORTED_CHAPTERS = tuple(range(1, 6))
MINIMUM_G3MTOOL_VERSION = (1, 2, 5)
BUILD_INFO_FILENAME = "build_info.json"

