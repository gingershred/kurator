"""Human-readable (German) labels for domain values, built from the central texts."""

from __future__ import annotations

from app.models import FilterRuleType
from app.web.i18n import translate


def rule_label(rule_type: str, value: str, channel_titles: dict[str, str]) -> str:
    kind = FilterRuleType(rule_type)
    if kind in (FilterRuleType.MIN_DURATION, FilterRuleType.MAX_DURATION):
        return translate(f"rule.label.{kind.value}", minutes=int(value) // 60)
    if kind == FilterRuleType.CHANNEL:
        return translate("rule.label.channel", value=channel_titles.get(value, "?"))
    return translate(f"rule.label.{kind.value}", value=value)


def duration_label(seconds: int | None) -> str:
    if seconds is None:
        return ""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
