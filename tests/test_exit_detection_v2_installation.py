from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def test_production_stack_installs_v2_in_final_order_and_persists_metadata(
    tmp_path: Path,
) -> None:
    memory_path = tmp_path / "navigation.json"
    script = f'''
from pathlib import Path
from deltarune_agent.hierarchical_policy import HierarchicalPolicy
from deltarune_agent.policy import StarterPolicy
from deltarune_agent.run4_explorer import Run4Explorer
from deltarune_agent.version import AGENT_REVISION
from deltarune_agent.world_model import WorldModel

assert StarterPolicy._observe_screen.__module__.endswith('exit_detection_v2')
assert StarterPolicy._confirm_visual_exit.__module__.endswith('exit_detection_v2_transition_guard')
assert Run4Explorer._visual_exit_is_actionable.__module__.endswith('exit_detection_v2')
assert Run4Explorer.summary.__module__.endswith('exit_detection_v2')
assert AGENT_REVISION.endswith('-exit-detection-v2')

policy = HierarchicalPolicy()
summary = policy.summary()
assert summary['exit_detection_version'] == 2

path = Path({str(memory_path)!r})
model = WorldModel(path)
key = ('room_test', 2, 3)
model.screen_regions[key] = {{
    'views': 2,
    'independent_views': 2,
    'interest': 0.5,
    'hypothesis': None,
    'guess_state': 'proposed',
    'visual_summary': 'rectangular doorway facade near the upper wall (frame score 82%)',
    'edge_opening_score': 0.82,
    'edge_width_ratio': 0.25,
    'exit_detection_version': 2,
    'exit_candidate_source': 'doorway_facade',
    'exit_candidate_state': 'needs_approach_evidence',
    'exit_candidate_visual_score': 0.51,
    'exit_candidate_views': 2,
    'exit_candidate_viewpoints': [[0, 0], [1, 0]],
    'exit_candidate_last_step': 40,
    'exit_candidate_reasons': ['stable shape', 'needs learned approach'],
    'exit_candidate_promotions': 0,
    'exit_approach_length': 1,
}}
model.save()
loaded = WorldModel.load(path)
record = loaded.screen_regions[key]
assert record['exit_detection_version'] == 2
assert record['exit_candidate_source'] == 'doorway_facade'
assert record['exit_candidate_state'] == 'needs_approach_evidence'
assert record['exit_candidate_views'] == 2
assert record['exit_candidate_viewpoints'] == [[0, 0], [1, 0]]
assert record['exit_approach_length'] == 1
'''
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
