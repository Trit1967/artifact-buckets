"""Password guessing is slowed, a colleague can't be locked out from another address, and a wrong name
costs the same as a wrong password."""
from app.security import LoginThrottle, login_throttle


def test_throttle_blocks_after_repeated_failures_then_allows_after_window():
    t = LoginThrottle()
    for i in range(t.PER_NAME):
        assert t.wait("1.1.1.1", "maya", now=i) == 0
        t.failed("1.1.1.1", "maya", now=i)
    assert t.wait("1.1.1.1", "maya", now=10) > 0
    # Another address is not held back, so the real person can still sign in.
    assert t.wait("2.2.2.2", "maya", now=10) == 0
    assert t.wait("1.1.1.1", "maya", now=t.WINDOW + 100) == 0


def test_success_clears_the_count():
    t = LoginThrottle()
    for i in range(t.PER_NAME - 1):
        t.failed("1.1.1.1", "maya", now=i)
    t.ok("1.1.1.1", "maya")
    assert t.wait("1.1.1.1", "maya", now=20) == 0


def test_login_endpoint_returns_429_then_recovers_for_others(app, make_user):
    from fastapi.testclient import TestClient
    login_throttle._fails.clear()
    make_user("alice")
    c = TestClient(app)
    for _ in range(login_throttle.PER_NAME):
        assert c.post("/api/auth/login", json={"username": "alice", "password": "nope"}).status_code == 401
    r = c.post("/api/auth/login", json={"username": "alice", "password": "nope"})
    assert r.status_code == 429 and r.json()["error"] == "too_many_attempts"
    login_throttle._fails.clear()


def test_cli_reset_password_signs_the_person_in_again(app, make_user, env):
    from fastapi.testclient import TestClient
    from app import cli
    from app.security import login_throttle
    make_user("alice")
    pw = cli.reset_password("alice")
    login_throttle._fails.clear()
    r = TestClient(app).post("/api/auth/login", json={"username": "alice", "password": pw})
    assert r.status_code == 200
    assert TestClient(app).post("/api/auth/login", json={"username": "alice", "password": "a-long-enough-password"}).status_code == 401
    login_throttle._fails.clear()
