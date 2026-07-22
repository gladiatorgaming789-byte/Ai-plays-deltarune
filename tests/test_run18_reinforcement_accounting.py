from __future__ import annotations

from deltarune_agent.run16_semantics import install_run16_semantics

install_run16_semantics()

from deltarune_agent.run18_reinforcement_accounting import Run18ReinforcementExplorer


def test_sync_totals_includes_direct_rewards():
    explorer = Run18ReinforcementExplorer()
    key = "interaction:room_test:1:2"
    explorer.reinforcement.begin_action(
        key,
        kind="interaction",
        context=None,
        step=1,
        settings=explorer.reward_settings,
    )
    explorer.reinforcement.reward_key(
        key,
        3.5,
        event="direct progress reward",
        step=2,
        kind="interaction",
    )

    explorer._sync_reward_totals()

    assert explorer.reinforcement.total_reward == 3.5
    assert explorer.reinforcement.reward_events == 1


def test_first_confirmed_interaction_receives_information_reward(monkeypatch):
    explorer = Run18ReinforcementExplorer()
    key = ("room_test", 4, 5)
    explorer.active_interaction_key = key
    explorer.interactables[key] = {
        "confirmations": 1,
        "attempts": 1,
        "dialogue_steps": 0,
        "cutscene_steps": 0,
        "progressions": 0,
        "choice_menus": 0,
        "classification": "unknown",
        "usefulness": "unknown",
        "last_outcome": "ordinary_dialogue",
        "outcome_counts": {},
        "approaches": [],
    }

    # Isolate the Run 18 repair from the older outcome finalizer. The record is
    # already in the state produced after a first confirmed interaction.
    monkeypatch.setattr(
        "deltarune_agent.run17_reinforcement.Run17ReinforcementExplorer._finish_active_interaction",
        lambda self, telemetry: None,
    )

    explorer._finish_active_interaction(None)

    record = explorer.reinforcement.records[explorer._interaction_key(key)]
    assert record["last_event"] == "first confirmed interaction"
    assert record["total_reward"] == explorer.reward_settings.reward(
        "information_gain"
    )


def test_summary_repairs_loaded_cached_totals():
    explorer = Run18ReinforcementExplorer()
    explorer.reinforcement.records["mode:room:search"] = {
        "kind": "navigation_mode",
        "context": {},
        "attempts": 2,
        "reward_count": 2,
        "total_reward": 7.0,
        "last_reward": 2.0,
        "positive_outcomes": 2,
        "negative_outcomes": 0,
        "last_step": 10,
        "last_event": "progress",
    }
    explorer.reinforcement.total_reward = 0.0
    explorer.reinforcement.reward_events = 0

    summary = explorer.summary()

    assert summary["reinforcement"]["total_reward"] == 7.0
    assert summary["reinforcement"]["reward_events"] == 2
