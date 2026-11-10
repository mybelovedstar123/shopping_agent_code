"""Whole episodes of the baseline agent: what it does, and what it refuses to spend steps on."""

from types import SimpleNamespace


def kinds(trace):
    return [event["kind"] for event in trace]


def tool_text(harness):
    return "\n".join(str(m["content"]) for m in harness.tool_results)


def test_buys_after_finding_and_carting_a_listing(play):
    trace, harness = play(
        [("find", {"q": "lamp"}), ("add", {"id": "SKU-1001", "n": 1}), ("buy", {})]
    )
    assert harness.store_calls[-1] == "buy"
    assert "find" in harness.store_calls and "add" in harness.store_calls
    assert trace[-1]["kind"] == "end" and trace[-1]["finished"] is True


def test_first_early_decline_is_held_and_the_agent_searches_before_ending(play):
    trace, harness = play([("decline", {}), ("decline", {})])
    assert kinds(trace).count("turn") == 2
    assert "search in ways you have not tried" in tool_text(harness)
    assert harness.store_calls.count("decline") == 1 and harness.store_calls[-1] == "decline"
    assert "find" in harness.store_calls, "it searches on its own before ending"


def test_schema_violation_is_rejected_without_a_store_call(play):
    _, harness = play([("add", {"id": "SKU-1001", "n": 0}), ("decline", {}), ("decline", {})])
    assert "add" not in harness.store_calls
    assert "Not run (no step used): args.n is below 1" in tool_text(harness)


def test_unknown_tool_is_rejected_without_a_store_call(play):
    _, harness = play([("teleport", {}), ("decline", {}), ("decline", {})])
    assert "teleport" not in harness.store_calls
    assert "unknown tool" in tool_text(harness)


def test_repeated_identical_read_is_answered_from_memory(play):
    _, harness = play(
        [("find", {"q": "lamp"}), ("find", {"q": "lamp"}), ("add", {"id": "SKU-1001", "n": 1}), ("buy", {})]
    )
    assert harness.store_calls.count("find") == 1
    assert "you just made this same call" in tool_text(harness)


def test_step_conflict_resynchronises_and_retries(play):
    seen = []

    def store(body):
        seen.append(body["step"])
        if len(seen) == 1:
            error = {"status": 409, "body": "wrong step, expected 7"}
            return SimpleNamespace(data=None, error=error)
        name = body["action"]["name"]
        return {"step": body["step"], "finished": name == "decline", "observation": {"ok": True}}

    play([("find", {"q": "lamp"}), ("decline", {}), ("decline", {})], store)
    assert seen[:2] == [1, 7]


def test_payment_required_from_the_store_stops_the_episode(play):
    def store(body):
        return SimpleNamespace(data=None, error={"status": 402, "body": "no funds"})

    trace, harness = play([("find", {"q": "lamp"}), ("find", {"q": "x"})], store)
    assert harness.store_calls == ["find"]
    assert trace[-1]["finished"] is False


def test_store_binding_and_step_are_sent_with_every_action(play):
    bodies = []

    def store(body):
        bodies.append(body)
        name = body["action"]["name"]
        return {"step": body["step"], "finished": name == "decline", "observation": {"ok": True}}

    play([("find", {"q": "lamp"}), ("decline", {}), ("decline", {})], store)
    assert bodies[0]["episode"] == "test"
    assert [b["step"] for b in bodies] == list(range(1, len(bodies) + 1))


def test_bad_problem_returns_a_crash_event(agent):
    trace = agent.agent_main({})
    assert trace == [{"kind": "crash", "error": "KeyError: 'environment'"}]


def test_exception_during_a_run_still_returns_the_trace(play, monkeypatch, agent):
    def explode(self, *args):
        raise RuntimeError("boom")

    monkeypatch.setattr(agent.Kyo, "work", explode)
    trace, harness = play([])
    assert [e["error"] for e in trace if e["kind"] == "crash"] == ["RuntimeError: boom"]
    assert all("at" in event for event in trace), "every event says when it happened"
    assert harness.store_calls == ["decline"], "it still tries to end the episode after a crash"


def test_exception_in_a_turn_is_recorded_and_the_episode_goes_on(play, monkeypatch, agent):
    real = agent.Kyo.turns
    raised = []

    def flaky(self, *args):
        if not raised:
            raised.append(True)
            raise RuntimeError("boom")
        return real(self, *args)

    monkeypatch.setattr(agent.Kyo, "turns", flaky)
    trace, harness = play(
        [("find", {"q": "lamp"}), ("add", {"id": "SKU-1001", "n": 1}), ("buy", {})]
    )
    assert [e["error"] for e in trace if e["kind"] == "crash"] == ["RuntimeError: boom"]
    assert harness.store_calls[-1] == "buy"
