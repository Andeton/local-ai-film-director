"""Wind Comic SQLite fixture matching v12.320 schema."""
import json
import sqlite3
from pathlib import Path

TEST_PROJECT_ID = "test_proj_001"

TEST_PROJECT = {
    "id": TEST_PROJECT_ID,
    "user_id": "u1",
    "title": "The Abandoned Hospital",
    "status": "active",
    "script_data": json.dumps(
        {"shots": [{"shotNumber": 1, "action": "walk", "characters": ["Detective"], "emotion": "tension"}]}
    ),
    "director_notes": None,
    "style_id": "cinematic",
    "aspect": "16:9",
    "locked_characters": json.dumps([]),
    "primary_character_ref": None,
    "mode": "cinematic",
}

TEST_ASSETS = [
    {
        "id": "asset_scene_001",
        "project_id": TEST_PROJECT_ID,
        "type": "scene",
        "name": "Hospital Exterior",
        "data": json.dumps({"description": "Dark hospital at night", "location": "Outskirts"}),
        "media_urls": json.dumps([]),
        "persistent_url": None,
        "shot_number": None,
        "version": 1,
        "confirmed": 0,
        "stale": 0,
    },
    {
        "id": "asset_scene_002",
        "project_id": TEST_PROJECT_ID,
        "type": "scene",
        "name": "Hospital Lobby",
        "data": json.dumps({"description": "Dim decayed lobby", "location": "Interior"}),
        "media_urls": json.dumps([]),
        "persistent_url": "/images/lobby.png",
        "shot_number": None,
        "version": 2,
        "confirmed": 1,
        "stale": 0,
    },
    {
        "id": "asset_char_001",
        "project_id": TEST_PROJECT_ID,
        "type": "character",
        "name": "Detective",
        "data": json.dumps({"description": "weathered detective", "appearance": "Tall, dark coat"}),
        "media_urls": json.dumps(["ref/det_front.png"]),
        "persistent_url": "/persist/det.png",
        "shot_number": None,
        "version": 1,
        "confirmed": 1,
        "stale": 0,
    },
    {
        "id": "asset_char_002",
        "project_id": TEST_PROJECT_ID,
        "type": "character",
        "name": "Mysterious Woman",
        "data": json.dumps({"description": "pale woman", "appearance": "White gown"}),
        "media_urls": json.dumps([]),
        "persistent_url": None,
        "shot_number": None,
        "version": 1,
        "confirmed": 0,
        "stale": 0,
    },
    {
        "id": "asset_sb_001",
        "project_id": TEST_PROJECT_ID,
        "type": "storyboard",
        "name": "Shot 1",
        "data": json.dumps({"description": "wide shot hospital approach", "duration": 8}),
        "media_urls": json.dumps([]),
        "persistent_url": None,
        "shot_number": 1,
        "version": 1,
        "confirmed": 0,
        "stale": 0,
    },
    {
        "id": "asset_sb_002",
        "project_id": TEST_PROJECT_ID,
        "type": "storyboard",
        "name": "Shot 2",
        "data": json.dumps({"description": "medium shot lobby entry", "duration": 10}),
        "media_urls": json.dumps([]),
        "persistent_url": "/persist/sb2.png",
        "shot_number": 2,
        "version": 1,
        "confirmed": 0,
        "stale": 0,
    },
]

_SCHEMA = """
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    title TEXT,
    status TEXT,
    script_data TEXT,
    director_notes TEXT,
    style_id TEXT,
    aspect TEXT DEFAULT '16:9',
    locked_characters TEXT,
    primary_character_ref TEXT,
    mode TEXT
);
CREATE TABLE project_assets (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    type TEXT,
    name TEXT,
    data TEXT,
    media_urls TEXT,
    persistent_url TEXT,
    shot_number INTEGER,
    version INTEGER DEFAULT 1,
    confirmed INTEGER DEFAULT 0,
    stale INTEGER DEFAULT 0
);
"""

_COLS_P = "id,user_id,title,status,script_data,director_notes,style_id,aspect,locked_characters,primary_character_ref,mode"
_COLS_A = "id,project_id,type,name,data,media_urls,persistent_url,shot_number,version,confirmed,stale"


def create_fixture_db(db_path: "str | Path") -> None:
    """Create a Wind Comic fixture database at db_path."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)

    placeholders_p = ", ".join(f":{c}" for c in _COLS_P.split(","))
    conn.execute(f"INSERT INTO projects ({_COLS_P}) VALUES ({placeholders_p})", TEST_PROJECT)

    placeholders_a = ", ".join(f":{c}" for c in _COLS_A.split(","))
    for asset in TEST_ASSETS:
        conn.execute(f"INSERT INTO project_assets ({_COLS_A}) VALUES ({placeholders_a})", asset)

    conn.commit()
    conn.close()
