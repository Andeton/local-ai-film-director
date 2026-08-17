"""ContinuityResolver — deterministic scene-scoped shot ordering and predecessor lookup.

M7.A: pure query functions, no side effects.
Ordering: (beats.order_index ASC, beats.id ASC, shots.order_index ASC, shots.id ASC)
within a single scene. Never crosses scene boundaries.
"""
from __future__ import annotations

import sqlite3


class ContinuityResolver:
    """Deterministic predecessor resolution via canonical scene ordering."""

    @staticmethod
    def get_scene_shot_order(scene_id: str, conn: sqlite3.Connection) -> list[str]:
        """Return ordered list of current (non-outdated) shot IDs in a scene.

        Ordering: beats.order_index ASC, beats.id ASC,
                  shots.order_index ASC, shots.id ASC.
        Only includes shots on non-outdated beats.
        """
        sql = """
            SELECT shots.id
            FROM shots
            JOIN beats ON shots.beat_id = beats.id
            WHERE beats.scene_id = ?
              AND beats.status != 'outdated'
              AND shots.status != 'outdated'
            ORDER BY beats.order_index ASC, beats.id ASC,
                     shots.order_index ASC, shots.id ASC
        """
        rows = conn.execute(sql, (scene_id,)).fetchall()
        return [row["id"] for row in rows]

    @staticmethod
    def resolve_predecessor(shot_id: str, scene_id: str, conn: sqlite3.Connection) -> str | None:
        """Return the predecessor shot ID for the given shot, or None if chain head.

        Derives the ordered shot list for the scene and finds the
        immediately preceding shot. Returns None for the first shot.
        """
        ordered = ContinuityResolver.get_scene_shot_order(scene_id, conn)
        try:
            idx = ordered.index(shot_id)
        except ValueError:
            return None  # shot not in scene's current ordering
        if idx == 0:
            return None  # chain head
        return ordered[idx - 1]

    @staticmethod
    def get_downstream_shots(shot_id: str, scene_id: str, conn: sqlite3.Connection) -> list[str]:
        """Return all shot IDs after shot_id in the scene's canonical order.

        Returns an empty list if shot_id is the last shot or not found.
        """
        ordered = ContinuityResolver.get_scene_shot_order(scene_id, conn)
        try:
            idx = ordered.index(shot_id)
        except ValueError:
            return []
        return ordered[idx + 1:]

    @staticmethod
    def get_scene_id_for_shot(shot_id: str, conn: sqlite3.Connection) -> str | None:
        """Derive scene_id from shot via shot→beat→scene join."""
        row = conn.execute(
            """
            SELECT scenes.id AS scene_id
            FROM shots
            JOIN beats ON shots.beat_id = beats.id
            JOIN scenes ON beats.scene_id = scenes.id
            WHERE shots.id = ?
            """,
            (shot_id,),
        ).fetchone()
        return row["scene_id"] if row else None
