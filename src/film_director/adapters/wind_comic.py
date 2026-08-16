"""Wind Comic SQLite adapter — strictly read-only. mode=ro, no writable fallback."""
import json
import logging
import sqlite3
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

from film_director.errors import (
    WindComicArtifactMalformedError,
    WindComicNotFoundError,
    WindComicSchemaError,
    WindComicUnavailableError,
)
from film_director.models.wind_comic_dto import (
    WCCharacter,
    WCDirectorPlan,
    WCHealth,
    WCProject,
    WCScene,
    WCScriptShot,
    WCStoryboardShot,
)


@dataclass(frozen=True)
class WCProjectBundle:
    """Snapshot of a project with all artifacts, read atomically."""

    project: WCProject
    scenes: list[WCScene]
    characters: list[WCCharacter]
    script_shots: list[WCScriptShot] = dataclass_field(default_factory=list)
    storyboard_shots: list[WCStoryboardShot] = dataclass_field(default_factory=list)
    director_plan: WCDirectorPlan | None = None

logger = logging.getLogger(__name__)


def _build_uri(db_path: str) -> str:
    """Build a sqlite3 URI with mode=ro, handling Windows drive letters and spaces."""
    p = Path(db_path).resolve()
    # Convert to POSIX-style path; on Windows e.g. C:/Users/... -> /C:/Users/...
    posix = p.as_posix()
    # sqlite3 URI requires forward slashes and the path must start with /
    if not posix.startswith("/"):
        posix = "/" + posix
    # Percent-encode spaces (sqlite3 URI doesn't accept raw spaces)
    posix = posix.replace(" ", "%20")
    return f"file:{posix}?mode=ro"


class WindComicAdapter:
    """Read-only adapter for Wind Comic's qfmj.db SQLite database.

    This is the ONLY module that knows Wind Comic's database schema.
    All other modules must use the DTO types returned by this adapter.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        """Open a read-only connection. Raises WindComicUnavailableError if file is missing."""
        if not Path(self._db_path).exists():
            raise WindComicUnavailableError(f"WC database not found: {self._db_path}")
        uri = _build_uri(self._db_path)
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _parse_json_dict(self, raw: "str | None", ctx: str) -> dict:
        """Parse JSON that must be a dict. Raises WindComicArtifactMalformedError on failure."""
        if raw is None:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise WindComicArtifactMalformedError(
                f"Malformed JSON in {ctx}", detail=str(e)
            ) from e
        if not isinstance(parsed, dict):
            raise WindComicArtifactMalformedError(
                f"Expected JSON object in {ctx}, got {type(parsed).__name__}"
            )
        return parsed

    def _parse_json_list(self, raw: "str | None", ctx: str) -> list:
        """Parse JSON that must be a list. Returns [] for None; silently coerces non-list to []."""
        if raw is None:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []

    def _query(self, sql: str, params: tuple = ()) -> list:
        """Execute a query and return all rows. Wraps OperationalError as WindComicSchemaError."""
        conn = self._connect()
        try:
            return conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:
            msg = str(e)
            if "no such table" in msg or "no such column" in msg:
                raise WindComicSchemaError(f"WC schema incompatible: {e}", detail=msg) from e
            raise
        finally:
            conn.close()

    def _query_one(self, sql: str, params: tuple = ()):
        """Execute a query and return one row or None. Wraps OperationalError as WindComicSchemaError."""
        conn = self._connect()
        try:
            return conn.execute(sql, params).fetchone()
        except sqlite3.OperationalError as e:
            msg = str(e)
            if "no such table" in msg or "no such column" in msg:
                raise WindComicSchemaError(f"WC schema incompatible: {e}", detail=msg) from e
            raise
        finally:
            conn.close()

    def health(self) -> WCHealth:
        """Return health status without raising exceptions."""
        try:
            conn = self._connect()
            conn.execute("SELECT 1")
            conn.close()
            return WCHealth(available=True, db_path=self._db_path)
        except Exception as e:  # noqa: BLE001
            return WCHealth(available=False, db_path=self._db_path, error=str(e))

    def get_project(self, project_id: str) -> WCProject:
        """Fetch a single project by ID.

        Raises:
            WindComicNotFoundError: project does not exist
            WindComicSchemaError: DB schema is incompatible
            WindComicArtifactMalformedError: JSON fields are corrupt
        """
        row = self._query_one(
            "SELECT id, title, status, aspect, style_id, script_data, locked_characters"
            " FROM projects WHERE id = ?",
            (project_id,),
        )
        if row is None:
            raise WindComicNotFoundError(f"Project not found: {project_id}")
        return WCProject(
            id=row["id"],
            title=row["title"] or "",
            status=row["status"] or "draft",
            aspect=row["aspect"] or "16:9",
            style_id=row["style_id"],
            script_data=self._parse_json_dict(row["script_data"], f"project {project_id} script_data") or None,
            locked_characters=self._parse_json_list(row["locked_characters"], f"project {project_id} locked_chars"),
        )

    def get_scenes(self, project_id: str) -> list[WCScene]:
        """Fetch all scene assets for a project, ordered by name.

        Returns an empty list if the project has no scenes (or doesn't exist).
        """
        rows = self._query(
            "SELECT id, project_id, name, data, media_urls, persistent_url, version"
            " FROM project_assets"
            " WHERE project_id = ? AND type = 'scene'"
            " ORDER BY name",
            (project_id,),
        )
        result = []
        for r in rows:
            d = self._parse_json_dict(r["data"], f"scene {r['id']}")
            result.append(
                WCScene(
                    asset_id=r["id"],
                    project_id=r["project_id"],
                    name=r["name"] or "",
                    data=d,
                    media_urls=self._parse_json_list(r["media_urls"], f"scene {r['id']} media_urls"),
                    persistent_url=r["persistent_url"],
                    version=r["version"] or 1,
                )
            )
        return result

    def get_characters(self, project_id: str) -> list[WCCharacter]:
        """Fetch all character assets for a project, ordered by name.

        Returns an empty list if the project has no characters (or doesn't exist).
        """
        rows = self._query(
            "SELECT id, project_id, name, data, media_urls, persistent_url, version"
            " FROM project_assets"
            " WHERE project_id = ? AND type = 'character'"
            " ORDER BY name",
            (project_id,),
        )
        result = []
        for r in rows:
            d = self._parse_json_dict(r["data"], f"char {r['id']}")
            result.append(
                WCCharacter(
                    asset_id=r["id"],
                    project_id=r["project_id"],
                    name=r["name"] or "",
                    data=d,
                    media_urls=self._parse_json_list(r["media_urls"], f"char {r['id']} media_urls"),
                    persistent_url=r["persistent_url"],
                    version=r["version"] or 1,
                )
            )
        return result

    def read_project_bundle(self, project_id: str) -> WCProjectBundle:
        """Read project, scenes, and characters from ONE read-only connection.

        This ensures source consistency — all data is from the same point-in-time
        snapshot of the Wind Comic database.

        Raises:
            WindComicNotFoundError: project does not exist
            WindComicSchemaError: DB schema is incompatible
            WindComicArtifactMalformedError: JSON fields are corrupt
        """
        conn = self._connect()
        try:
            # --- project ---
            row = conn.execute(
                "SELECT id, title, status, aspect, style_id, script_data, locked_characters"
                " FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            if row is None:
                raise WindComicNotFoundError(f"Project not found: {project_id}")
            project = WCProject(
                id=row["id"],
                title=row["title"] or "",
                status=row["status"] or "draft",
                aspect=row["aspect"] or "16:9",
                style_id=row["style_id"],
                script_data=self._parse_json_dict(row["script_data"], f"project {project_id} script_data") or None,
                locked_characters=self._parse_json_list(row["locked_characters"], f"project {project_id} locked_chars"),
            )

            # --- scenes ---
            scene_rows = conn.execute(
                "SELECT id, project_id, name, data, media_urls, persistent_url, version"
                " FROM project_assets"
                " WHERE project_id = ? AND type = 'scene'"
                " ORDER BY name",
                (project_id,),
            ).fetchall()
            scenes = []
            for r in scene_rows:
                d = self._parse_json_dict(r["data"], f"scene {r['id']}")
                scenes.append(
                    WCScene(
                        asset_id=r["id"],
                        project_id=r["project_id"],
                        name=r["name"] or "",
                        data=d,
                        media_urls=self._parse_json_list(r["media_urls"], f"scene {r['id']} media_urls"),
                        persistent_url=r["persistent_url"],
                        version=r["version"] or 1,
                    )
                )

            # --- characters ---
            char_rows = conn.execute(
                "SELECT id, project_id, name, data, media_urls, persistent_url, version"
                " FROM project_assets"
                " WHERE project_id = ? AND type = 'character'"
                " ORDER BY name",
                (project_id,),
            ).fetchall()
            characters = []
            for r in char_rows:
                d = self._parse_json_dict(r["data"], f"char {r['id']}")
                characters.append(
                    WCCharacter(
                        asset_id=r["id"],
                        project_id=r["project_id"],
                        name=r["name"] or "",
                        data=d,
                        media_urls=self._parse_json_list(r["media_urls"], f"char {r['id']} media_urls"),
                        persistent_url=r["persistent_url"],
                        version=r["version"] or 1,
                    )
                )

            # --- script shots from project.script_data ---
            script_shots = self._parse_script_shots(project.script_data)

            # --- storyboard shots ---
            sb_rows = conn.execute(
                "SELECT id, project_id, shot_number, data, media_urls, persistent_url, version"
                " FROM project_assets"
                " WHERE project_id = ? AND type = 'storyboard'"
                " ORDER BY shot_number",
                (project_id,),
            ).fetchall()
            storyboard_shots = []
            for r in sb_rows:
                d = self._parse_json_dict(r["data"], f"storyboard {r['id']}")
                storyboard_shots.append(
                    WCStoryboardShot(
                        asset_id=r["id"],
                        project_id=r["project_id"],
                        shot_number=r["shot_number"] or 0,
                        data=d,
                        media_urls=self._parse_json_list(r["media_urls"], f"storyboard {r['id']} media_urls"),
                        persistent_url=r["persistent_url"],
                        version=r["version"] or 1,
                    )
                )

            # --- Director plan (first plan asset) ---
            director_plan = None
            plan_row = conn.execute(
                "SELECT data FROM project_assets"
                " WHERE project_id = ? AND type = 'plan'"
                " ORDER BY version DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            if plan_row:
                plan_data = self._parse_json_dict(plan_row["data"], f"plan for {project_id}")
                director_plan = WCDirectorPlan(
                    genre=plan_data.get("genre", ""),
                    style=plan_data.get("style", ""),
                    story_structure=plan_data.get("storyStructure", {}),
                )

            return WCProjectBundle(
                project=project, scenes=scenes, characters=characters,
                script_shots=script_shots, storyboard_shots=storyboard_shots,
                director_plan=director_plan,
            )
        except sqlite3.OperationalError as e:
            msg = str(e)
            if "no such table" in msg or "no such column" in msg:
                raise WindComicSchemaError(f"WC schema incompatible: {e}", detail=msg) from e
            raise
        finally:
            conn.close()

    @staticmethod
    def _parse_script_shots(script_data: dict | None) -> list[WCScriptShot]:
        """Extract script shots from project script_data JSON."""
        if not script_data:
            return []
        shots = script_data.get("shots", [])
        if not isinstance(shots, list):
            return []
        result = []
        for s in shots:
            if not isinstance(s, dict):
                continue
            result.append(WCScriptShot(
                shot_number=s.get("shotNumber", 0),
                scene_description=s.get("sceneDescription", ""),
                characters=s.get("characters", []) if isinstance(s.get("characters"), list) else [],
                dialogue=s.get("dialogue", ""),
                action=s.get("action", ""),
                emotion=s.get("emotion", ""),
            ))
        return result

    def get_storyboard(self, project_id: str) -> list[WCStoryboardShot]:
        """Fetch all storyboard shot assets for a project, ordered by shot_number.

        Returns an empty list if the project has no storyboard shots (or doesn't exist).
        """
        rows = self._query(
            "SELECT id, project_id, shot_number, data, media_urls, persistent_url, version"
            " FROM project_assets"
            " WHERE project_id = ? AND type = 'storyboard'"
            " ORDER BY shot_number",
            (project_id,),
        )
        result = []
        for r in rows:
            d = self._parse_json_dict(r["data"], f"storyboard {r['id']}")
            result.append(
                WCStoryboardShot(
                    asset_id=r["id"],
                    project_id=r["project_id"],
                    shot_number=r["shot_number"] or 0,
                    data=d,
                    media_urls=self._parse_json_list(r["media_urls"], f"storyboard {r['id']} media_urls"),
                    persistent_url=r["persistent_url"],
                    version=r["version"] or 1,
                )
            )
        return result
