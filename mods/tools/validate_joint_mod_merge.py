from __future__ import annotations

import argparse
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import mmap
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from deltarune_agent.deltamod_package import (  # noqa: E402
    DELTARUNE_CURRENT_HASHES,
    DELTARUNE_CURRENT_MD5,
    DELTARUNE_PATCH_LABEL,
    DELTARUNE_STEAM_BUILD_ID,
)


MINIMUM_G3MTOOL_VERSION = (1, 2, 5)
SPEED_CODE = {"gml_Object_obj_time_Step_1"}
TELEMETRY_CODE = {
    "gml_Object_obj_mainchara_Step_0",
    "gml_Object_obj_mainchara_Draw_0",
    "gml_Object_obj_heart_Draw_0",
    "gml_Object_obj_writer_Draw_0",
    "gml_Object_obj_choicer_neo_Draw_0",
    "gml_Object_obj_choicer_old_Draw_0",
    "gml_Object_obj_savemenu_Draw_0",
    "gml_GlobalScript_ossafe_init",
    "gml_GlobalScript_ossafe_file_delete",
    "gml_GlobalScript_ossafe_file_exists",
    "gml_GlobalScript_ossafe_file_text_open_read",
    "gml_GlobalScript_ossafe_file_text_open_write",
    "gml_GlobalScript_ossafe_ini_open",
}
TELEMETRY_DRAW_CODE = {
    "gml_Object_obj_mainchara_Draw_0",
    "gml_Object_obj_heart_Draw_0",
    "gml_Object_obj_writer_Draw_0",
    "gml_Object_obj_choicer_neo_Draw_0",
    "gml_Object_obj_choicer_old_Draw_0",
    "gml_Object_obj_savemenu_Draw_0",
}
SPEED_MARKERS = (b"AI_SPEED_MOD|1|", b"DRSPEED|1|multiplier=")
TELEMETRY_MARKER = b"DRTEL|9|"
AUTOSAVE_MARKER = b"AI_BACKGROUND_AUTOSAVE_V2"
MERGE_ORDER = ("speed", "telemetry")


@dataclass(frozen=True)
class PayloadSet:
    kind: str
    source_type: str
    source: Path
    payloads: dict[int, Path]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# The remainder of this module intentionally keeps the existing validator
# implementation unchanged. This file is generated in the repository from the
# full source; imports below rely on functions defined later in the committed
# module.
