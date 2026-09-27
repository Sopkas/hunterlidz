"""Inbound reply classification contract reply_v1 and code-level safety rules. (H-09)

Doc 11 "Классификация ответа". The model only proposes; the operator confirms. Two rules are
enforced by code whatever the model says:
- an explicit "do not write" is opt_out (T-15);
- a month without a year, or a vague "later", gives no return date (T-16).
Any reply pauses the chain before classification; that happens in the use case, not here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from hunter.domain.listings import normalize_text
from hunter.domain.research import SchemaError

SCHEMA_VERSION = "reply_v1"


class ReplyClass(StrEnum):
    POSITIVE = "positive"
    QUESTION = "question"
    LATER = "later"
    NOT_INTERESTED = "not_interested"
    WRONG_PERSON = "wrong_person"
    OPT_OUT = "opt_out"
    AUTO_REPLY = "auto_reply"
    UNCLEAR = "unclear"


class ReplyAction(StrEnum):
    QUALIFY = "qualify"
    ANSWER_QUESTION = "answer_question"
    NURTURE = "nurture"
    CLOSE = "close"
    REROUTE = "reroute"
    SUPPRESS = "suppress"
    REVIEW = "review"


DEFAULT_ACTION = {
    ReplyClass.POSITIVE: ReplyAction.QUALIFY,
    ReplyClass.QUESTION: ReplyAction.ANSWER_QUESTION,
    ReplyClass.LATER: ReplyAction.NURTURE,
    ReplyClass.NOT_INTERESTED: ReplyAction.CLOSE,
    ReplyClass.WRONG_PERSON: ReplyAction.REROUTE,
    ReplyClass.OPT_OUT: ReplyAction.SUPPRESS,
    ReplyClass.AUTO_REPLY: ReplyAction.REVIEW,
    ReplyClass.UNCLEAR: ReplyAction.REVIEW,
}

_OPT_OUT = re.compile(
    r"не пиши(те)?|больше не (пиши|обраща|беспоко)|не беспокой|отпиш|удалите (мой|наш)|"
    r"прекратите|не присылайте|unsubscribe|stop writing|do not contact"
)
_MONTHS = r"январ|феврал|март|апрел|ма[йея]\b|июн|июл|август|сентябр|октябр|ноябр|декабр"
# \w*\b keeps the whole word: "в ноябре 2026" must not match by backtracking inside the word.
_MONTH_WITHOUT_YEAR = re.compile(rf"\b(в|к|после|до)\s+({_MONTHS})\w*\b(?!\s*\d{{4}})")
_VAGUE = re.compile(r"\bпотом\b|\bпозже\b|как.нибудь|\bкогда.нибудь\b|later")


@dataclass(frozen=True, slots=True)
class ReplyProposal:
    reply_class: ReplyClass
    supporting_excerpt: str
    suggested_action: ReplyAction
    return_date: date | None
    date_ambiguity: bool
    needs_review: bool
    notes: tuple[str, ...] = ()


def explicit_opt_out(body: str) -> bool:
    return bool(_OPT_OUT.search(normalize_text(body)))


def ambiguous_return(body: str) -> bool:
    text = normalize_text(body)
    return bool(_MONTH_WITHOUT_YEAR.search(text) or _VAGUE.search(text))


def parse_reply(payload: object, *, body: str, received_on: date) -> ReplyProposal:
    if not isinstance(payload, Mapping):
        raise SchemaError("ожидается JSON-объект")
    keys = {
        "schema_version",
        "class",
        "supporting_excerpt",
        "suggested_action",
        "return_date",
        "date_ambiguity",
        "needs_review",
    }
    if set(payload) != keys:
        raise SchemaError(f"поля должны быть ровно: {sorted(keys)}")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise SchemaError(f"schema_version должен быть {SCHEMA_VERSION}")
    try:
        reply_class = ReplyClass(payload["class"])
        action = ReplyAction(payload["suggested_action"])
    except ValueError as exc:
        raise SchemaError(f"недопустимое значение: {exc}") from None
    excerpt = payload["supporting_excerpt"]
    raw_date = payload["return_date"]
    if not isinstance(excerpt, str) or not isinstance(raw_date, str):
        raise SchemaError("supporting_excerpt и return_date — строки")
    if not isinstance(payload["date_ambiguity"], bool) or not isinstance(
        payload["needs_review"], bool
    ):
        raise SchemaError("date_ambiguity и needs_review — boolean")

    notes: list[str] = []
    if explicit_opt_out(body) and reply_class is not ReplyClass.OPT_OUT:
        notes.append("явный запрет контакта — класс opt_out установлен кодом")
        reply_class, action = ReplyClass.OPT_OUT, ReplyAction.SUPPRESS
    if excerpt and normalize_text(excerpt) not in normalize_text(body):
        notes.append("цитата не найдена в тексте ответа")
        excerpt = ""
    return_date: date | None = None
    ambiguity = payload["date_ambiguity"]
    if raw_date:
        try:
            return_date = date.fromisoformat(raw_date)
        except ValueError:
            notes.append("return_date не в формате YYYY-MM-DD")
    if return_date and ambiguous_return(body):
        notes.append("срок возврата неоднозначен — дату уточняет оператор")
        return_date, ambiguity = None, True
    if return_date and return_date <= received_on:
        notes.append("дата возврата не в будущем — отброшена")
        return_date = None
    return ReplyProposal(
        reply_class=reply_class,
        supporting_excerpt=excerpt,
        suggested_action=action,
        return_date=return_date,
        date_ambiguity=ambiguity,
        needs_review=payload["needs_review"] or bool(notes) or ambiguity,
        notes=tuple(notes),
    )
