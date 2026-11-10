"""Endpoints for starting agent runs and following them."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..store import payments, runs
from .deps import CurrentUser, db

router = APIRouter(tags=["assistant"])
Db = Annotated[Session, Depends(db, scope="function")]


class NewRun(BaseModel):
    request: str


class Answer(BaseModel):
    answer: str


class Decision(BaseModel):
    approved: bool
    card_number: str = Field(default="", repr=False)


@router.post("/runs", status_code=201)
def start_run(body: NewRun, request: Request, user: CurrentUser) -> dict[str, int]:
    try:
        return {"id": request.app.state.runs.start(user.id, body.request)}
    except runs.RunError as error:
        raise HTTPException(422, str(error)) from None


@router.get("/runs")
def list_runs(user: CurrentUser, session: Db) -> list[dict[str, Any]]:
    return runs.list_runs(session, user.id)


@router.get("/runs/{run_id}")
def get_run(run_id: int, user: CurrentUser, session: Db, since: int = 0) -> dict[str, Any]:
    found = runs.get_run(session, user.id, run_id, since)
    if found is None:
        raise HTTPException(404, "no such run")
    return found


@router.post("/runs/{run_id}/questions/{question_id}/answer", status_code=204)
def answer_question(
    run_id: int, question_id: int, body: Answer, user: CurrentUser, session: Db
) -> None:
    try:
        runs.answer_question(session, user.id, run_id, question_id, body.answer)
    except LookupError:
        raise HTTPException(404, "no such question") from None
    except runs.RunError as error:
        raise HTTPException(409, str(error)) from None


@router.post("/runs/{run_id}/questions/{question_id}/decision")
def decide(
    run_id: int, question_id: int, body: Decision, request: Request, user: CurrentUser
) -> dict[str, Any]:
    try:
        return runs.decide_approval(
            request.app.state.sessions,
            user.id,
            run_id,
            question_id,
            body.approved,
            body.card_number,
        )
    except LookupError:
        raise HTTPException(404, "no such question") from None
    except runs.RunError as error:
        raise HTTPException(409, str(error)) from None
    except payments.PaymentDeclined as error:
        raise HTTPException(402, str(error)) from None
