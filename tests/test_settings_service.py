import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User
from app.services import settings as svc


def test_user_settings_defaults_and_isolation(db: Session, alice: User, bob: User) -> None:
    assert svc.get_user_setting(db, alice.id, "playback_mode") == "embed"
    svc.set_user_setting(db, alice.id, "show_livestreams", True)
    svc.set_user_setting(db, alice.id, "show_livestreams", True)  # update path
    assert svc.get_user_setting(db, alice.id, "show_livestreams") is True
    assert svc.get_user_setting(db, bob.id, "show_livestreams") is False
    assert svc.get_user_settings(db, bob.id) == svc.USER_DEFAULTS


def test_user_setting_validation(db: Session, alice: User) -> None:
    with pytest.raises(KeyError):
        svc.set_user_setting(db, alice.id, "nope", 1)
    with pytest.raises(TypeError):
        svc.set_user_setting(db, alice.id, "show_livestreams", "yes")


def test_global_setting_falls_back_to_env(db: Session, settings: Settings, admin: User) -> None:
    assert svc.get_global_setting(db, settings, "downloads_enabled") is False
    svc.set_global_setting(db, "downloads_enabled", True, updated_by=admin.id)
    assert svc.get_global_setting(db, settings, "downloads_enabled") is True
    with pytest.raises(KeyError):
        svc.get_global_setting(db, settings, "secret_key")
