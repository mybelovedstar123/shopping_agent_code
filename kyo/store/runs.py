"""Run agent teams in the background, and let shoppers follow and answer them."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from ..logs import log, scrub
from ..models.base import Model
from ..runtime.trace import Trace
from ..team import run_team
from . import cart, checkout, payments
from .catalog import to_dollars
from .db import now, transaction
from .models import AgentEvent, AgentQuestion, AgentRun
from .tools import StoreTools

ACTIVE = ("running", "waiting")
MAX_ACTIVE_RUNS = 3
MAX_REQUEST_LENGTH = 2000
MAX_EVENT_TEXT = 500
# Trace payloads hold every store reply, so runs keep only what a person needs to read.
SHOWN_EVENT_TYPES = ("note", "failure")


class RunError(Exception):
    """A run could not be started or changed. The message is safe to show to the user."""


class _DatabaseShopper:
    """Puts questions in the database and waits for the shopper to answer them."""

    def __init__(self, manager: RunManager, run_id: int):
        self.manager = manager
        self.run_id = run_id

    def ask(self, question: str) -> str:
        answer = self.manager.wait_for_answer(self.run_id, "question", question, {})
        return answer or "I don't mind."

    def approve(self, summary: dict[str, Any]) -> dict[str, Any]:
        lines = ", ".join(f"{x['quantity']} x {x['title']}" for x in summary["lines"])
        text = f"Approve this purchase: {lines}, for a total of {summary['total']:.2f}?"
        data = {"lines": summary["lines"], "total_cents": summary["total_cents"]}
        answer = self.manager.wait_for_answer(self.run_id, "approval", text, data)
        if answer is None:
            return {"approved": False, "reason": "The shopper did not answer in time."}
        if answer.startswith("approved:"):
            return {"approved": True, "order_id": int(answer.split(":", 1)[1])}
        if answer.startswith("failed:"):
            return {"approved": False, "failed": answer.split(":", 1)[1].strip()}
        return {"approved": False, "reason": "The shopper did not approve this order."}


class RunManager:
    """Starts team runs on background threads and stores what they do."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        model_factory: Callable[[], Model | None] = lambda: None,
        question_timeout: float = 900.0,
        poll_seconds: float = 0.25,
        max_steps: int = 40,
    ):
        self.sessions = sessions
        self.model_factory = model_factory
        self.question_timeout = question_timeout
        self.poll_seconds = poll_seconds
        self.max_steps = max_steps
        self.threads: dict[int, threading.Thread] = {}

    def recover(self) -> int:
        """Runs left unfinished by a restart cannot continue, so they are marked as failed."""
        with transaction(self.sessions) as session:
            stuck = session.scalars(select(AgentRun).where(AgentRun.status.in_(ACTIVE))).all()
            for run in stuck:
                self._close(session, run, "failed", {"reason": "the server restarted"})
            return len(stuck)

    def start(self, user_id: int, request: str) -> int:
        request = scrub(request).strip()
        if not request:
            raise RunError("Tell the assistant what you would like to buy.")
        if len(request) > MAX_REQUEST_LENGTH:
            raise RunError(f"Requests can be at most {MAX_REQUEST_LENGTH} characters.")
        with transaction(self.sessions) as session:
            active = session.scalar(
                select(func.count())
                .select_from(AgentRun)
                .where(AgentRun.user_id == user_id, AgentRun.status.in_(ACTIVE))
            )
            if active >= MAX_ACTIVE_RUNS:
                raise RunError(
                    "You already have several assistants working. Wait for one to finish."
                )
            run = AgentRun(user_id=user_id, request=request)
            session.add(run)
            session.flush()
            run_id = run.id
        log.info("run started", extra={"run_id": run_id, "user_id": user_id})
        thread = threading.Thread(
            target=self._execute, args=(run_id, user_id, request), name=f"run-{run_id}", daemon=True
        )
        self.threads[run_id] = thread
        thread.start()
        return run_id

    def _execute(self, run_id: int, user_id: int, request: str) -> None:
        trace = Trace()
        trace.subscribe(lambda event: self._store_event(run_id, event))
        try:
            tools = StoreTools(self.sessions, user_id, run_id, _DatabaseShopper(self, run_id))
            result = run_team(
                request,
                tools,
                model=self.model_factory(),
                max_steps=self.max_steps,
                trace=trace,
                timeout=self.question_timeout * 20,
            )
            status = result["outcome"]
        except Exception as error:  # a failed run is reported to its shopper, not raised
            result = {"reason": f"{type(error).__name__}: {error}"[:300]}
            status = "failed"
            log.exception("run failed", extra={"run_id": run_id, "user_id": user_id})
        with transaction(self.sessions) as session:
            run = session.get(AgentRun, run_id)
            self._close(session, run, status, result)
        log.info(
            "run finished",
            extra={
                "run_id": run_id,
                "status": status,
                "steps": result.get("steps"),
                "model_review": result.get("model_review"),
            },
        )

    def _close(self, session: Session, run: AgentRun, status: str, result: dict[str, Any]) -> None:
        run.status = status
        run.result = result
        run.finished_at = now()
        if status != "purchased":
            cart.clear(session, run.user_id, run.id)
        for question in run.questions:
            if question.answer is None:
                question.answered_at = now()

    def _store_event(self, run_id: int, event: dict[str, Any]) -> None:
        if event["type"] not in SHOWN_EVENT_TYPES:
            return
        text = scrub(event.get("text") or event.get("error") or "")[:MAX_EVENT_TEXT]
        args = scrub(json.dumps(event["args"]))[:MAX_EVENT_TEXT] if event.get("args") else ""
        with transaction(self.sessions) as session:
            session.add(
                AgentEvent(
                    run_id=run_id,
                    seq=event["seq"],
                    type=event["type"],
                    agent=event["agent"],
                    data={"text": text, "args": args},
                )
            )

    def wait_for_answer(
        self, run_id: int, kind: str, text: str, data: dict[str, Any]
    ) -> str | None:
        """Store a question and wait for its answer. None means it timed out."""
        with transaction(self.sessions) as session:
            question = AgentQuestion(run_id=run_id, kind=kind, text=text, data=data)
            session.add(question)
            session.get(AgentRun, run_id).status = "waiting"
            session.flush()
            question_id = question.id
        deadline = time.monotonic() + self.question_timeout
        answer = None
        while time.monotonic() < deadline:
            with transaction(self.sessions) as session:
                current = session.get(AgentQuestion, question_id)
                if current.answered_at is not None:
                    answer = current.answer
                    break
            time.sleep(self.poll_seconds)
        with transaction(self.sessions) as session:
            # An answer can arrive after the last poll. Closing the question only if it is still
            # open means either the answer or the timeout wins, never both.
            if answer is None and not _close_question(session, question_id, None):
                answer = session.get(AgentQuestion, question_id).answer
            run = session.get(AgentRun, run_id)
            if run.status == "waiting":
                run.status = "running"
        return answer

    def join(self, run_id: int, timeout: float = 30.0) -> None:
        """Wait for a run to end. Used by tests and shutdown."""
        thread = self.threads.get(run_id)
        if thread:
            thread.join(timeout)


def _owned_run(session: Session, user_id: int, run_id: int) -> AgentRun | None:
    run = session.get(AgentRun, run_id)
    return run if run is not None and run.user_id == user_id else None


def question_view(question: AgentQuestion) -> dict[str, Any]:
    data = dict(question.data or {})
    total = data.get("total_cents")
    return {
        "id": question.id,
        "kind": question.kind,
        "text": question.text,
        "lines": data.get("lines"),
        "total": to_dollars(total) if total is not None else None,
        "answer": question.answer,
        "open": question.answered_at is None,
    }


def run_view(run: AgentRun, since: int = 0) -> dict[str, Any]:
    questions = [question_view(q) for q in run.questions]
    # A question and the waiting status are committed together but read separately here, so a
    # read between them could otherwise show an open question on a run that seems not to wait.
    waiting = run.status in ACTIVE and any(q["open"] for q in questions)
    return {
        "id": run.id,
        "request": run.request,
        "status": "waiting" if waiting else run.status,
        "result": run.result,
        "created_at": run.created_at.isoformat(timespec="seconds"),
        "finished": run.status not in ACTIVE,
        "events": [
            {
                "seq": e.seq,
                "type": e.type,
                "agent": e.agent,
                "text": e.data.get("text", ""),
                "args": e.data.get("args", ""),
            }
            for e in run.events
            if e.seq > since
        ],
        "questions": questions,
    }


def get_run(session: Session, user_id: int, run_id: int, since: int = 0) -> dict[str, Any] | None:
    run = _owned_run(session, user_id, run_id)
    return run_view(run, since) if run else None


def list_runs(session: Session, user_id: int) -> list[dict[str, Any]]:
    runs = session.scalars(
        select(AgentRun).where(AgentRun.user_id == user_id).order_by(AgentRun.id.desc()).limit(50)
    )
    return [
        {
            "id": r.id,
            "request": r.request,
            "status": r.status,
            "created_at": r.created_at.isoformat(timespec="seconds"),
        }
        for r in runs
    ]


def _open_question(
    session: Session, user_id: int, run_id: int, question_id: int, kind: str
) -> AgentQuestion:
    run = _owned_run(session, user_id, run_id)
    question = session.get(AgentQuestion, question_id)
    if run is None or question is None or question.run_id != run_id:
        raise LookupError("no such question")
    if question.kind != kind or question.answered_at is not None:
        raise RunError("This question has already been answered.")
    return question


def _close_question(session: Session, question_id: int, answer: str | None) -> bool:
    """Answer a question unless it is already closed. Only one of two racing closers succeeds."""
    closed = session.execute(
        update(AgentQuestion)
        .where(AgentQuestion.id == question_id, AgentQuestion.answered_at.is_(None))
        .values(answer=answer, answered_at=now())
        .execution_options(synchronize_session=False)
    )
    return closed.rowcount == 1


def answer_question(
    session: Session, user_id: int, run_id: int, question_id: int, answer: str
) -> None:
    question = _open_question(session, user_id, run_id, question_id, "question")
    if not answer.strip():
        raise RunError("Type an answer first.")
    question.answer = scrub(answer).strip()[:MAX_REQUEST_LENGTH]
    question.answered_at = now()


def decide_approval(
    sessions: sessionmaker[Session],
    user_id: int,
    run_id: int,
    question_id: int,
    approved: bool,
    card_number: str = "",
) -> dict[str, Any]:
    """Apply the shopper's decision. Approval places the order here, not in any agent.

    A declined card raises PaymentDeclined and leaves the question open, so the shopper
    can try another card.
    """
    with transaction(sessions) as session:
        question = _open_question(session, user_id, run_id, question_id, "approval")
        total = (question.data or {}).get("total_cents")
    if not approved:
        with transaction(sessions) as session:
            question = _open_question(session, user_id, run_id, question_id, "approval")
            question.answer = "declined"
            question.answered_at = now()
        return {"approved": False}
    key = f"run-{run_id}-question-{question_id}"
    try:
        with transaction(sessions) as session:
            order = checkout.place_order(
                session,
                user_id,
                card_number,
                idempotency_key=key,
                run_id=run_id,
                expected_total_cents=total,
            )
            _open_question(session, user_id, run_id, question_id, "approval")
            if not _close_question(session, question_id, f"approved:{order.id}"):
                # The run gave up waiting meanwhile. Raising undoes the order and the charge.
                raise RunError("This question has already been answered.")
            return {"approved": True, "order": checkout.order_view(order)}
    except payments.PaymentDeclined:
        raise
    except checkout.CheckoutError as error:
        with transaction(sessions) as session:
            question = _open_question(session, user_id, run_id, question_id, "approval")
            question.answer = f"failed: {error}"
            question.answered_at = now()
        return {"approved": False, "failed": str(error)}
