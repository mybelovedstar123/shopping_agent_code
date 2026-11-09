from pathlib import Path

from kyo.evaluate import evaluate, main, report_markdown, scenarios


def test_every_core_scenario_passes():
    failed = [o for o in evaluate() if o.scenario.level == "core" and not o.passed]
    assert not failed, [(o.scenario.name, o.got, o.reason) for o in failed]


def test_the_team_never_buys_the_wrong_thing_in_any_scenario():
    """Failing to buy is acceptable in a hard case. Buying the wrong thing never is."""
    wrong = [o for o in evaluate() if o.wrong_purchase]
    assert not wrong, [(o.scenario.name, o.got, o.reason) for o in wrong]


def test_scenarios_have_unique_names_and_cover_declining():
    names = [s.name for s in scenarios()]
    assert len(names) == len(set(names))
    assert any(s.expect is None for s in scenarios())
    assert any(s.level == "stretch" for s in scenarios())


def test_report_states_results_and_limits():
    report = report_markdown(evaluate())
    assert "Core scenarios passed:** 30 of 30" in report
    assert "Wrong purchases:** 0" in report
    assert "What these results do not prove" in report
    assert "No live model has been evaluated" in report
    assert "## Failures" in report, "known failures are published, not hidden"


def test_command_writes_the_report(tmp_path, capsys):
    out = tmp_path / "report.md"
    assert main(["--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# Evaluation")
    assert "Wrong purchases: 0" in capsys.readouterr().out


def test_published_report_is_up_to_date():
    published = Path(__file__).parents[1] / "docs" / "EVALUATION.md"
    assert published.read_text(encoding="utf-8") == report_markdown(evaluate())
