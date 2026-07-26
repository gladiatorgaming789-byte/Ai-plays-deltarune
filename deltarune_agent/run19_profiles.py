from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

from .profiles import (
    LEGACY_DATA_DIRECTORIES,
    MigrationResult,
    Profile,
    ProfileStore as BaseProfileStore,
    _atomic_write_json,
    _copy_verified,
    _create_directory_link,
    _is_directory_link,
    _remove_directory_link,
    _utc_now,
)


class ProfileStore(BaseProfileStore):
    """Failure-safe profile storage compatible with the original schema.

    The Run 18 store updates several filesystem and in-memory structures in a
    single operation. This subclass preserves the same public API and on-disk
    format, but stages destructive changes and restores the previous state when
    a later step fails.
    """

    def _restore_index(self, snapshot: dict[str, object]) -> None:
        self._index = snapshot

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
        final_root = self.profiles_root / profile.id
        staging_root = self.profiles_root / f".{profile.id}.{uuid4().hex}.creating"
        snapshot = deepcopy(self._index)

        try:
            (staging_root / "memory").mkdir(parents=True, exist_ok=False)
            (staging_root / "runs").mkdir(parents=True, exist_ok=False)
            if source_profile_id is not None:
                self.get(source_profile_id)
                _copy_verified(
                    self.memory_directory(source_profile_id),
                    staging_root / "memory",
                )
                if include_runs:
                    _copy_verified(
                        self.runs_directory(source_profile_id),
                        staging_root / "runs",
                    )
            os.replace(staging_root, final_root)
            self._records().append(
                {
                    "id": profile.id,
                    "name": profile.name,
                    "created_at": profile.created_at,
                    "last_used_at": profile.last_used_at,
                }
            )
            self._save()
        except BaseException:
            self._restore_index(snapshot)
            shutil.rmtree(staging_root, ignore_errors=True)
            shutil.rmtree(final_root, ignore_errors=True)
            raise
        return profile

    def rename(self, profile_id: str, name: str) -> Profile:
        cleaned = self._ensure_unique_name(name, exclude_id=profile_id)
        snapshot = deepcopy(self._index)
        try:
            for record in self._records():
                if record["id"] == profile_id:
                    record["name"] = cleaned
                    self._save()
                    return Profile(**record)
        except BaseException:
            self._restore_index(snapshot)
            raise
        raise KeyError(f"Unknown profile: {profile_id}")

    def set_active(self, profile_id: str) -> Profile:
        self.get(profile_id)
        snapshot = deepcopy(self._index)
        try:
            now = _utc_now()
            for record in self._records():
                if record["id"] == profile_id:
                    record["last_used_at"] = now
                    break
            self._index["active_profile_id"] = profile_id
            self._save()
        except BaseException:
            self._restore_index(snapshot)
            raise
        return self.get(profile_id)

    def delete(self, profile_id: str) -> None:
        records = self._records()
        if len(records) <= 1:
            raise ValueError("The last profile cannot be deleted.")
        self.get(profile_id)

        source = self.profiles_root / profile_id
        staging = self.profiles_root / f".{profile_id}.{uuid4().hex}.deleting"
        snapshot = deepcopy(self._index)
        os.replace(source, staging)
        try:
            records[:] = [record for record in records if record["id"] != profile_id]
            if self._index.get("active_profile_id") == profile_id:
                self._index["active_profile_id"] = records[0]["id"]
            self._save()
        except BaseException:
            self._restore_index(snapshot)
            os.replace(staging, source)
            raise

        # The profile is already committed as deleted. A cleanup failure should
        # not resurrect it or corrupt profiles.json; the hidden staging folder
        # can be removed safely on a later launch.
        shutil.rmtree(staging, ignore_errors=True)

    def _link_target(self, link: Path) -> Path | None:
        if not os.path.lexists(link):
            return None
        if not _is_directory_link(link):
            raise OSError(
                f"{link} is still a real directory. Run the profile migration "
                "before activating a profile."
            )
        try:
            return link.resolve()
        except OSError:
            return None

    def _restore_links(
        self,
        project_root: Path,
        previous: dict[str, Path | None],
        changed: list[str],
    ) -> None:
        rollback_error: BaseException | None = None
        for name in reversed(changed):
            link = project_root / name
            try:
                if os.path.lexists(link) and _is_directory_link(link):
                    _remove_directory_link(link)
                old_target = previous[name]
                if old_target is not None:
                    _create_directory_link(link, old_target)
            except BaseException as exc:
                if rollback_error is None:
                    rollback_error = exc
        if rollback_error is not None:
            raise OSError(f"Profile link rollback failed: {rollback_error}") from rollback_error

    def activate(self, project_root: Path, profile_id: str) -> Profile:
        project_root = project_root.resolve()
        profile = self.get(profile_id)
        self._ensure_profile_directories(profile_id)
        targets = {
            "memory": self.memory_directory(profile_id),
            "runs": self.runs_directory(profile_id),
        }
        previous = {
            name: self._link_target(project_root / name) for name in targets
        }
        changed: list[str] = []

        try:
            for name, target in targets.items():
                link = project_root / name
                old_target = previous[name]
                try:
                    if old_target is not None and old_target.resolve() == target.resolve():
                        continue
                except OSError:
                    pass
                if os.path.lexists(link):
                    _remove_directory_link(link)
                _create_directory_link(link, target)
                changed.append(name)
            return self.set_active(profile.id)
        except BaseException as operation_error:
            try:
                self._restore_links(project_root, previous, changed)
            except BaseException as rollback_error:
                raise OSError(
                    f"Profile activation failed ({operation_error}); rollback also failed "
                    f"({rollback_error})."
                ) from operation_error
            raise

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
        staged: dict[str, Path] = {}

        # Copy and verify every source twice before moving any original data.
        for name in candidates:
            source = project_root / name
            destination = self.profile_directory(default_profile.id) / name
            backup = backup_root / name
            _copy_verified(source, destination)
            _copy_verified(source, backup)

        try:
            for name in candidates:
                source = project_root / name
                hidden = project_root / f".{name}.{uuid4().hex}.migrating"
                os.replace(source, hidden)
                staged[name] = hidden

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
                    "directories": list(candidates),
                    "backup_directory": str(backup_root),
                }
            )
            _atomic_write_json(marker_path, history)
        except BaseException:
            for name, hidden in reversed(tuple(staged.items())):
                source = project_root / name
                if hidden.exists() and not source.exists():
                    os.replace(hidden, source)
            raise

        for hidden in staged.values():
            shutil.rmtree(hidden, ignore_errors=True)
        return MigrationResult(tuple(candidates), backup_root)


__all__ = ["MigrationResult", "Profile", "ProfileStore"]
