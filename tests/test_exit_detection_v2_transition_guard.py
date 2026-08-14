from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from deltarune_agent.exit_detection_v2_transition_guard import (
    movement_crossing_is_confirmable,
)
from deltarune_agent.run4_explorer import Run4Explorer


def test_only_cardinal_current_movement_is_confirmable() -> None:
    explorer = Run4Explorer()

    explorer.last_movement = None
    assert not movement_crossing_is_confirmable(explorer)

    explorer.last_movement = "confirm"
    assert not movement_crossing_is_confirmable(explorer)

    for direction in ("up", "down", "left", "right"):
        explorer.last_movement = direction
        assert movement_crossing_is_confirmable(explorer)


def test_production_stack_does_not_confirm_scripted_room_change() -> None:
    script = r'''
from deltarune_agent.hierarchical_policy import HierarchicalPolicy

policy = HierarchicalPolicy()
explorer = policy.explorer
key = ('room_a', 2, 2)
explorer.screen_regions[key] = {
    'views': 2,
    'independent_views': 2,
    'interest': 0.5,
    'hypothesis': None,
    'guess_state': 'proposed',
    'visual_summary': 'rectangular doorway facade near the upper wall (frame score 82%)',
    'edge_opening_score': 0.82,
    'edge_width_ratio': 0.25,
    'anchor_cell': [8, 8],
    'exit_detection_version': 2,
    'exit_candidate_source': 'doorway_facade',
    'exit_candidate_state': 'visual_candidate',
    'exit_candidate_visual_score': 0.45,
    'last_seen_step': 40,
    'last_seen_sequence': 4,
}

# Scripted/non-movement room changes must not credit nearby scenery.
explorer.last_movement = None
explorer._confirm_visual_exit('room_a', (8, 8), 'room_scripted')
assert explorer.screen_regions[key]['guess_state'] == 'proposed'
assert explorer.screen_regions[key].get('confirmed_target_room') is None

# A later actual cardinal crossing at the same location is authoritative.
explorer.last_movement = 'right'
explorer._confirm_visual_exit('room_a', (8, 8), 'room_b')
record = explorer.screen_regions[key]
assert record['guess_state'] == 'confirmed'
assert record['hypothesis'] == 'possible_exit'
assert record['exit_candidate_state'] == 'confirmed'
assert record['confirmed_target_room'] == 'room_b'
'''
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
