import ast
from pathlib import Path

from kyo import prompts

NAMES = "GUIDE BRIEF PLAN HEAR NOTE CHECK READING AUDIT UNMIX TRAIL GATE SWAP SHAPE".split()

# Prompts the baseline agent has revised since the team copied them at version 1.0.0. The revisions describe
# behaviour of agent.py that the team does not have, so the team keeps its copy of these.
REVISED_IN_BASELINE = {
    "BRIEF",
}


def baseline_prompts():
    """Evaluate the prompt constants of agent.py without importing it."""
    source = (Path(__file__).parents[1] / "agent.py").read_text(encoding="utf-8")
    found = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") in NAMES:
            code = compile(ast.Expression(node.value), "agent.py", "eval")
            found[node.targets[0].id] = eval(code, {}, dict(found))
    return found


def test_shared_prompts_match_the_baseline_agent():
    baseline = baseline_prompts()
    assert sorted(baseline) == sorted(NAMES)
    differ = {name for name in NAMES if getattr(prompts, name) != baseline[name]}
    assert differ == REVISED_IN_BASELINE
