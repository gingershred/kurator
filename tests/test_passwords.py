from app.auth.passwords import hash_password, password_problem, verify_password


def test_hash_and_verify() -> None:
    hashed = hash_password("correct horse battery")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "correct horse battery")
    assert not verify_password(hashed, "wrong password!!")


def test_verify_garbage_hash_is_false() -> None:
    assert not verify_password("not-a-hash", "whatever")


def test_password_rules() -> None:
    assert password_problem("short") == "error.password_too_short"
    assert password_problem("x" * 2000) == "error.password_too_long"
    assert password_problem("long enough pw") is None
