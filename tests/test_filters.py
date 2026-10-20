import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import FilterRule, User, Video
from app.services import filters
from app.services import settings as user_settings
from app.services.filters import RuleError
from app.web.labels import duration_label, rule_label
from tests.conftest import login, new_client, post
from tests.factories import add_channel, subscribe


def video(**fields: object) -> Video:
    base: dict[str, object] = {
        "title": "Die Zukunft der Raumfahrt",
        "description": "Mit SPONSOR: NordVPN",
        "channel_id": 1,
        "duration_seconds": 900,
    }
    base.update(fields)
    return Video(**base)


@pytest.mark.parametrize(
    ("rule_type", "value", "fields", "expected"),
    [
        ("keyword_title", "raumfahrt", {}, True),
        ("keyword_title", "Kochen", {}, False),
        ("keyword_desc", "nordvpn", {}, True),
        ("keyword_desc", "raumfahrt", {}, False),
        ("channel", "1", {}, True),
        ("channel", "2", {}, False),
        ("min_duration", "1200", {}, True),  # 15 min < 20 min
        ("min_duration", "600", {}, False),
        ("min_duration", "600", {"duration_seconds": None}, False),
        ("max_duration", "600", {}, True),
        ("max_duration", "3600", {}, False),
        ("regex", r"^die\s+zukunft", {}, True),
        ("regex", r"sponsor:\s*\w+", {}, True),
        ("regex", r"^Kochen", {}, False),
    ],
)
def test_rule_matching(
    rule_type: str, value: str, fields: dict[str, object], expected: bool
) -> None:
    [compiled] = filters.compile_rules(
        [FilterRule(id=1, type=rule_type, value=value, is_active=True)]
    )
    assert compiled.matches(video(**fields)) is expected


def test_inactive_rules_are_ignored() -> None:
    rules = [FilterRule(id=1, type="keyword_title", value="zukunft", is_active=False)]
    assert filters.compile_rules(rules) == []


def test_create_rule_validation(db: Session, alice: User, bob: User) -> None:
    channel = add_channel(db, 1)
    subscribe(db, alice.id, channel)
    assert filters.create_rule(db, alice, "min_duration", " 20 ").value == "1200"
    assert filters.create_rule(db, alice, "channel", str(channel.id)).value == str(channel.id)
    assert filters.create_rule(db, alice, "keyword_title", "  Reaktion ").value == "Reaktion"
    for rule_type, value, key in [
        ("keyword_title", "  ", "error.rule_value_empty"),
        ("keyword_title", "x" * 201, "error.rule_value_too_long"),
        ("regex", "([unclosed", "error.rule_regex_invalid"),
        ("max_duration", "zehn", "error.rule_duration_invalid"),
        ("max_duration", "0", "error.rule_duration_invalid"),
        ("channel", "999", "error.rule_channel_unknown"),
        ("channel", "abc", "error.rule_channel_unknown"),
        ("nonsense", "x", "error.rule_type_invalid"),
    ]:
        with pytest.raises(RuleError) as exc:
            filters.create_rule(db, alice, rule_type, value)
        assert exc.value.key == key
    # Bob cannot exclude a channel he does not follow, and cannot see Alice's rules.
    with pytest.raises(RuleError):
        filters.create_rule(db, bob, "channel", str(channel.id))
    rule = filters.list_rules(db, alice)[0]
    assert filters.get_rule(db, bob, rule.id) is None
    assert filters.list_rules(db, bob) == []


def test_labels() -> None:
    assert rule_label("keyword_title", "Reaktion", {}) == "Stichwort „Reaktion“ im Titel"
    assert rule_label("min_duration", "1200", {}) == "kürzer als 20 min"
    assert rule_label("channel", "3", {"3": "Testkanal"}) == "Kanal „Testkanal“"
    assert duration_label(59) == "0:59"
    assert duration_label(3723) == "1:02:03"
    assert duration_label(None) == ""


def test_settings_page_and_toggles(alice_client: TestClient, alice: User, db: Session) -> None:
    page = alice_client.get("/settings")
    assert page.status_code == 200 and "Filterregeln" in page.text
    post(alice_client, "/settings", {"show_livestreams": "on"})
    db.expire_all()
    assert user_settings.get_user_setting(db, alice.id, "show_livestreams") is True
    assert user_settings.get_user_setting(db, alice.id, "show_premieres") is False
    post(alice_client, "/settings", {})
    db.expire_all()
    assert user_settings.get_user_setting(db, alice.id, "show_livestreams") is False


def test_rule_routes(alice_client: TestClient, alice: User, db: Session) -> None:
    channel = add_channel(db, 1, title="Lauter Kanal")
    subscribe(db, alice.id, channel)
    page = post(
        alice_client, "/settings/rules", {"type": "keyword_title", "value": "Reaktion"}
    ).text
    assert "Regel hinzugefügt" in page and "Stichwort „Reaktion“ im Titel" in page
    page = post(
        alice_client, "/settings/rules", {"type": "channel", "channel": str(channel.id)}
    ).text
    assert "Kanal „Lauter Kanal“" in page
    page = post(alice_client, "/settings/rules", {"type": "regex", "value": "(("}).text
    assert "ungültig" in page

    rule = filters.list_rules(db, alice)[0]
    post(alice_client, f"/settings/rules/{rule.id}/toggle")
    db.expire_all()
    assert not filters.get_rule(db, alice, rule.id).is_active  # type: ignore[union-attr]
    post(alice_client, f"/settings/rules/{rule.id}/delete")
    assert filters.get_rule(db, alice, rule.id) is None


def test_rule_isolation(app: FastAPI, alice: User, bob: User, db: Session) -> None:
    rule = filters.create_rule(db, alice, "keyword_title", "Geheimnis")
    user_settings.set_user_setting(db, alice.id, "show_livestreams", True)
    bob_client = new_client(app)
    login(bob_client, "bob")
    assert "Geheimnis" not in bob_client.get("/settings").text
    assert post(bob_client, f"/settings/rules/{rule.id}/toggle").status_code == 404
    assert post(bob_client, f"/settings/rules/{rule.id}/delete").status_code == 404
    post(bob_client, "/settings", {})
    db.expire_all()
    assert filters.get_rule(db, alice, rule.id) is not None
    assert user_settings.get_user_setting(db, alice.id, "show_livestreams") is True
