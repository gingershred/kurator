"""Per-user filter rules. Rules are applied when the feed is queried, so changes take effect
immediately and disabling a rule undoes it."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Channel, FilterRule, FilterRuleType, Subscription, User, Video

MAX_VALUE_LENGTH = 200
MAX_DURATION_MINUTES = 24 * 60


class RuleError(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


@dataclass(frozen=True)
class CompiledRule:
    rule_id: int
    type: FilterRuleType
    value: str
    pattern: re.Pattern[str] | None = None

    def matches(self, video: Video) -> bool:
        match self.type:
            case FilterRuleType.KEYWORD_TITLE:
                return self.value.casefold() in video.title.casefold()
            case FilterRuleType.KEYWORD_DESC:
                return self.value.casefold() in video.description.casefold()
            case FilterRuleType.CHANNEL:
                return str(video.channel_id) == self.value
            case FilterRuleType.MIN_DURATION:
                return video.duration_seconds is not None and video.duration_seconds < int(
                    self.value
                )
            case FilterRuleType.MAX_DURATION:
                return video.duration_seconds is not None and video.duration_seconds > int(
                    self.value
                )
            case FilterRuleType.REGEX:
                assert self.pattern is not None  # noqa: S101  (set by compile_rules)
                return self.pattern.search(f"{video.title}\n{video.description}") is not None
        return False  # pragma: no cover


def _normalize(db: Session, user: User, rule_type: FilterRuleType, raw: str) -> str:
    value = raw.strip()
    if not value:
        raise RuleError("error.rule_value_empty")
    if len(value) > MAX_VALUE_LENGTH:
        raise RuleError("error.rule_value_too_long")
    if rule_type == FilterRuleType.REGEX:
        try:
            re.compile(value, re.IGNORECASE)
        except re.error as exc:
            raise RuleError("error.rule_regex_invalid") from exc
    elif rule_type in (FilterRuleType.MIN_DURATION, FilterRuleType.MAX_DURATION):
        # Entered in minutes, stored in seconds.
        try:
            minutes = int(value)
        except ValueError as exc:
            raise RuleError("error.rule_duration_invalid") from exc
        if not 0 < minutes <= MAX_DURATION_MINUTES:
            raise RuleError("error.rule_duration_invalid")
        value = str(minutes * 60)
    elif rule_type == FilterRuleType.CHANNEL:
        subscribed = db.scalars(
            select(Subscription.channel_id).where(
                Subscription.user_id == user.id, Subscription.channel_id == _int(value)
            )
        ).first()
        if subscribed is None:
            raise RuleError("error.rule_channel_unknown")
    return value


def _int(value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise RuleError("error.rule_channel_unknown") from exc


def create_rule(db: Session, user: User, rule_type: str, raw_value: str) -> FilterRule:
    try:
        kind = FilterRuleType(rule_type)
    except ValueError as exc:
        raise RuleError("error.rule_type_invalid") from exc
    rule = FilterRule(user_id=user.id, type=kind.value, value=_normalize(db, user, kind, raw_value))
    db.add(rule)
    db.commit()
    return rule


def list_rules(db: Session, user: User) -> list[FilterRule]:
    return list(
        db.scalars(select(FilterRule).where(FilterRule.user_id == user.id).order_by(FilterRule.id))
    )


def get_rule(db: Session, user: User, rule_id: int) -> FilterRule | None:
    return db.scalars(
        select(FilterRule).where(FilterRule.id == rule_id, FilterRule.user_id == user.id)
    ).first()


def set_active(db: Session, rule: FilterRule, active: bool) -> None:
    rule.is_active = active
    db.commit()


def delete_rule(db: Session, rule: FilterRule) -> None:
    db.delete(rule)
    db.commit()


def compile_rules(rules: list[FilterRule]) -> list[CompiledRule]:
    compiled = []
    for rule in rules:
        if not rule.is_active:
            continue
        kind = FilterRuleType(rule.type)
        pattern = re.compile(rule.value, re.IGNORECASE) if kind == FilterRuleType.REGEX else None
        compiled.append(CompiledRule(rule.id, kind, rule.value, pattern))
    return compiled


def active_rules(db: Session, user: User) -> list[CompiledRule]:
    return compile_rules([r for r in list_rules(db, user) if r.is_active])


def first_match(rules: list[CompiledRule], video: Video) -> CompiledRule | None:
    return next((rule for rule in rules if rule.matches(video)), None)


def channel_titles(db: Session, user: User) -> dict[str, str]:
    """Channel id (as rule value) -> title, for the user's subscriptions."""
    rows = db.execute(
        select(Channel.id, Channel.title, Channel.youtube_channel_id)
        .join(Subscription, Subscription.channel_id == Channel.id)
        .where(Subscription.user_id == user.id)
        .order_by(Channel.title)
    ).all()
    return {str(cid): title or yt_id for cid, title, yt_id in rows}
