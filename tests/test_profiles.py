from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from deltarune_agent.profiles import ProfileStore


def test_profile_store_creates_default_appdata_layout():
    with TemporaryDirectory() as directory:
        store = ProfileStore(Path(directory) / "data")
        profile = store.active()

        assert profile.name == "Default"
        assert store.memory_directory(profile.id).is_dir()
        assert store.runs_directory(profile.id).is_dir()


def test_profiles_keep_independent_memory_and_runs():
    with TemporaryDirectory() as directory:
        store = ProfileStore(Path(directory) / "data")
        first = store.active()
        (store.memory_directory(first.id) / "navigation.json").write_text(
            "first", encoding="utf-8"
        )
        second = store.create("Second")
        (store.memory_directory(second.id) / "navigation.json").write_text(
            "second", encoding="utf-8"
        )

        assert (
            store.memory_directory(first.id) / "navigation.json"
        ).read_text(encoding="utf-8") == "first"
        assert (
            store.memory_directory(second.id) / "navigation.json"
        ).read_text(encoding="utf-8") == "second"


def test_duplicate_profile_copies_memory_and_runs():
    with TemporaryDirectory() as directory:
        store = ProfileStore(Path(directory) / "data")
        source = store.active()
        (store.memory_directory(source.id) / "navigation.json").write_text(
            "memory", encoding="utf-8"
        )
        run = store.runs_directory(source.id) / "run-001"
        run.mkdir()
        (run / "run.json").write_text("{}", encoding="utf-8")

        duplicate = store.create(
            "Copy",
            source_profile_id=source.id,
            include_runs=True,
        )

        assert (
            store.memory_directory(duplicate.id) / "navigation.json"
        ).read_text(encoding="utf-8") == "memory"
        assert (store.runs_directory(duplicate.id) / "run-001" / "run.json").is_file()


def test_profile_names_are_unique_case_insensitively():
    with TemporaryDirectory() as directory:
        store = ProfileStore(Path(directory) / "data")
        store.create("Testing")

        with pytest.raises(ValueError, match="already exists"):
            store.create("testing")


def test_migration_copies_to_profile_and_keeps_verified_backup():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        project = root / "project"
        project.mkdir()
        memory = project / "memory"
        runs = project / "runs" / "run-001"
        memory.mkdir()
        runs.mkdir(parents=True)
        (memory / "navigation.json").write_text("learned", encoding="utf-8")
        (runs / "run.json").write_text("{}", encoding="utf-8")

        store = ProfileStore(root / "appdata")
        result = store.migrate_legacy_data(project)
        active = store.active()

        assert result.migrated == ("memory", "runs")
        assert not (project / "memory").exists()
        assert not (project / "runs").exists()
        assert (
            store.memory_directory(active.id) / "navigation.json"
        ).read_text(encoding="utf-8") == "learned"
        assert result.backup_directory is not None
        assert (
            result.backup_directory / "memory" / "navigation.json"
        ).read_text(encoding="utf-8") == "learned"
        assert (result.backup_directory / "runs" / "run-001" / "run.json").is_file()


def test_last_profile_cannot_be_deleted():
    with TemporaryDirectory() as directory:
        store = ProfileStore(Path(directory) / "data")

        with pytest.raises(ValueError, match="last profile"):
            store.delete(store.active().id)
