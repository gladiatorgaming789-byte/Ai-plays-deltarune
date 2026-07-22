from __future__ import annotations

from pathlib import Path

import pytest

from deltarune_agent.reinforcement import (
    CUSTOM_PRESET,
    PRESETS,
    ReinforcementMemory,
    RewardSettings,
    load_reward_settings,
    save_reward_settings,
)


def test_every_preset_round_trips_through_disk(tmp_path: Path):
    path = tmp_path / "reinforcement_settings.json"
    for name in PRESETS:
        settings = RewardSettings.for_preset(name)
        save_reward_settings(path, settings)
        loaded = load_reward_settings(path)
        assert loaded.detect_preset() == name
        assert loaded.to_dict() == settings.to_dict()


def test_editing_one_value_becomes_custom():
    normal = RewardSettings.for_preset("Normal")
    rewards = dict(normal.rewards)
    rewards["story_progress"] += 0.25
    custom = RewardSettings(
        enabled=normal.enabled,
        preset="Normal",
        exploration_constant=normal.exploration_constant,
        eligibility_decay=normal.eligibility_decay,
        trace_length=normal.trace_length,
        decision_repeat_steps=normal.decision_repeat_steps,
        rewards=rewards,
    )

    assert custom.detect_preset() == CUSTOM_PRESET


def test_invalid_decay_is_rejected():
    settings = RewardSettings.for_preset("Normal")
    invalid = RewardSettings(
        enabled=True,
        preset="Custom",
        exploration_constant=settings.exploration_constant,
        eligibility_decay=1.2,
        trace_length=settings.trace_length,
        decision_repeat_steps=settings.decision_repeat_steps,
        rewards=dict(settings.rewards),
    )

    with pytest.raises(ValueError):
        invalid.validate()


def test_ucb_gives_untried_action_an_exploration_bonus():
    settings = RewardSettings.for_preset("Normal")
    memory = ReinforcementMemory()
    memory.begin_action(
        "tested",
        kind="interaction",
        context=None,
        step=1,
        settings=settings,
    )
    memory.reward_key(
        "tested",
        0.0,
        event="neutral",
        step=1,
        kind="interaction",
    )

    assert memory.score("untried", settings) > memory.score("tested", settings)


def test_delayed_reward_decays_across_trace():
    settings = RewardSettings.for_preset("Normal")
    memory = ReinforcementMemory()
    memory.begin_action(
        "older",
        kind="navigation_mode",
        context=None,
        step=1,
        settings=settings,
    )
    memory.begin_action(
        "newer",
        kind="interaction",
        context=None,
        step=100,
        settings=settings,
    )

    memory.reward_trace(
        10.0,
        event="progress",
        step=101,
        settings=settings,
    )

    assert memory.records["newer"]["total_reward"] == pytest.approx(10.0)
    assert memory.records["older"]["total_reward"] == pytest.approx(
        10.0 * settings.eligibility_decay
    )


def test_memory_persists_records_but_not_runtime_trace(tmp_path: Path):
    path = tmp_path / "reinforcement.json"
    settings = RewardSettings.for_preset("Normal")
    memory = ReinforcementMemory(path)
    memory.begin_action(
        "interaction:room:1:2",
        kind="interaction",
        context={"room": "room", "x": 1, "y": 2},
        step=5,
        settings=settings,
    )
    memory.reward_trace(4.0, event="progress", step=6, settings=settings)
    memory.flush(force=True)

    loaded = ReinforcementMemory.load(path)

    assert loaded.records["interaction:room:1:2"]["total_reward"] == pytest.approx(4.0)
    assert loaded.total_decisions == 1
    assert loaded.reward_events == 1
    assert not loaded.trace


def test_disabled_settings_do_not_start_actions():
    base = RewardSettings.for_preset("Normal")
    disabled = RewardSettings(
        enabled=False,
        preset="Custom",
        exploration_constant=base.exploration_constant,
        eligibility_decay=base.eligibility_decay,
        trace_length=base.trace_length,
        decision_repeat_steps=base.decision_repeat_steps,
        rewards=dict(base.rewards),
    )
    memory = ReinforcementMemory()

    assert not memory.begin_action(
        "anything",
        kind="test",
        context=None,
        step=1,
        settings=disabled,
    )
    assert memory.total_decisions == 0
