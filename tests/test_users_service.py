import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Role, User, UserSetting
from app.services import users
from app.services.users import UserError

PW = "password-1234"


def make(db: Session, name: str, role: Role = Role.USER) -> User:
    return users.create_user(db, name, PW, role)


def test_create_and_authenticate(db: Session) -> None:
    alice = make(db, "alice")
    assert users.authenticate(db, "alice", PW) == alice
    assert users.authenticate(db, "ALICE", PW) == alice
    assert users.authenticate(db, "alice", "wrong-password") is None
    assert users.authenticate(db, "nobody", PW) is None


@pytest.mark.parametrize("name", ["a", "with space", "-dash", "x" * 65, "ümlaut"])
def test_invalid_usernames(db: Session, name: str) -> None:
    with pytest.raises(UserError, match="username_invalid"):
        users.create_user(db, name, PW)


def test_duplicate_username_case_insensitive(db: Session) -> None:
    make(db, "alice")
    with pytest.raises(UserError, match="username_taken"):
        users.create_user(db, "Alice", PW)


def test_short_password_rejected(db: Session) -> None:
    with pytest.raises(UserError, match="password_too_short"):
        users.create_user(db, "alice", "short")


def test_inactive_user_cannot_authenticate(db: Session) -> None:
    admin = make(db, "admin", Role.ADMIN)
    alice = make(db, "alice")
    users.set_active(db, admin, alice, False)
    assert users.authenticate(db, "alice", PW) is None


def test_oidc_only_user_cannot_use_password(db: Session) -> None:
    users.create_user(db, "carol", None, oidc_subject="sub-1")
    assert users.authenticate(db, "carol", "") is None


def test_password_changes_bump_session_version(db: Session) -> None:
    alice = make(db, "alice")
    before = alice.session_version
    users.change_own_password(db, alice, PW, "new-password-5678")
    assert alice.session_version == before + 1
    assert users.authenticate(db, "alice", "new-password-5678") == alice
    with pytest.raises(UserError, match="current_password_wrong"):
        users.change_own_password(db, alice, "wrong-current", "another-password")


def test_last_admin_is_protected(db: Session) -> None:
    admin = make(db, "admin", Role.ADMIN)
    other = make(db, "other", Role.ADMIN)
    users.delete_user(db, admin, other)

    with pytest.raises(UserError, match="last_admin"):
        users.set_role(db, admin, admin, Role.USER)
    helper = make(db, "helper")
    with pytest.raises(UserError, match="last_admin"):
        users.delete_user(db, helper, admin)
    with pytest.raises(UserError, match="last_admin"):
        users.set_active(db, helper, admin, False)


def test_cannot_delete_or_deactivate_self(db: Session) -> None:
    admin = make(db, "admin", Role.ADMIN)
    make(db, "admin2", Role.ADMIN)
    with pytest.raises(UserError, match="cannot_delete_self"):
        users.delete_user(db, admin, admin)
    with pytest.raises(UserError, match="cannot_deactivate_self"):
        users.set_active(db, admin, admin, False)


def test_role_change_invalidates_sessions(db: Session) -> None:
    admin = make(db, "admin", Role.ADMIN)
    alice = make(db, "alice")
    version = alice.session_version
    users.set_role(db, admin, alice, Role.ADMIN)
    assert alice.is_admin
    assert alice.session_version == version + 1


def test_delete_user_removes_personal_data(db: Session) -> None:
    admin = make(db, "admin", Role.ADMIN)
    alice = make(db, "alice")
    bob = make(db, "bob")
    db.add_all(
        [
            UserSetting(user_id=alice.id, key="feed_sort", value="newest"),
            UserSetting(user_id=bob.id, key="feed_sort", value="for_me"),
        ]
    )
    db.commit()
    alice_id = alice.id

    users.delete_user(db, admin, alice)
    db.expire_all()

    assert db.get(User, alice_id) is None
    remaining = db.scalars(select(UserSetting)).all()
    assert [(s.user_id, s.value) for s in remaining] == [(bob.id, "for_me")]
