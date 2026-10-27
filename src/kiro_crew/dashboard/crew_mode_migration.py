"""Start-up seed of the closed Crew Mode roots.

``kiro_crew.crew_mode`` keeps the set of known Crew Mode roots in memory, and a slot
frame adds only the roots that are live. A root whose tab is closed is found here,
from saved session metadata and persisted lineage, scheduled once at start, off
the boot path (``crew_mode.note_roots``), so its workers keep their ``crew_root``
and stay out of the person's lists after the seed unless the root has a persisted
parent. With the crew log enabled, the pass seeds its projection before reading
parents and defers root discovery while lineage is unavailable. With the crew log
off, saved metadata decides. The pass changes only in-memory root membership and
publishes slot stamps.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _slot_key(key: str) -> str:
    """A saved session's stem (``dashboard_chat-3-1792...``) as its slot key (``chat-3-1792...``).

    A resume round-trip can stack the prefix (``dashboard_dashboard_<slot>``), so
    every layer comes off.
    """
    out = key
    while True:
        for prefix in ("dashboard_", "dashboard:"):
            if out.startswith(prefix):
                out = out[len(prefix) :]
                break
        else:
            return out


def _closed_roots(log: Any) -> list[str]:
    """Saved roots without a persisted parent; defer unavailable lineage. BLOCKING.

    An enabled crew log must have settled lineage before metadata identifies roots.
    """
    from kiro_crew import crew_mode
    from kiro_crew.crew_log import emit as crew_log_emit

    if crew_log_emit.enabled():
        from kiro_crew.crew_log.session_tree_projection import projection

        projection().ensure_seeded()
    if crew_log_emit.enabled() and not crew_mode.lineage_is_settled():
        logger.debug("crew mode: closed-root seed skipped; lineage unavailable")
        return []

    try:
        parents = crew_mode.persisted_parents()
    except Exception:
        logger.debug("crew mode: closed-root lineage not loaded", exc_info=True)
        parents = {}
    conductors = crew_mode.conductor_agents()
    found: list[str] = []
    for row in log.list_sessions():
        key = str(row.get("key") or "")
        if not key or (row.get("agent") not in conductors and not row.get(crew_mode.STAMP_FIELD)):
            continue
        slot_key = _slot_key(key)
        if parents.get(slot_key) or parents.get(f"dashboard:{slot_key}"):
            continue
        try:
            meta = log.get_metadata(key)
        except Exception:
            continue
        if isinstance(meta, dict) and crew_mode.saved_row_is_root(meta):
            found.append(slot_key)
    return found


async def learn_closed_roots(state: Any) -> bool:
    """Load the closed Crew Mode roots from saved metadata. True when a live slot changes role.

    True when a root this call learns is the creator of a live slot: that slot's
    row becomes a worker, so the caller owes a slot frame. The start-up background
    seed publishes that frame after learning completes, outside the restore's
    coalesced push.
    """
    from kiro_crew import crew_mode

    log = getattr(state, "conversation_log", None)
    if log is None:
        return False
    try:
        found = await asyncio.to_thread(_closed_roots, log)
    except Exception:
        logger.debug("crew mode: closed roots not loaded", exc_info=True)
        return False
    new = set(found) - crew_mode.known_roots()
    crew_mode.note_roots(found)
    if not new:
        return False
    return any(
        str(getattr(slot, field, "") or "") in new
        for slot in list(getattr(state, "_slots", {}).values())
        for field in ("_created_by", "_saved_created_by")
    )


async def seed_closed_roots_in_background(state: Any) -> None:
    """Learn closed roots off the boot path and publish changed live-slot stamps."""
    try:
        if await learn_closed_roots(state):
            state.push_slots_update()
    except Exception:
        logger.debug("crew mode: closed-root background seed failed", exc_info=True)
