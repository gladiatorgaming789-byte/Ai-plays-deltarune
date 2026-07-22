from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
from typing import Iterable
from uuid import uuid4


APP_DIRECTORY_NAME = "DeltaruneAgent"
DATA_DIRECTORY_ENV = "DELTARUNE_AGENT_DATA_DIR"
PROFILE_SCHEMA_VERSION = 1
DEFAULT_PROFILE_NAME = "Default"
LEGACY_DATA_DIRECTORIES = ("memory", "runs")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def default_data_root() -> Path:
    override = os.environ.get(DATA_DIRECTORY_ENV)
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / APP_DIRECTORY_NAME
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home) / APP_DIRECTORY_NAME
    return Path.home() / ".local" / "share" / APP_DIRECTORY_NAME


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _clean_profile_name(name: str) -> str:
    cleaned = " ".join(str(name).split())
    if not cleaned:
        raise ValueError("Profile name cannot be blank.")
    if len(cleaned) > 80:
        raise ValueError("Profile name must be 80 characters or fewer.")
    return cleaned


def _tree_snapshot(path: Path) -> dict[str, int]:
    if not path.is_dir():
        return {}
    return {
        str(file.relative_to(path)): file.stat().st_size
        for file in path.rglob("*")
        if file.is_file()
    }


def _copy_verified(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True)
    source_snapshot = _tree_snapshot(source)
    destination_snapshot = _tree_snapshot(destination)
    missing_or_changed = {
        relative: size
        for relative, size in source_snapshot.items()
        if destination_snapshot.get(relative) != size
    }
    if missing_or_changed:
        sample = next(iter(missing_or_changed))
        raise OSError(f"Copy verification failed for {source / sample}")


def _is_directory_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(os.path, "isjunction", None)
    if is_junction and is_junction(path):
        return True
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _remove_directory_link(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
        return
    if _is_directory_link(path):
        os.rmdir(path)
        return
    raise OSError(f"Refusing to remove non-link directory: {path}")


def _create_directory_link(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            creationflags=creation_flags,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown error").strip()
            raise OSError(f"Could not create Windows junction {link}: {detail}")
        return
    link.symlink_to(target, target_is_directory=True)


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    created_at: str
    last_used_at: str


@dataclass(frozen=True)
class MigrationResult:
    migrated: tuple[str, ...] = ()
    backup_directory: Path | None = None


class ProfileStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_data_root()).expanduser().resolve()
        self.profiles_root = self.root / "profiles"
        self.index_path = self.root / "profiles.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.profiles_root.mkdir(parents=True, exist_ok=True)
        self._index = self._load_or_create_index()

    def _load_or_create_index(self) -> dict[str, object]:
        payload: dict[str, object] = {}
        try:
            loaded = json.loads(self.index_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, json.JSONDecodeError):
            payload = {}

        records = payload.get("profiles")
        valid_records: list[dict[str, str]] = []
        if isinstance(records, list):
            for record in records:
                if not isinstance(record, dict):
                    continue
                profile_id = str(record.get("id") or "").strip()
                name = str(record.get("name") or "").strip()
                if not profile_id or not name:
                    continue
                created = str(record.get("created_at") or _utc_now())
                last_used = str(record.get("last_used_at") or created)
                valid_records.append(
                    {
                        "id": profile_id,
                        "name": name,
                        "created_at": created,
                        "last_used_at": last_used,
                    }
                )

        if not valid_records:
            now = _utc_now()
            profile_id = uuid4().hex
            valid_records = [
                {
                    "id": profile_id,
                    "name": DEFAULT_PROFILE_NAME,
                    "created_at": now,
                    "last_used_at": now,
                }
            ]
            active_id = profile_id
        else:
            requested_active = str(payload.get("active_profile_id") or "")
            known_ids = {record["id"] for record in valid_records}
            active_id = (
                requested_active
                if requested_active in known_ids
                else valid_records[0]["id"]
            )

        normalized: dict[str, object] = {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "active_profile_id": active_id,
            "profiles": valid_records,
        }
        for record in valid_records:
            self._ensure_profile_directories(record["id"])
        _atomic_write_json(self.index_path, normalized)
        return normalized

    def _save(self) -> None:
        _atomic_write_json(self.index_path, self._index)

    def _records(self) -> list[dict[str, str]]:
        records = self._index.get("profiles")
        assert isinstance(records, list)
        return records

    def _ensure_profile_directories(self, profile_id: str) -> None:
        directory = self.profiles_root / profile_id
        (directory / "memory").mkdir(parents=True, exist_ok=True)
        (directory / "runs").mkdir(parents=True, exist_ok=True)

    def profiles(self) -> list[Profile]:
        return [Profile(**record) for record in self._records()]

    def get(self, profile_id: str) -> Profile:
        for profile in self.profiles():
            if profile.id == profile_id:
                return profile
        raise KeyError(f"Unknown profile: {profile_id}")

    def active(self) -> Profile:
        return self.get(str(self._index["active_profile_id"]))

    def profile_directory(self, profile_id: str) -> Path:
        self.get(profile_id)
        return self.profiles_root / profile_id

    def memory_directory(self, profile_id: str) -> Path:
        return self.profile_directory(profile_id) / "memory"

    def runs_directory(self, profile_id: str) -> Path:
        return self.profile_directory(profile_id) / "runs"

    def _ensure_unique_name(self, name: str, *, exclude_id: str | None = None) -> str:
        cleaned = _clean_profile_name(name)
        duplicate = next(
            (
                profile
                for profile in self.profiles()
                if profile.id != exclude_id
                and profile.name.casefold() == cleaned.casefold()
            ),
            None,
        )
        if duplicate is not None:
            raise ValueError(f'A profile named "{cleaned}" already exists.')
        return cleaned

    def create(
        self,
        name: str,
        *,
        source_profile_id: str | None = None,
        include_runs: bool = False,
    ) -> Profile:
        cleaned = self._ensure_unique_name(name)
        now = _utc_now()
        profile = Profile(uuid4().hex, cleaned, now, now)
        self._ensure_profile_directories(profile.id)
        destination_root = self.profiles_root / profile.id
        if source_profile_id is not None:
            self.get(source_profile_id)
            _copy_verified(
                self.memory_directory(source_profile_id),
                destination_root / "memory",
            )
            if include_runs:
                _copy_verified(
                    self.runs_directory(source_profile_id),
                    destination_root / "runs",
                )
        self._records().append(
            {
                "id": profile.id,
                "name": profile.name,
                "created_at": profile.created_at,
                "last_used_at": profile.last_used_at,
            }
        )
        self._save()
        return profile

    def rename(self, profile_id: str, name: str) -> Profile:
        cleaned = self._ensure_unique_name(name, exclude_id=profile_id)
        for record in self._records():
            if record["id"] == profile_id:
                record["name"] = cleaned
                self._save()
                return Profile(**record)
        raise KeyError(f"Unknown profile: {profile_id}")

    def delete(self, profile_id: str) -> None:
        records = self._records()
        if len(records) <= 1:
            raise ValueError("The last profile cannot be deleted.")
        self.get(profile_id)
        records[:] = [record for record in records if record["id"] != profile_id]
        if self._index.get("active_profile_id") == profile_id:
            self._index["active_profile_id"] = records[0]["id"]
        shutil.rmtree(self.profiles_root / profile_id, ignore_errors=False)
        self._save()

    def set_active(self, profile_id: str) -> Profile:
        self.get(profile_id)
        now = _utc_now()
        for record in self._records():
            if record["id"] == profile_id:
                record["last_used_at"] = now
                break
        self._index["active_profile_id"] = profile_id
        self._save()
        return self.get(profile_id)

    def profile_file_counts(self, profile_id: str) -> tuple[int, int]:
        self.get(profile_id)
        memory_count = len(_tree_snapshot(self.memory_directory(profile_id)))
        run_count = sum(
            1 for item in self.runs_directory(profile_id).iterdir() if item.is_dir()
        )
        return memory_count, run_count

    def migrate_legacy_data(self, project_root: Path) -> MigrationResult:
        project_root = project_root.resolve()
        default_profile = self.active()
        candidates = [
            name
            for name in LEGACY_DATA_DIRECTORIES
            if (project_root / name).is_dir()
            and not _is_directory_link(project_root / name)
        ]
        if not candidates:
            return MigrationResult()

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_root = self.root / "migration-backups" / timestamp
        migrated: list[str] = []
        for name in candidates:
            source = project_root / name
            destination = self.profile_directory(default_profile.id) / name
            backup = backup_root / name
            _copy_verified(source, destination)
            _copy_verified(source, backup)
            shutil.rmtree(source)
            migrated.append(name)

        marker_path = self.root / "migration-history.json"
        history: list[dict[str, object]] = []
        try:
            loaded = json.loads(marker_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                history = loaded
        except (OSError, json.JSONDecodeError):
            pass
        history.append(
            {
                "migrated_at": _utc_now(),
                "project_root": str(project_root),
                "profile_id": default_profile.id,
                "directories": migrated,
                "backup_directory": str(backup_root),
            }
        )
        _atomic_write_json(marker_path, history)
        return MigrationResult(tuple(migrated), backup_root)

    def activate(self, project_root: Path, profile_id: str) -> Profile:
        project_root = project_root.resolve()
        profile = self.get(profile_id)
        self._ensure_profile_directories(profile_id)
        targets = {
            "memory": self.memory_directory(profile_id),
            "runs": self.runs_directory(profile_id),
        }
        for name, target in targets.items():
            link = project_root / name
            if os.path.lexists(link):
                if not _is_directory_link(link):
                    raise OSError(
                        f"{link} is still a real directory. Run the profile migration "
                        "before activating a profile."
                    )
                try:
                    if link.resolve() == target.resolve():
                        continue
                except OSError:
                    pass
                _remove_directory_link(link)
            _create_directory_link(link, target)
        return self.set_active(profile.id)


def profile_names(profiles: Iterable[Profile]) -> list[str]:
    return [profile.name for profile in profiles]
