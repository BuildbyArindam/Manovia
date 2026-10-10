"""Ephemeral sessions: in memory, owner-scoped, expiring after 30 idle minutes."""

from __future__ import annotations

import uuid

import pytest

from app.services.chat.ephemeral import MAX_SESSIONS_PER_USER, EphemeralSessionStore


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock: Clock) -> EphemeralSessionStore:
    return EphemeralSessionStore(ttl_seconds=1800, max_sessions=50, max_turns=6, clock=clock)


def test_the_default_ttl_is_thirty_minutes() -> None:
    assert EphemeralSessionStore().ttl_seconds == 30 * 60


def test_a_session_is_found_by_its_owner(store: EphemeralSessionStore) -> None:
    owner = uuid.uuid4()
    session = store.create(owner)
    assert store.get(session.id, owner) is session


def test_someone_elses_session_looks_exactly_like_a_missing_one(
    store: EphemeralSessionStore,
) -> None:
    session = store.create(uuid.uuid4())
    assert store.get(session.id, uuid.uuid4()) is None
    assert store.get(uuid.uuid4(), uuid.uuid4()) is None


def test_a_session_expires_after_thirty_idle_minutes(
    store: EphemeralSessionStore, clock: Clock
) -> None:
    owner = uuid.uuid4()
    session = store.create(owner)
    clock.now += 1799
    assert store.get(session.id, owner) is not None
    clock.now += 1801  # the read above did not extend the life
    assert store.get(session.id, owner) is None
    assert len(store) == 0, "an expired session is removed, not merely hidden"


def test_activity_slides_the_expiry(store: EphemeralSessionStore, clock: Clock) -> None:
    owner = uuid.uuid4()
    session = store.create(owner)
    clock.now += 1500
    store.add_turn(session, role="user", text="hi", risk=0, emotion=None)
    clock.now += 1500  # 3000s after creation, 1500s after the last turn
    assert store.get(session.id, owner) is not None
    clock.now += 301
    assert store.get(session.id, owner) is None


def test_touching_on_get_slides_the_expiry(store: EphemeralSessionStore, clock: Clock) -> None:
    owner = uuid.uuid4()
    session = store.create(owner)
    clock.now += 1700
    assert store.get(session.id, owner, touch=True) is not None
    clock.now += 1700
    assert store.get(session.id, owner) is not None


def test_create_sweeps_expired_sessions(store: EphemeralSessionStore, clock: Clock) -> None:
    for _ in range(5):
        store.create(uuid.uuid4())
    clock.now += 2000
    store.create(uuid.uuid4())
    assert len(store) == 1


def test_turns_are_capped_and_the_oldest_fall_off(store: EphemeralSessionStore) -> None:
    session = store.create(uuid.uuid4())
    for index in range(10):
        store.add_turn(session, role="user", text=f"t{index}", risk=0, emotion=None)
    assert [t.text for t in session.turns] == [f"t{i}" for i in range(4, 10)]


def test_one_user_cannot_hold_unlimited_sessions(store: EphemeralSessionStore) -> None:
    owner = uuid.uuid4()
    other = store.create(uuid.uuid4())
    first = store.create(owner)
    for _ in range(MAX_SESSIONS_PER_USER):
        store.create(owner)
    assert store.get(first.id, owner) is None, "their oldest went first"
    assert store.get(other.id, other.user_id) is not None, "nobody else was touched"


def test_the_total_is_capped_and_the_idlest_session_goes_first(clock: Clock) -> None:
    store = EphemeralSessionStore(ttl_seconds=1800, max_sessions=3, max_turns=6, clock=clock)
    users = [uuid.uuid4() for _ in range(3)]
    sessions = []
    for user in users:
        sessions.append(store.create(user))
        clock.now += 1
    store.add_turn(sessions[0], role="user", text="still here", risk=0, emotion=None)
    clock.now += 1
    store.create(uuid.uuid4())
    assert len(store) == 3
    assert store.get(sessions[1].id, users[1]) is None, "the idlest was evicted"
    assert store.get(sessions[0].id, users[0]) is not None


def test_discard_and_purge(store: EphemeralSessionStore, clock: Clock) -> None:
    owner = uuid.uuid4()
    session = store.create(owner)
    store.discard(session.id)
    assert store.get(session.id, owner) is None
    store.create(owner)
    clock.now += 5000
    assert store.purge() == 1


def test_a_crisis_turn_is_marked_by_its_stored_risk(store: EphemeralSessionStore) -> None:
    session = store.create(uuid.uuid4())
    calm = store.add_turn(session, role="user", text="a", risk=0, emotion=None)
    crisis = store.add_turn(session, role="user", text="b", risk=3, emotion=None)
    assert (calm.crisis, crisis.crisis) == (False, True)


@pytest.mark.parametrize(
    "kwargs",
    [{"ttl_seconds": 0}, {"max_sessions": 0}, {"max_turns": 1}],
)
def test_nonsense_limits_are_refused(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        EphemeralSessionStore(**kwargs)  # type: ignore[arg-type]
