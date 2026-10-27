"""The start-up seed of closed Crew Mode roots (``crew_mode_migration.learn_closed_roots``)."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from kiro_crew import crew_mode
from kiro_crew.crew_log import emit as crew_log_emit
from kiro_crew.crew_log import session_tree_projection
from kiro_crew.dashboard import crew_mode_migration


@pytest.fixture(autouse=True)
def _fresh_known_roots(_floor_monkeypatch: pytest.MonkeyPatch) -> None:
    _floor_monkeypatch.setattr(crew_mode, "_known_roots", set())
    _floor_monkeypatch.setattr(crew_mode, "persisted_parents", lambda: {})
    _floor_monkeypatch.setattr(crew_log_emit, "enabled", lambda: False)
    _floor_monkeypatch.setattr(
        session_tree_projection, "projection", Mock(side_effect=AssertionError("unexpected seed"))
    )
    _floor_monkeypatch.setattr(
        crew_mode, "lineage_is_settled", Mock(side_effect=AssertionError("unexpected lineage read"))
    )


class _Log:
    """A conversation log holding saved session rows and their metadata, in memory."""

    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self._rows = rows

    def list_sessions(self) -> list[dict[str, Any]]:
        return [{"key": key, "agent": meta.get("agent")} for key, meta in self._rows.items()]

    def get_metadata(self, key: str) -> dict[str, Any]:
        return self._rows[key]


@pytest.mark.asyncio
async def test_a_saved_root_is_noted_and_its_live_worker_is_reported() -> None:
    conductor = sorted(crew_mode.CONDUCTOR_TEMPLATES)[0]
    log = _Log(
        {
            "dashboard_chat-1-100": {"agent": conductor, "memory_mode": "persistent"},
            "dashboard_chat-2-200": {"agent": "kiro_default", "memory_mode": "persistent"},
        }
    )
    worker = SimpleNamespace(_created_by="chat-1-100", _saved_created_by="")
    state = SimpleNamespace(conversation_log=log, _slots={"chat-3-300": worker})

    moved = await crew_mode_migration.learn_closed_roots(state)

    assert moved is True
    assert crew_mode.known_roots() == frozenset({"chat-1-100"})


@pytest.mark.asyncio
async def test_enabled_crew_log_seeds_before_reading_adopted_conductor_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conductor = sorted(crew_mode.CONDUCTOR_TEMPLATES)[0]
    log = _Log(
        {
            "dashboard_chat-1-100": {"agent": conductor, "memory_mode": "persistent"},
            "dashboard_chat-2-200": {"agent": conductor, "memory_mode": "persistent"},
        }
    )
    events: list[str] = []
    seeded_parents: dict[str, str] = {}

    def seed() -> None:
        events.append("seed")
        seeded_parents["chat-1-100"] = "chat-ordinary"

    def settled() -> bool:
        events.append("settled")
        return bool(seeded_parents)

    def parents() -> dict[str, str]:
        events.append("parents")
        return dict(seeded_parents)

    ensure_seeded = Mock(side_effect=seed)
    monkeypatch.setattr(crew_log_emit, "enabled", lambda: True)
    monkeypatch.setattr(
        session_tree_projection, "projection", lambda: SimpleNamespace(ensure_seeded=ensure_seeded)
    )
    monkeypatch.setattr(crew_mode, "lineage_is_settled", settled)
    monkeypatch.setattr(crew_mode, "persisted_parents", parents)
    worker = SimpleNamespace(_created_by="chat-1-100", _saved_created_by="")
    state = SimpleNamespace(conversation_log=log, _slots={"chat-3-300": worker})

    moved = await crew_mode_migration.learn_closed_roots(state)

    assert moved is False
    assert crew_mode.known_roots() == frozenset({"chat-2-200"})
    ensure_seeded.assert_called_once_with()
    assert events == ["seed", "settled", "parents"]


@pytest.mark.asyncio
async def test_enabled_crew_log_defers_roots_when_lineage_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conductor = sorted(crew_mode.CONDUCTOR_TEMPLATES)[0]
    log = _Log({"dashboard_chat-1-100": {"agent": conductor, "memory_mode": "persistent"}})
    ensure_seeded = Mock()
    parents = Mock(return_value={})
    debug = Mock()
    monkeypatch.setattr(crew_log_emit, "enabled", lambda: True)
    monkeypatch.setattr(
        session_tree_projection, "projection", lambda: SimpleNamespace(ensure_seeded=ensure_seeded)
    )
    monkeypatch.setattr(crew_mode, "lineage_is_settled", lambda: False)
    monkeypatch.setattr(crew_mode, "persisted_parents", parents)
    monkeypatch.setattr(crew_mode_migration.logger, "debug", debug)
    worker = SimpleNamespace(_created_by="chat-1-100", _saved_created_by="")
    state = SimpleNamespace(conversation_log=log, _slots={"chat-3-300": worker})

    moved = await crew_mode_migration.learn_closed_roots(state)

    assert moved is False
    assert crew_mode.known_roots() == frozenset()
    ensure_seeded.assert_called_once_with()
    parents.assert_not_called()
    debug.assert_called_once_with("crew mode: closed-root seed skipped; lineage unavailable")


@pytest.mark.asyncio
@pytest.mark.parametrize("tree_key", ["chat-1-100", "dashboard:chat-1-100"])
async def test_an_adopted_saved_conductor_is_not_noted(
    monkeypatch: pytest.MonkeyPatch, tree_key: str
) -> None:
    conductor = sorted(crew_mode.CONDUCTOR_TEMPLATES)[0]
    log = _Log(
        {
            "dashboard_chat-1-100": {"agent": conductor, "memory_mode": "persistent"},
            "dashboard_chat-2-200": {"agent": conductor, "memory_mode": "persistent"},
        }
    )
    reads: list[bool] = []

    def parents() -> dict[str, str]:
        reads.append(True)
        return {tree_key: "chat-ordinary"}

    monkeypatch.setattr(crew_mode, "persisted_parents", parents)
    worker = SimpleNamespace(_created_by="chat-1-100", _saved_created_by="")
    state = SimpleNamespace(conversation_log=log, _slots={"chat-3-300": worker})

    moved = await crew_mode_migration.learn_closed_roots(state)

    assert moved is False
    assert crew_mode.known_roots() == frozenset({"chat-2-200"})
    assert reads == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("known", [False, True])
@pytest.mark.parametrize("creator_field", ["_created_by", "_saved_created_by"])
async def test_background_seed_pushes_only_for_a_new_live_worker(
    known: bool, creator_field: str
) -> None:
    conductor = sorted(crew_mode.CONDUCTOR_TEMPLATES)[0]
    log = _Log({"dashboard_chat-1-100": {"agent": conductor, "memory_mode": "persistent"}})
    worker = SimpleNamespace(**{creator_field: "chat-1-100"})
    state = SimpleNamespace(
        conversation_log=log, _slots={"chat-3-300": worker}, push_slots_update=Mock()
    )
    if known:
        crew_mode.note_roots(["chat-1-100"])

    await crew_mode_migration.seed_closed_roots_in_background(state)

    assert crew_mode.known_roots() == frozenset({"chat-1-100"})
    if known:
        state.push_slots_update.assert_not_called()
    else:
        state.push_slots_update.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["learn", "push"])
async def test_background_seed_logs_errors(monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    error = RuntimeError("seed unavailable")
    learn = AsyncMock(side_effect=error if failure == "learn" else None, return_value=True)
    push = Mock(side_effect=error if failure == "push" else None)
    debug = Mock()
    monkeypatch.setattr(crew_mode_migration, "learn_closed_roots", learn)
    monkeypatch.setattr(crew_mode_migration.logger, "debug", debug)

    await crew_mode_migration.seed_closed_roots_in_background(
        SimpleNamespace(push_slots_update=push)
    )

    debug.assert_called_once_with("crew mode: closed-root background seed failed", exc_info=True)
    assert push.call_count == (1 if failure == "push" else 0)


@pytest.mark.asyncio
async def test_restore_tracks_closed_root_seed_outside_its_first_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kiro_crew.dashboard import server as srv
    from kiro_crew.testing.wait import await_until

    started = asyncio.Event()
    release = asyncio.Event()
    events: list[str] = []

    @contextmanager
    def suspend():
        events.append("suspend")
        yield
        events.append("first frame")

    async def learn(_state: Any) -> bool:
        events.append("seed")
        started.set()
        await await_until(release)
        return True

    monkeypatch.setattr(srv.cautious_boot, "pause_before", AsyncMock())
    monkeypatch.setattr(srv.chat, "restore_open_slots_async", AsyncMock(return_value=0))
    monkeypatch.setattr(srv.chat, "restore_recent_sessions_async", AsyncMock(return_value=0))
    monkeypatch.setattr(crew_mode_migration, "learn_closed_roots", learn)
    state: Any = SimpleNamespace(
        arm_slot_list_rebuild_deadline=Mock(),
        suspend_slots_push=suspend,
        reseed_slot_counter=Mock(),
        _background_tasks=set(),
        _dynamic_cards=None,
        replace_channel_slot_holds=Mock(),
        finish_slot_list_rebuild=Mock(),
        push_slots_update=Mock(),
    )
    cfg: Any = SimpleNamespace(
        dashboard=SimpleNamespace(
            restore_sessions=True,
            restore_window_minutes=30,
            idle_slot_sweep_days=0,
            surface_channel_sessions=False,
        )
    )

    try:
        await await_until(asyncio.ensure_future(srv._restore_dashboard_sessions(state, cfg)))
        await await_until(started)
        assert events == ["suspend", "first frame", "seed"]
        assert len(state._background_tasks) == 1
        task = next(iter(state._background_tasks))
        assert not task.done()
        state.push_slots_update.assert_not_called()
        release.set()
        await await_until(task)
        assert not state._background_tasks
        state.push_slots_update.assert_called_once_with()
    finally:
        release.set()
        for pending in list(state._background_tasks):
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
