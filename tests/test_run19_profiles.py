from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from deltarune_agent.run19_profiles import ProfileStore


def test_failed_duplicate_copy_leaves_no_profile_or_directory():
    with TemporaryDirectory() as directory:
        store = ProfileStore(Path(directory) / "data")
        source = store.active()
        original_ids = [profile.id for profile in store.profiles()]

        with patch(
            "deltarune_agent.run19_profiles._copy_verified",
            side_effect=OSError("copy failed"),
        ):
            with pytest.raises(OSError, match="copy failed"):
                store.create("Broken", source_profile_id=source.id)

        assert [profile.id for profile in store.profiles()] == original_ids
        assert not any(path.name.endswith(".creating") for path in store.profiles_root.iterdir())


def test_failed_index_save_rolls_back_profile_creation():
    with TemporaryDirectory() as directory:
        store = ProfileStore(Path(directory) / "data")
        original_ids = [profile.id for profile in store.profiles()]

        with patch.object(store, "_save", side_effect=OSError("save failed")):
            with pytest.raises(OSError, match="save failed"):
                store.create("Broken")

        assert [profile.id for profile in store.profiles()] == original_ids
        visible = [path for path in store.profiles_root.iterdir() if not path.name.startswith(".")]
        assert [path.name for path in visible] == original_ids


def test_failed_rename_restores_in_memory_index():
    with TemporaryDirectory() as directory:
        store = ProfileStore(Path(directory) / "data")
        profile = store.active()

        with patch.object(store, "_save", side_effect=OSError("save failed")):
            with pytest.raises(OSError, match="save failed"):
                store.rename(profile.id, "Renamed")

        assert store.get(profile.id).name == profile.name


def test_failed_delete_save_restores_profile_directory_and_index():
    with TemporaryDirectory() as directory:
        store = ProfileStore(Path(directory) / "data")
        doomed = store.create("Doomed")
        marker = store.memory_directory(doomed.id) / "navigation.json"
        marker.write_text("keep me", encoding="utf-8")

        with patch.object(store, "_save", side_effect=OSError("save failed")):
            with pytest.raises(OSError, match="save failed"):
                store.delete(doomed.id)

        assert store.get(doomed.id).name == "Doomed"
        assert marker.read_text(encoding="utf-8") == "keep me"


def test_failed_activation_restores_previous_links():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        project = root / "project"
        project.mkdir()
        store = ProfileStore(root / "data")
        first = store.active()
        second = store.create("Second")
        store.activate(project, first.id)

        original_memory = (project / "memory").resolve()
        original_runs = (project / "runs").resolve()

        real_create = __import__(
            "deltarune_agent.run19_profiles", fromlist=["_create_directory_link"]
        )._create_directory_link
        calls = 0

        def fail_second_link(link, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("link failed")
            return real_create(link, target)

        with patch(
            "deltarune_agent.run19_profiles._create_directory_link",
            side_effect=fail_second_link,
        ):
            with pytest.raises(OSError, match="link failed"):
                store.activate(project, second.id)

        assert (project / "memory").resolve() == original_memory
        assert (project / "runs").resolve() == original_runs
        assert store.active().id == first.id


def test_migration_failure_never_removes_original_data():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        project = root / "project"
        memory = project / "memory"
        runs = project / "runs"
        memory.mkdir(parents=True)
        runs.mkdir()
        (memory / "navigation.json").write_text("learned", encoding="utf-8")
        store = ProfileStore(root / "data")

        real_copy = __import__(
            "deltarune_agent.run19_profiles", fromlist=["_copy_verified"]
        )._copy_verified
        calls = 0

        def fail_late(source, destination):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("backup failed")
            return real_copy(source, destination)

        with patch(
            "deltarune_agent.run19_profiles._copy_verified",
            side_effect=fail_late,
        ):
            with pytest.raises(OSError, match="backup failed"):
                store.migrate_legacy_data(project)

        assert (memory / "navigation.json").read_text(encoding="utf-8") == "learned"
        assert runs.is_dir()
