# M1 Integration Core Implementation Plan (Final)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the minimum foundation to read an existing Wind Comic project, normalize its artifacts into our canonical production model, persist imported state, detect upstream changes (added/modified/deleted), and provide the LLM abstraction needed by later enrichment milestones.

**Architecture:** Hybrid Wind Comic Sidecar (ADR-001). Wind Comic runs separately and owns pre-production. Our application reads its SQLite database through a `WindComicAdapter` isolation boundary, normalizes artifacts into our own canonical `ProductionProject / Scene / CharacterReference` models with provenance tracking, persists them in our own SQLite database, and exposes them via FastAPI. An `LLMProvider` abstraction connects to Ollama for future enrichment use.

**Tech Stack:** Python (version determined by compatibility gate — 3.14 preferred, 3.13/3.12 fallback from locally installed versions only), FastAPI, Pydantic v2, SQLite (stdlib `sqlite3`), httpx, pytest

## Global Constraints

- Architecture is FROZEN V1 — do not change ADR-001 through ADR-005
- Wind Comic is a read-only sidecar — NEVER write to `qfmj.db`
- Wind Comic adapter connection MUST be read-only at the driver level (`mode=ro`); do NOT fall back to writable access
- `experiments/wind-comic/` is a nested Git repository — must NOT be absorbed into our repo
- No H3/ComfyUI generation code in M1 (M3 scope)
- No beat enrichment or coverage planning in M1 (M2 scope)
- No frontend/UI in M1 (M9 scope)
- No provider-specific prompt artifacts in M1 (ADR-005, M3 scope)
- Secrets (API keys) never in source code — environment variables only
- ComfyUI MCP is development tooling only (ADR-004) — never in production code
- GenerationRequest/Take immutability rules per ADR-005 apply in later milestones, not M1
- Dependency management via `venv` + `pip` (uv not currently installed)
- Use Superpowers `test-driven-development` skill during implementation — write tests FIRST
- M1 LLM support: Ollama ONLY. LM Studio and OpenRouter are config placeholders; calling them raises `ConfigurationError`
- `max_retries` means ADDITIONAL attempts after the first. `max_retries=2` → 3 total attempts maximum
- Exit criterion 5 (Ollama structured JSON) requires a LIVE integration test — parser-only tests are insufficient
- Python compatibility gate MUST NOT auto-install Python. Use locally available versions only. If no compatible version exists, M1 is BLOCKED.
- All ProductionProjects in M1 originate from Wind Comic — `provenance` is REQUIRED (not optional)
- Deleted upstream WC artifacts are preserved as OUTDATED in our DB — never physically deleted
- Reimport updates existing entities and clears OUTDATED status back to active/draft
- WindComicSchemaError must be raised (not raw sqlite errors) when WC DB has missing tables/columns
- **CharacterReference normalization (Task 7):** WC `media_urls` MUST be preserved. Non-empty `persistent_url` MUST also be preserved. Deduplicate while preserving stable order. Normalize combined references into `CharacterReference.turnaround_paths` as: `dedupe_preserve_order(media_urls + [persistent_url if non-empty])`. Do NOT classify `persistent_url` as `face_ref_path` — that classification is deferred to M5 Reference Management. This is a non-lossy normalization clarification, not an architecture change.

## Authority Order (for implementers)

When documents conflict, precedence is:
1. Accepted ADRs (`docs/architecture/ADR-*.md`)
2. `docs/ARCHITECTURE_V1.md`
3. `docs/ROADMAP_V2.md`
4. `docs/DEVELOPMENT_STATE.md`
5. M0 empirical findings (`docs/M0_*.md`)
6. Original specification (`Техническое задание и roadmap_...md`)

## File Structure

```
D:\Ai\Local AI Film Director\
  .gitignore
  .env.example
  pyproject.toml
  docs/                              # (already exists — not modified)
  experiments/wind-comic/            # (already exists — gitignored, not modified)
  src/
    film_director/
      __init__.py
      main.py                        # FastAPI app factory, dependency wiring, error handlers
      config.py                      # pydantic-settings configuration
      errors.py                      # Error taxonomy
      models/
        __init__.py
        canonical.py                 # ProductionProject, Sequence, Scene, CharacterReference
        provenance.py                # Provenance dataclass, hashing, source payload builders
        wind_comic_dto.py            # WC raw data transfer objects (adapter output)
      adapters/
        __init__.py
        wind_comic.py                # WindComicAdapter — reads WC SQLite (read-only)
      persistence/
        __init__.py
        database.py                  # Our SQLite connection + schema init
        repositories.py              # CRUD for our canonical entities
      services/
        __init__.py
        import_service.py            # WC import pipeline orchestration
      llm/
        __init__.py
        provider.py                  # LLMProvider protocol, LLMResponse, parse_llm_json
        ollama.py                    # OllamaProvider + create_llm_provider factory
      api/
        __init__.py
        routes.py                    # All FastAPI route definitions (APIRouter)
  tests/
    __init__.py
    conftest.py                      # Shared fixtures
    fixtures/
      __init__.py
      wind_comic_fixture.py          # WC SQLite fixture builder
    unit/
      __init__.py
      test_config.py
      test_canonical_models.py
      test_provenance.py
      test_wind_comic_adapter.py
      test_repositories.py
      test_llm_provider.py
    integration/
      __init__.py
      test_import_pipeline.py
      test_persistence_restart.py
      test_api.py
      test_ollama_live.py            # REQUIRED for M1 acceptance
```

## M1 Canonical Entities

| Entity | Table | Provenance | Status Field | Populated by |
|--------|-------|-----------|-------------|-------------|
| ProductionProject | production_projects | YES — **REQUIRED** | draft / active / outdated | Import |
| Sequence | sequences | NO (synthetic) | NO | Import (auto-created) |
| Scene | scenes | YES — REQUIRED | draft / ready / outdated | Import |
| CharacterReference | character_references | YES — REQUIRED | active / outdated | Import |

Entities NOT created in M1: Beat, ShotSpecificationV1, GenerationPlan, H3PromptV1, GenerationRequest, Take, ContinuityState, ReviewResult.

## Error Taxonomy → HTTP Mapping

| Error | HTTP Status | When |
|-------|-----------|------|
| `WindComicNotFoundError` | 404 | Requested WC project/asset does not exist in WC DB |
| `WindComicArtifactMalformedError` | 422 | WC asset data is corrupt/unparseable JSON |
| `WindComicSchemaError` | 502 | WC DB exists but has missing tables or columns |
| `WindComicUnavailableError` | 503 | WC database file missing or unreadable |
| `ConfigurationError` | N/A (startup) | Invalid config, unsupported LLM provider |
| `LLMUnavailableError` | 503 | LLM provider not reachable |
| `LLMStructuredOutputError` | 422 | LLM response not parseable as JSON |
| `NormalizationError` | 500 | Internal transform failure |
| `PersistenceError` | 500 | Our database read/write failure |

## Adapter → Normalization Boundary

```
Wind Comic SQLite (qfmj.db)
    ↓  WindComicAdapter (read-only SQL, mode=ro)
WC DTOs (WCProject, WCScene, WCCharacter, WCStoryboardShot)
    ↓  ImportService (normalization + provenance hashing)
Canonical entities (ProductionProject, Scene, CharacterReference)
    ↓  Repositories (persistence)
Our SQLite (production.db)
```

- `WindComicAdapter.get_project()` returns `WCProject` (a DTO), NOT our `ProductionProject`
- The adapter NEVER returns canonical types directly
- `persistent_url` is read from WC and included in DTOs — not silently discarded

## Provenance Hash Scope

Source payloads include ALL semantic fields affecting our normalized artifact. Volatile/non-semantic WC flags (`confirmed`, `stale`) are EXCLUDED.

| Entity | Source Payload Fields | Excluded |
|--------|---------------------|----------|
| Scene | `asset_id`, `name`, `data`, `media_urls`, `persistent_url`, `version` | WC `confirmed`, `stale` |
| CharacterReference | `asset_id`, `name`, `data`, `media_urls`, `persistent_url`, `version` | WC `confirmed`, `stale` |
| ProductionProject | `id`, `title`, `aspect`, `style_id` | WC `status`, `user_id`, `pipeline_state`, billing |

## Change Detection

Three change types:

| Type | Meaning | `entity_id` |
|------|---------|------------|
| `added` | WC has an asset not present in our imported state | `None` (no canonical entity yet) |
| `modified` | Provenance hash mismatch | Our entity ID |
| `deleted` | Our imported asset no longer exists in WC | Our entity ID |

**`check_for_changes()`** — side-effect-free, returns `list[ChangeDetection]`
**`apply_detected_changes()`** — marks affected entities OUTDATED; for `added` artifacts, marks the parent project OUTDATED (does NOT auto-import)

## Import Lifecycle

```
FIRST IMPORT
  → canonical entities created (active/draft)
  → provenance snapshot stored

CHECK UPSTREAM
  → reports added/modified/deleted (side-effect-free)

APPLY CHANGES
  → modified/deleted entities → OUTDATED
  → added child → parent project OUTDATED
  → no auto-import, no physical deletion

REIMPORT
  → reads current WC source
  → updates existing entities (upsert)
  → imports newly added entities
  → replaces provenance snapshots
  → clears OUTDATED → back to active/draft
  → entities deleted upstream remain OUTDATED (never physically deleted)
```

## Source-ID Unique Constraints

| Table | Constraint | Purpose |
|-------|-----------|---------|
| `production_projects` | `UNIQUE(wc_project_id)` | One canonical project per WC project |
| `scenes` | `UNIQUE(sequence_id, wc_scene_id)` | No duplicate scene imports per sequence |
| `character_references` | `UNIQUE(project_id, wc_character_id)` | No duplicate character imports per project |

## M1 Exit Criteria Mapping

| Exit Criterion | Verified By |
|---------------|-------------|
| 1. `WindComicAdapter.get_project()` returns normalized data | `test_wind_comic_adapter.py` (DTO) + `test_api.py` (full pipeline) |
| 2. `WindComicAdapter.get_storyboard()` returns storyboard shots | `test_wind_comic_adapter.py` |
| 3. Imported data persists across restart | `test_persistence_restart.py` |
| 4. Provenance hash detects source changes | `test_import_pipeline.py` (added/modified/deleted for scenes and characters) |
| 5. `LLMProvider.chat()` returns structured JSON from Ollama | `test_ollama_live.py` — **REQUIRED live test** |

---

## Superpowers Implementation Workflow

1. **`using-git-worktrees`** — After Task 1 baseline commit. Follow recommendation. Record workspace/branch.
2. **`test-driven-development`** — RED-GREEN-REFACTOR for every task.
3. **`subagent-driven-development`** — Fresh subagent per task. Code review between checkpoints.
4. **`systematic-debugging`** — If any test fails unexpectedly.
5. **`requesting-code-review`** — After all tasks, before declaring M1 done.
6. **`verification-before-completion`** — Full suite including live Ollama. If blocked, report — don't downgrade.

---

## Task Dependency Table

| Task | Requires | Produces |
|------|----------|----------|
| 1. Repository Bootstrap | nothing | git repo, .gitignore, .env.example |
| 2. Python Compat Gate | git repo | validated Python venv |
| 3. Backend Scaffold | venv | `config.py`, `errors.py`, `main.py` (factory + /health), pyproject.toml, conftest.py |
| 4. Canonical Models | `errors.py` | `canonical.py`, `provenance.py`, `wind_comic_dto.py` |
| 5. WindComicAdapter | `wind_comic_dto.py`, `errors.py` | `wind_comic.py`, `wind_comic_fixture.py` |
| 6. Persistence | `canonical.py`, `provenance.py` | `database.py`, `repositories.py` |
| 7. Import Service | `wind_comic.py`, `repositories.py`, `provenance.py`, `canonical.py` | `import_service.py` |
| 8. LLM Provider | `config.py`, `errors.py` | `provider.py`, `ollama.py` |
| 9. API + Verification | `import_service.py`, `ollama.py`, `repositories.py`, `main.py` | `routes.py`, wired `main.py`, all integration tests |

**Forward dependencies: ZERO.** Every task only imports from earlier tasks.

---

### Task 1: Repository Bootstrap (M1.A)

**Files:** Create `.gitignore`, `.env.example`

**Interfaces:**
- Consumes: nothing
- Produces: git repository with tracked docs, gitignored secrets/runtime

- [ ] **Step 1: Create `.gitignore`**

```gitignore
# Python
__pycache__/
*.py[cod]
*$py.class
*.egg-info/
dist/
build/
.eggs/

# Virtual environment
.venv/
venv/
env/

# IDE
.vscode/
.idea/
*.swp
*.swo

# Environment / secrets
.env
.env.local
.env.production

# Our runtime data
data/*.db
data/*.db-journal
data/*.db-wal
storage/

# Wind Comic sidecar (separate git repo)
experiments/wind-comic/

# Node (future frontend)
node_modules/

# OS
.DS_Store
Thumbs.db
Desktop.ini

# Test / coverage
.pytest_cache/
.coverage
htmlcov/
.mypy_cache/

# Temporary
*.tmp
*.log
```

- [ ] **Step 2: Create `.env.example`**

```dotenv
# Local AI Film Director — Environment Configuration
# Copy to .env and fill in values

FILM_DATABASE_PATH=data/production.db
FILM_STORAGE_ROOT=storage
FILM_WC_DATABASE_PATH=experiments/wind-comic/data/qfmj.db

# LLM Provider: only "ollama" supported in M1
FILM_LLM_PROVIDER=ollama
FILM_LLM_MODEL=gemma4:e4b
FILM_OLLAMA_BASE_URL=http://127.0.0.1:11434/v1

# Future providers (not implemented in M1)
FILM_LM_STUDIO_BASE_URL=http://127.0.0.1:1234/v1
FILM_OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
FILM_OPENROUTER_API_KEY=

# ComfyUI (not used in M1)
FILM_COMFYUI_BASE_URL=http://127.0.0.1:8188

FILM_LOG_LEVEL=INFO
# max_retries = additional attempts after first (2 means 3 total)
FILM_LLM_TIMEOUT_SECONDS=120
FILM_LLM_MAX_RETRIES=2
```

- [ ] **Step 3: git init, verify WC isolation, baseline commit**

```bash
cd "D:\Ai\Local AI Film Director"
git init
git status  # verify experiments/wind-comic/ NOT listed
git add .gitignore .env.example docs/ "Техническое задание и roadmap_ Local AI Film Director - Production Orchestrator.md"
git commit -m "M1.A: repository bootstrap with architecture docs and discovery reports"
git log --oneline && git status
```

- [ ] **Step 4: Invoke `using-git-worktrees` skill. Record workspace/branch.**

If no worktree created, create feature branch:

```bash
git checkout -b m1-integration-core
```

---

### Task 2: Python Compatibility Gate (M1.A2)

**Interfaces:**
- Consumes: git repo
- Produces: validated Python venv at `.venv/`

- [ ] **Step 1: Inventory locally available Python versions**

```bash
py -0p 2>/dev/null || echo "py launcher not available"
where python
python --version
```

Record all available versions. Do NOT download or install Python.

- [ ] **Step 2: Test Python 3.14 compatibility**

```bash
python -m venv .venv
.venv/Scripts/activate
pip install fastapi pydantic pydantic-settings httpx pytest pytest-asyncio uvicorn
python -c "import fastapi; import pydantic; import pydantic_settings; import httpx; import pytest; print('All imports OK')"
python -c "
from fastapi import FastAPI
from fastapi.testclient import TestClient
app = FastAPI()
@app.get('/t')
def t(): return {'ok': True}
c = TestClient(app)
r = c.get('/t')
assert r.status_code == 200 and r.json() == {'ok': True}
print('FastAPI TestClient smoke test PASSED')
"
```

- [ ] **Step 3: Decision gate**

**PASS (all steps succeed):** Use Python 3.14. Continue with `.venv`.

**FAIL:** Delete `.venv`. Check Step 1 inventory for 3.13 or 3.12. Repeat Step 2 with the fallback version (e.g., `py -3.13 -m venv .venv`).

**No compatible Python available locally:** M1 is **BLOCKED**. Report evidence. Do NOT auto-install Python. Do NOT reuse ComfyUI's Python. Wait for explicit approval.

- [ ] **Step 4: Record result in next commit message.**

---

### Task 3: Backend Scaffold (M1.B)

**Files:** Create `pyproject.toml`, `src/film_director/__init__.py`, `config.py`, `errors.py`, `main.py`, `tests/conftest.py`, `tests/unit/test_config.py`, `tests/unit/test_health.py`

**Interfaces:**
- Consumes: validated venv
- Produces: `Settings`, `FilmDirectorError` hierarchy (including `WindComicNotFoundError`, `WindComicSchemaError`), `create_app()`, `GET /health`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "film-director"
version = "0.1.0"
description = "Local AI Film Director — Production Orchestrator"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "pydantic>=2.9.0",
    "pydantic-settings>=2.6.0",
    "httpx>=0.27.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "httpx",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "integration: tests needing external services",
    "live: tests needing running Ollama or real WC DB (REQUIRED for M1 acceptance)",
]
asyncio_mode = "auto"

[build-system]
requires = ["setuptools>=75.0"]
build-backend = "setuptools.backends._legacy:_Backend"

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 2: Install, create package dirs, create `__init__.py` files**

```bash
pip install -e ".[dev]"
mkdir -p src/film_director/models src/film_director/adapters src/film_director/persistence src/film_director/services src/film_director/llm src/film_director/api
mkdir -p tests/unit tests/integration tests/fixtures
```

Create `__init__.py` in every package. `src/film_director/__init__.py`: `"""Local AI Film Director — Production Orchestrator."""`

- [ ] **Step 3: Write failing config test → implement `config.py`**

`tests/unit/test_config.py`:
```python
from film_director.config import Settings

def test_default_settings():
    s = Settings(_env_file=None, wc_database_path="test.db")
    assert s.database_path == "data/production.db"
    assert s.llm_provider == "ollama"
    assert s.llm_model == "gemma4:e4b"
    assert s.llm_max_retries == 2
    assert s.openrouter_api_key is None

def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("FILM_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("FILM_OPENROUTER_API_KEY", "sk-test")
    s = Settings(_env_file=None, wc_database_path="test.db")
    assert s.llm_provider == "openrouter"
    assert s.openrouter_api_key == "sk-test"
```

`src/film_director/config.py`:
```python
"""Application configuration via environment variables."""
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FILM_", env_file=".env", env_file_encoding="utf-8", extra="ignore")
    database_path: str = "data/production.db"
    storage_root: str = "storage"
    wc_database_path: str
    llm_provider: str = "ollama"
    llm_model: str = "gemma4:e4b"
    llm_timeout_seconds: int = 120
    llm_max_retries: int = 2
    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    lm_studio_base_url: str = "http://127.0.0.1:1234/v1"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_api_key: str | None = None
    comfyui_base_url: str = "http://127.0.0.1:8188"
    log_level: str = "INFO"
```

- [ ] **Step 4: Implement `errors.py`**

`src/film_director/errors.py`:
```python
"""Error taxonomy for Local AI Film Director."""

class FilmDirectorError(Exception):
    def __init__(self, message: str, detail: str | None = None):
        self.message = message
        self.detail = detail
        super().__init__(message)

class WindComicUnavailableError(FilmDirectorError):
    """WC database file missing or unreadable."""

class WindComicSchemaError(FilmDirectorError):
    """WC DB exists but has missing/unexpected tables or columns."""

class WindComicNotFoundError(FilmDirectorError):
    """Requested WC project or asset does not exist."""

class WindComicArtifactMalformedError(FilmDirectorError):
    """WC asset data is corrupt JSON or missing required fields."""

class NormalizationError(FilmDirectorError):
    """Failed to normalize WC data to canonical model."""

class PersistenceError(FilmDirectorError):
    """Our database read/write failure."""

class LLMUnavailableError(FilmDirectorError):
    """LLM provider not reachable."""

class LLMStructuredOutputError(FilmDirectorError):
    """LLM response not parseable as structured JSON."""

class ConfigurationError(FilmDirectorError):
    """Invalid or missing configuration."""
```

- [ ] **Step 5: Write health test → implement `main.py` (factory only, routes later)**

`tests/conftest.py`:
```python
"""Shared test fixtures."""
import pytest
from fastapi.testclient import TestClient
from film_director.config import Settings
from film_director.main import create_app

@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, database_path=str(tmp_path / "test.db"), storage_root=str(tmp_path / "storage"), wc_database_path=str(tmp_path / "wc_test.db"))

@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))
```

`tests/unit/test_health.py`:
```python
def test_health_is_lightweight(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"
    assert "integrations" not in body
```

`src/film_director/main.py`:
```python
"""FastAPI application factory."""
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from film_director.config import Settings
from film_director.errors import (
    FilmDirectorError, WindComicNotFoundError, WindComicArtifactMalformedError,
    WindComicSchemaError, WindComicUnavailableError, LLMUnavailableError,
    LLMStructuredOutputError, NormalizationError, PersistenceError,
)

logger = logging.getLogger(__name__)

_ERROR_STATUS: dict[type, int] = {
    WindComicNotFoundError: 404, WindComicArtifactMalformedError: 422,
    LLMStructuredOutputError: 422, WindComicSchemaError: 502,
    WindComicUnavailableError: 503, LLMUnavailableError: 503,
    NormalizationError: 500, PersistenceError: 500,
}

def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = Settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = FastAPI(title="Local AI Film Director", version="0.1.0")
    app.state.settings = settings

    @app.exception_handler(FilmDirectorError)
    def handle_error(request: Request, exc: FilmDirectorError):
        return JSONResponse(status_code=_ERROR_STATUS.get(type(exc), 500), content={"error": exc.message, "detail": exc.detail})

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    return app
```

- [ ] **Step 6: Run all tests, commit**

```bash
pytest -v
git add src/ tests/ pyproject.toml
git commit -m "M1.B: backend scaffold — Python <VERSION> verified, FastAPI, config, errors, health"
```

---

### Task 4: Canonical Models + Provenance (M1.C)

**Files:** Create `models/provenance.py`, `models/canonical.py`, `models/wind_comic_dto.py`, `tests/unit/test_provenance.py`, `tests/unit/test_canonical_models.py`

**Interfaces:**
- Consumes: `errors.py`
- Produces: `Provenance`, `compute_source_hash()`, `build_*_source_payload()`, `ProductionProject` (provenance REQUIRED), `Scene`, `CharacterReference`, `Sequence`, WC DTOs with `persistent_url`

- [ ] **Step 1: Write provenance tests (including persistent_url detection)**

`tests/unit/test_provenance.py`:
```python
from film_director.models.provenance import Provenance, compute_source_hash, build_scene_source_payload, build_character_source_payload, build_project_source_payload
from film_director.models.wind_comic_dto import WCScene, WCCharacter, WCProject

def test_hash_deterministic():
    d = {"a": 1, "b": 2}
    assert compute_source_hash(d) == compute_source_hash(d)
    assert len(compute_source_hash(d)) == 64

def test_hash_key_order_independent():
    assert compute_source_hash({"z": 1, "a": 2}) == compute_source_hash({"a": 2, "z": 1})

def test_hash_detects_change():
    assert compute_source_hash({"x": 1}) != compute_source_hash({"x": 2})

def test_hash_unicode():
    assert len(compute_source_hash({"n": "探偵"})) == 64

def test_scene_payload_detects_description_change():
    s1 = WCScene(asset_id="s1", project_id="p1", name="X", data={"description": "dark"}, media_urls=[], persistent_url=None, version=1)
    s2 = WCScene(asset_id="s1", project_id="p1", name="X", data={"description": "bright"}, media_urls=[], persistent_url=None, version=1)
    assert compute_source_hash(build_scene_source_payload(s1)) != compute_source_hash(build_scene_source_payload(s2))

def test_scene_payload_detects_name_change():
    s1 = WCScene(asset_id="s1", project_id="p1", name="Ext", data={}, media_urls=[], persistent_url=None, version=1)
    s2 = WCScene(asset_id="s1", project_id="p1", name="Int", data={}, media_urls=[], persistent_url=None, version=1)
    assert compute_source_hash(build_scene_source_payload(s1)) != compute_source_hash(build_scene_source_payload(s2))

def test_character_payload_detects_appearance_change():
    c1 = WCCharacter(asset_id="c1", project_id="p1", name="D", data={"appearance": "tall"}, media_urls=[], persistent_url=None, version=1)
    c2 = WCCharacter(asset_id="c1", project_id="p1", name="D", data={"appearance": "short"}, media_urls=[], persistent_url=None, version=1)
    assert compute_source_hash(build_character_source_payload(c1)) != compute_source_hash(build_character_source_payload(c2))

def test_character_payload_detects_media_url_change():
    c1 = WCCharacter(asset_id="c1", project_id="p1", name="D", data={}, media_urls=["a.png"], persistent_url=None, version=1)
    c2 = WCCharacter(asset_id="c1", project_id="p1", name="D", data={}, media_urls=["b.png"], persistent_url=None, version=1)
    assert compute_source_hash(build_character_source_payload(c1)) != compute_source_hash(build_character_source_payload(c2))

def test_character_payload_detects_persistent_url_change():
    c1 = WCCharacter(asset_id="c1", project_id="p1", name="D", data={}, media_urls=[], persistent_url="old.png", version=1)
    c2 = WCCharacter(asset_id="c1", project_id="p1", name="D", data={}, media_urls=[], persistent_url="new.png", version=1)
    assert compute_source_hash(build_character_source_payload(c1)) != compute_source_hash(build_character_source_payload(c2))

def test_project_payload_detects_title_change():
    p1 = WCProject(id="p1", title="A", status="active", aspect="16:9", style_id=None, script_data=None, locked_characters=[])
    p2 = WCProject(id="p1", title="B", status="active", aspect="16:9", style_id=None, script_data=None, locked_characters=[])
    assert compute_source_hash(build_project_source_payload(p1)) != compute_source_hash(build_project_source_payload(p2))

def test_project_payload_ignores_wc_status():
    p1 = WCProject(id="p1", title="F", status="active", aspect="16:9", style_id=None, script_data=None, locked_characters=[])
    p2 = WCProject(id="p1", title="F", status="completed", aspect="16:9", style_id=None, script_data=None, locked_characters=[])
    assert compute_source_hash(build_project_source_payload(p1)) == compute_source_hash(build_project_source_payload(p2))
```

- [ ] **Step 2: Implement WC DTOs (with persistent_url)**

`src/film_director/models/wind_comic_dto.py`:
```python
"""Wind Comic raw data transfer objects."""
from dataclasses import dataclass

@dataclass(frozen=True)
class WCProject:
    id: str; title: str; status: str; aspect: str; style_id: str | None; script_data: dict | None; locked_characters: list[dict]

@dataclass(frozen=True)
class WCScene:
    asset_id: str; project_id: str; name: str; data: dict; media_urls: list[str]; persistent_url: str | None; version: int

@dataclass(frozen=True)
class WCCharacter:
    asset_id: str; project_id: str; name: str; data: dict; media_urls: list[str]; persistent_url: str | None; version: int

@dataclass(frozen=True)
class WCStoryboardShot:
    asset_id: str; project_id: str; shot_number: int; data: dict; media_urls: list[str]; persistent_url: str | None; version: int

@dataclass(frozen=True)
class WCHealth:
    available: bool; db_path: str; error: str | None = None
```

- [ ] **Step 3: Implement provenance with source payload builders**

`src/film_director/models/provenance.py`:
```python
"""Provenance tracking for imported artifacts."""
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from film_director.models.wind_comic_dto import WCCharacter, WCProject, WCScene

@dataclass(frozen=True)
class Provenance:
    source_system: str; source_project_id: str; source_asset_id: str; source_asset_version: int | None; imported_at: str; source_hash: str

def compute_source_hash(data: dict) -> str:
    normalized = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

def build_project_source_payload(wc: WCProject) -> dict:
    return {"id": wc.id, "title": wc.title, "aspect": wc.aspect, "style_id": wc.style_id}

def build_scene_source_payload(wc: WCScene) -> dict:
    return {"asset_id": wc.asset_id, "name": wc.name, "data": wc.data, "media_urls": wc.media_urls, "persistent_url": wc.persistent_url, "version": wc.version}

def build_character_source_payload(wc: WCCharacter) -> dict:
    return {"asset_id": wc.asset_id, "name": wc.name, "data": wc.data, "media_urls": wc.media_urls, "persistent_url": wc.persistent_url, "version": wc.version}
```

- [ ] **Step 4: Write canonical model tests (provenance REQUIRED on ProductionProject)**

`tests/unit/test_canonical_models.py`:
```python
import pytest
from pydantic import ValidationError
from film_director.models.canonical import ProductionProject, Sequence, Scene, CharacterReference
from film_director.models.provenance import Provenance

def _prov(**kw) -> Provenance:
    d = dict(source_system="wind_comic", source_project_id="p", source_asset_id="a", source_asset_version=1, imported_at="t", source_hash="a"*64)
    d.update(kw); return Provenance(**d)

def test_project_requires_provenance():
    with pytest.raises(ValidationError):
        ProductionProject(id="p1", wc_project_id="wc1", title="F")

def test_project_with_provenance():
    p = ProductionProject(id="p1", wc_project_id="wc1", title="F", provenance=_prov())
    assert p.status == "draft"
    assert p.provenance.source_system == "wind_comic"

def test_scene_statuses():
    for status in ("draft", "ready", "outdated"):
        s = Scene(id="s1", sequence_id="sq1", wc_scene_id="ws", name="N", location="", description="", order_index=0, status=status, provenance=_prov())
        assert s.status == status

def test_character_statuses():
    for status in ("active", "outdated"):
        c = CharacterReference(id="c1", project_id="p1", wc_character_id="wc", name="N", description="", appearance="", status=status, provenance=_prov())
        assert c.status == status

def test_character_defaults():
    c = CharacterReference(id="c1", project_id="p1", wc_character_id="wc", name="N", description="", appearance="", provenance=_prov())
    assert c.face_ref_path is None
    assert c.turnaround_paths == []
    assert c.status == "active"

def test_sequence_no_provenance():
    s = Sequence(id="sq1", project_id="p1", name="Main", order_index=0)
    assert s.order_index == 0
```

- [ ] **Step 5: Implement canonical models (provenance REQUIRED on ProductionProject)**

`src/film_director/models/canonical.py`:
```python
"""Canonical production specification models (M1 subset)."""
from pydantic import BaseModel, Field
from film_director.models.provenance import Provenance

class ProductionProject(BaseModel):
    id: str; wc_project_id: str; title: str
    status: str = "draft"  # draft | active | outdated
    aspect: str = "16:9"; created_at: str = ""; updated_at: str = ""
    provenance: Provenance  # REQUIRED — all M1 projects originate from WC

class Sequence(BaseModel):
    id: str; project_id: str; name: str; order_index: int

class Scene(BaseModel):
    id: str; sequence_id: str; wc_scene_id: str; name: str; location: str; description: str
    order_index: int; status: str = "draft"  # draft | ready | outdated
    provenance: Provenance

class CharacterReference(BaseModel):
    id: str; project_id: str; wc_character_id: str; name: str; description: str; appearance: str
    face_ref_path: str | None = None
    turnaround_paths: list[str] = Field(default_factory=list)
    visual_anchors: list[str] = Field(default_factory=list)
    status: str = "active"  # active | outdated
    provenance: Provenance
```

- [ ] **Step 6: Run tests, commit**

```bash
pytest tests/unit/test_provenance.py tests/unit/test_canonical_models.py -v
git add src/film_director/models/ tests/unit/test_provenance.py tests/unit/test_canonical_models.py
git commit -m "M1.C: canonical models — provenance REQUIRED, persistent_url in DTOs and hash scope"
```

---

### Task 5: Wind Comic Adapter (M1.D)

**Files:** Create `tests/fixtures/wind_comic_fixture.py`, `tests/unit/test_wind_comic_adapter.py`, `src/film_director/adapters/wind_comic.py`

**Interfaces:**
- Consumes: `wind_comic_dto.py`, `errors.py`
- Produces: `WindComicAdapter` with `health()`, `get_project()`, `get_scenes()`, `get_characters()`, `get_storyboard()`. Read-only mode=ro. Raises `WindComicSchemaError` for missing tables. Reads `persistent_url`.

- [ ] **Step 1: Create fixture builder (includes persistent_url)**

`tests/fixtures/wind_comic_fixture.py`:
```python
"""Wind Comic SQLite fixture matching v12.320 schema."""
import json, sqlite3
from pathlib import Path

TEST_PROJECT_ID = "test_proj_001"
TEST_PROJECT = {"id": TEST_PROJECT_ID, "user_id": "u1", "title": "The Abandoned Hospital", "status": "active", "script_data": json.dumps({"shots": [{"shotNumber": 1, "action": "walk", "characters": ["Detective"], "emotion": "tension"}]}), "director_notes": None, "style_id": "cinematic", "aspect": "16:9", "locked_characters": json.dumps([]), "primary_character_ref": None, "mode": "cinematic"}

TEST_ASSETS = [
    {"id": "asset_scene_001", "project_id": TEST_PROJECT_ID, "type": "scene", "name": "Hospital Exterior", "data": json.dumps({"description": "Dark hospital at night", "location": "Outskirts"}), "media_urls": json.dumps([]), "persistent_url": None, "shot_number": None, "version": 1, "confirmed": 0, "stale": 0},
    {"id": "asset_scene_002", "project_id": TEST_PROJECT_ID, "type": "scene", "name": "Hospital Lobby", "data": json.dumps({"description": "Dim decayed lobby", "location": "Interior"}), "media_urls": json.dumps([]), "persistent_url": "/images/lobby.png", "shot_number": None, "version": 2, "confirmed": 1, "stale": 0},
    {"id": "asset_char_001", "project_id": TEST_PROJECT_ID, "type": "character", "name": "Detective", "data": json.dumps({"description": "weathered detective", "appearance": "Tall, dark coat"}), "media_urls": json.dumps(["ref/det_front.png"]), "persistent_url": "/persist/det.png", "shot_number": None, "version": 1, "confirmed": 1, "stale": 0},
    {"id": "asset_char_002", "project_id": TEST_PROJECT_ID, "type": "character", "name": "Mysterious Woman", "data": json.dumps({"description": "pale woman", "appearance": "White gown"}), "media_urls": json.dumps([]), "persistent_url": None, "shot_number": None, "version": 1, "confirmed": 0, "stale": 0},
    {"id": "asset_sb_001", "project_id": TEST_PROJECT_ID, "type": "storyboard", "name": "Shot 1", "data": json.dumps({"description": "wide shot hospital approach", "duration": 8}), "media_urls": json.dumps([]), "persistent_url": None, "shot_number": 1, "version": 1, "confirmed": 0, "stale": 0},
    {"id": "asset_sb_002", "project_id": TEST_PROJECT_ID, "type": "storyboard", "name": "Shot 2", "data": json.dumps({"description": "medium shot lobby entry", "duration": 10}), "media_urls": json.dumps([]), "persistent_url": "/persist/sb2.png", "shot_number": 2, "version": 1, "confirmed": 0, "stale": 0},
]

_SCHEMA = """
CREATE TABLE projects (id TEXT PRIMARY KEY, user_id TEXT, title TEXT, status TEXT, script_data TEXT, director_notes TEXT, style_id TEXT, aspect TEXT DEFAULT '16:9', locked_characters TEXT, primary_character_ref TEXT, mode TEXT);
CREATE TABLE project_assets (id TEXT PRIMARY KEY, project_id TEXT, type TEXT, name TEXT, data TEXT, media_urls TEXT, persistent_url TEXT, shot_number INTEGER, version INTEGER DEFAULT 1, confirmed INTEGER DEFAULT 0, stale INTEGER DEFAULT 0);
"""

def create_fixture_db(db_path: str | Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    cols_p = "id,user_id,title,status,script_data,director_notes,style_id,aspect,locked_characters,primary_character_ref,mode"
    conn.execute(f"INSERT INTO projects ({cols_p}) VALUES (:{',  :'.join(cols_p.split(','))})", TEST_PROJECT)
    cols_a = "id,project_id,type,name,data,media_urls,persistent_url,shot_number,version,confirmed,stale"
    for a in TEST_ASSETS:
        conn.execute(f"INSERT INTO project_assets ({cols_a}) VALUES (:{', :'.join(cols_a.split(','))})", a)
    conn.commit(); conn.close()
```

Add fixtures to `tests/conftest.py` (append):
```python
from tests.fixtures.wind_comic_fixture import create_fixture_db, TEST_PROJECT_ID

@pytest.fixture
def wc_db_path(tmp_path):
    p = tmp_path / "qfmj.db"; create_fixture_db(p); return str(p)

@pytest.fixture
def wc_project_id():
    return TEST_PROJECT_ID
```

- [ ] **Step 2: Write adapter tests (including schema error and read-only)**

`tests/unit/test_wind_comic_adapter.py`:
```python
import sqlite3, pytest
from film_director.adapters.wind_comic import WindComicAdapter
from film_director.errors import WindComicUnavailableError, WindComicNotFoundError, WindComicArtifactMalformedError, WindComicSchemaError

class TestHealth:
    def test_available(self, wc_db_path):
        assert WindComicAdapter(wc_db_path).health().available is True
    def test_missing_db(self, tmp_path):
        assert WindComicAdapter(str(tmp_path / "no.db")).health().available is False

class TestReadOnly:
    def test_write_rejected(self, wc_db_path):
        conn = WindComicAdapter(wc_db_path)._connect()
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO projects (id, title) VALUES ('x','x')")
        conn.close()

class TestSchemaError:
    def test_missing_table_raises_schema_error(self, tmp_path):
        db = str(tmp_path / "bad_schema.db")
        sqlite3.connect(db).execute("CREATE TABLE other (x TEXT)").connection.commit()
        with pytest.raises(WindComicSchemaError):
            WindComicAdapter(db).get_project("any")

    def test_missing_column_raises_schema_error(self, tmp_path):
        db = str(tmp_path / "bad_cols.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, title TEXT)")  # missing columns
        conn.execute("INSERT INTO projects VALUES ('p1', 'T')")
        conn.commit(); conn.close()
        with pytest.raises(WindComicSchemaError):
            WindComicAdapter(db).get_project("p1")

class TestGetProject:
    def test_returns_dto(self, wc_db_path, wc_project_id):
        p = WindComicAdapter(wc_db_path).get_project(wc_project_id)
        assert p.id == wc_project_id
        assert p.title == "The Abandoned Hospital"
    def test_not_found(self, wc_db_path):
        with pytest.raises(WindComicNotFoundError):
            WindComicAdapter(wc_db_path).get_project("nope")

class TestGetScenes:
    def test_returns_scenes(self, wc_db_path, wc_project_id):
        scenes = WindComicAdapter(wc_db_path).get_scenes(wc_project_id)
        assert len(scenes) == 2
    def test_persistent_url_read(self, wc_db_path, wc_project_id):
        scenes = WindComicAdapter(wc_db_path).get_scenes(wc_project_id)
        lobby = next(s for s in scenes if s.name == "Hospital Lobby")
        assert lobby.persistent_url == "/images/lobby.png"
    def test_empty_project(self, wc_db_path):
        assert WindComicAdapter(wc_db_path).get_scenes("nope") == []

class TestGetCharacters:
    def test_returns_characters(self, wc_db_path, wc_project_id):
        chars = WindComicAdapter(wc_db_path).get_characters(wc_project_id)
        assert len(chars) == 2
    def test_persistent_url_read(self, wc_db_path, wc_project_id):
        chars = WindComicAdapter(wc_db_path).get_characters(wc_project_id)
        det = next(c for c in chars if c.name == "Detective")
        assert det.persistent_url == "/persist/det.png"
        assert det.media_urls == ["ref/det_front.png"]

class TestGetStoryboard:
    def test_ordered_shots(self, wc_db_path, wc_project_id):
        shots = WindComicAdapter(wc_db_path).get_storyboard(wc_project_id)
        assert len(shots) == 2
        assert shots[0].shot_number == 1
    def test_persistent_url_in_storyboard(self, wc_db_path, wc_project_id):
        shots = WindComicAdapter(wc_db_path).get_storyboard(wc_project_id)
        assert shots[1].persistent_url == "/persist/sb2.png"

class TestMalformedData:
    def test_corrupt_json(self, tmp_path):
        db = str(tmp_path / "bad.db")
        conn = sqlite3.connect(db)
        conn.executescript("CREATE TABLE projects (id TEXT PRIMARY KEY, user_id TEXT, title TEXT, status TEXT, script_data TEXT, director_notes TEXT, style_id TEXT, aspect TEXT, locked_characters TEXT, primary_character_ref TEXT, mode TEXT); CREATE TABLE project_assets (id TEXT PRIMARY KEY, project_id TEXT, type TEXT, name TEXT, data TEXT, media_urls TEXT, persistent_url TEXT, shot_number INTEGER, version INTEGER, confirmed INTEGER, stale INTEGER);")
        conn.execute("INSERT INTO projects VALUES ('p1','u','T','active',NULL,NULL,NULL,'16:9','[]',NULL,'cinematic')")
        conn.execute("INSERT INTO project_assets VALUES ('a1','p1','scene','S','{bad json','[]',NULL,NULL,1,0,0)")
        conn.commit(); conn.close()
        with pytest.raises(WindComicArtifactMalformedError):
            WindComicAdapter(db).get_scenes("p1")
```

- [ ] **Step 3: Implement WindComicAdapter (with schema error handling, persistent_url)**

`src/film_director/adapters/wind_comic.py`:
```python
"""Wind Comic SQLite adapter — strictly read-only. mode=ro, no writable fallback."""
import json, logging, sqlite3
from pathlib import Path
from film_director.errors import WindComicArtifactMalformedError, WindComicNotFoundError, WindComicSchemaError, WindComicUnavailableError
from film_director.models.wind_comic_dto import WCCharacter, WCHealth, WCProject, WCScene, WCStoryboardShot

logger = logging.getLogger(__name__)

class WindComicAdapter:
    def __init__(self, db_path: str):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        if not Path(self._db_path).exists():
            raise WindComicUnavailableError(f"WC database not found: {self._db_path}")
        conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _parse_json(self, raw: str | None, ctx: str) -> dict | list:
        if raw is None: return {}
        try: return json.loads(raw)
        except json.JSONDecodeError as e: raise WindComicArtifactMalformedError(f"Malformed JSON in {ctx}", detail=str(e))

    def _parse_json_list(self, raw: str | None, ctx: str) -> list:
        r = self._parse_json(raw, ctx); return r if isinstance(r, list) else []

    def _query(self, sql: str, params: tuple = ()) -> list:
        conn = self._connect()
        try:
            return conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:
            if "no such table" in str(e) or "no such column" in str(e):
                raise WindComicSchemaError(f"WC schema incompatible: {e}", detail=str(e))
            raise
        finally:
            conn.close()

    def _query_one(self, sql: str, params: tuple = ()):
        conn = self._connect()
        try:
            return conn.execute(sql, params).fetchone()
        except sqlite3.OperationalError as e:
            if "no such table" in str(e) or "no such column" in str(e):
                raise WindComicSchemaError(f"WC schema incompatible: {e}", detail=str(e))
            raise
        finally:
            conn.close()

    def health(self) -> WCHealth:
        try:
            conn = self._connect(); conn.execute("SELECT 1"); conn.close()
            return WCHealth(available=True, db_path=self._db_path)
        except Exception as e:
            return WCHealth(available=False, db_path=self._db_path, error=str(e))

    def get_project(self, project_id: str) -> WCProject:
        row = self._query_one("SELECT id,title,status,aspect,style_id,script_data,locked_characters FROM projects WHERE id=?", (project_id,))
        if row is None: raise WindComicNotFoundError(f"Project not found: {project_id}")
        return WCProject(id=row["id"], title=row["title"] or "", status=row["status"] or "draft", aspect=row["aspect"] or "16:9", style_id=row["style_id"],
            script_data=self._parse_json(row["script_data"], f"project {project_id}") or None,
            locked_characters=self._parse_json_list(row["locked_characters"], f"project {project_id} locked_chars"))

    def get_scenes(self, project_id: str) -> list[WCScene]:
        rows = self._query("SELECT id,project_id,name,data,media_urls,persistent_url,version FROM project_assets WHERE project_id=? AND type='scene' ORDER BY name", (project_id,))
        result = []
        for r in rows:
            d = self._parse_json(r["data"], f"scene {r['id']}")
            result.append(WCScene(asset_id=r["id"], project_id=r["project_id"], name=r["name"] or "", data=d if isinstance(d, dict) else {},
                media_urls=self._parse_json_list(r["media_urls"], f"scene {r['id']} media"), persistent_url=r["persistent_url"], version=r["version"] or 1))
        return result

    def get_characters(self, project_id: str) -> list[WCCharacter]:
        rows = self._query("SELECT id,project_id,name,data,media_urls,persistent_url,version FROM project_assets WHERE project_id=? AND type='character' ORDER BY name", (project_id,))
        result = []
        for r in rows:
            d = self._parse_json(r["data"], f"char {r['id']}")
            result.append(WCCharacter(asset_id=r["id"], project_id=r["project_id"], name=r["name"] or "", data=d if isinstance(d, dict) else {},
                media_urls=self._parse_json_list(r["media_urls"], f"char {r['id']} media"), persistent_url=r["persistent_url"], version=r["version"] or 1))
        return result

    def get_storyboard(self, project_id: str) -> list[WCStoryboardShot]:
        rows = self._query("SELECT id,project_id,shot_number,data,media_urls,persistent_url,version FROM project_assets WHERE project_id=? AND type='storyboard' ORDER BY shot_number", (project_id,))
        result = []
        for r in rows:
            d = self._parse_json(r["data"], f"sb {r['id']}")
            result.append(WCStoryboardShot(asset_id=r["id"], project_id=r["project_id"], shot_number=r["shot_number"] or 0, data=d if isinstance(d, dict) else {},
                media_urls=self._parse_json_list(r["media_urls"], f"sb {r['id']} media"), persistent_url=r["persistent_url"], version=r["version"] or 1))
        return result
```

- [ ] **Step 4: Run, commit**

```bash
pytest tests/unit/test_wind_comic_adapter.py -v
git add src/film_director/adapters/ tests/unit/test_wind_comic_adapter.py tests/fixtures/ tests/conftest.py
git commit -m "M1.D: WindComicAdapter — read-only, persistent_url, SchemaError for missing tables"
```

---

### Task 6: Persistence Layer (M1.E)

**Files:** Create `persistence/database.py`, `persistence/repositories.py`, `tests/unit/test_repositories.py`

**Interfaces:**
- Consumes: `canonical.py`, `provenance.py`
- Produces: `Database`, repositories with proper UPSERT (`ON CONFLICT DO UPDATE`), scoped UNIQUE constraints, `mark_outdated()` on all entity repos

- [ ] **Step 1: Write repository tests (including unique constraints, required provenance)**

`tests/unit/test_repositories.py` — key tests:

```python
import sqlite3, pytest
from film_director.models.canonical import ProductionProject, Sequence, Scene, CharacterReference
from film_director.models.provenance import Provenance
from film_director.persistence.database import Database
from film_director.persistence.repositories import ProjectRepository, SequenceRepository, SceneRepository, CharacterRepository

def _prov(**kw) -> Provenance:
    d = dict(source_system="wind_comic", source_project_id="p", source_asset_id="a", source_asset_version=1, imported_at="t", source_hash="a"*64)
    d.update(kw); return Provenance(**d)

@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db")); d.init_schema(); return d

@pytest.fixture
def project_repo(db): return ProjectRepository(db)
@pytest.fixture
def seq_repo(db): return SequenceRepository(db)
@pytest.fixture
def scene_repo(db): return SceneRepository(db)
@pytest.fixture
def char_repo(db): return CharacterRepository(db)

class TestProjectRepo:
    def test_save_get(self, project_repo):
        project_repo.save_project(ProductionProject(id="p1", wc_project_id="wc1", title="Film", created_at="t", updated_at="t", provenance=_prov(source_asset_id="wc1")))
        p = project_repo.get_project("p1")
        assert p.title == "Film" and p.provenance.source_hash == "a"*64

    def test_upsert_in_place(self, project_repo):
        project_repo.save_project(ProductionProject(id="p1", wc_project_id="wc1", title="V1", created_at="t", updated_at="t", provenance=_prov()))
        project_repo.save_project(ProductionProject(id="p1", wc_project_id="wc1", title="V2", created_at="t", updated_at="t2", provenance=_prov()))
        assert project_repo.get_project("p1").title == "V2"
        assert len(project_repo.list_projects()) == 1

    def test_wc_project_id_unique(self, db, project_repo):
        project_repo.save_project(ProductionProject(id="pA", wc_project_id="same", title="A", created_at="t", updated_at="t", provenance=_prov()))
        with pytest.raises(sqlite3.IntegrityError):
            with db.connection() as c:
                c.execute("INSERT INTO production_projects (id,wc_project_id,title,status,aspect,created_at,updated_at,prov_source_system,prov_source_project_id,prov_source_asset_id,prov_source_asset_version,prov_imported_at,prov_source_hash) VALUES ('pB','same','B','draft','16:9','t','t','w','p','a',1,'t','h')")

    def test_mark_outdated(self, project_repo):
        project_repo.save_project(ProductionProject(id="p1", wc_project_id="wc1", title="F", created_at="t", updated_at="t", provenance=_prov()))
        project_repo.mark_outdated("p1")
        assert project_repo.get_project("p1").status == "outdated"

class TestSceneRepo:
    def test_save_get(self, scene_repo):
        scene_repo.save_scene(Scene(id="s1", sequence_id="sq1", wc_scene_id="ws1", name="E", location="L", description="D", order_index=0, provenance=_prov()))
        assert len(scene_repo.get_scenes_by_sequence("sq1")) == 1

    def test_upsert(self, scene_repo):
        scene_repo.save_scene(Scene(id="s1", sequence_id="sq1", wc_scene_id="ws1", name="V1", location="", description="", order_index=0, provenance=_prov()))
        scene_repo.save_scene(Scene(id="s1", sequence_id="sq1", wc_scene_id="ws1", name="V2", location="", description="", order_index=0, provenance=_prov()))
        assert scene_repo.get_scenes_by_sequence("sq1")[0].name == "V2"

    def test_wc_scene_id_unique_per_sequence(self, db, scene_repo):
        scene_repo.save_scene(Scene(id="s1", sequence_id="sq1", wc_scene_id="ws_same", name="A", location="", description="", order_index=0, provenance=_prov()))
        with pytest.raises(sqlite3.IntegrityError):
            with db.connection() as c:
                c.execute("INSERT INTO scenes (id,sequence_id,wc_scene_id,name,location,description,order_index,status,prov_source_system,prov_source_project_id,prov_source_asset_id,prov_source_asset_version,prov_imported_at,prov_source_hash) VALUES ('s2','sq1','ws_same','B','','',1,'draft','w','p','a',1,'t','h')")

    def test_mark_outdated(self, scene_repo):
        scene_repo.save_scene(Scene(id="s1", sequence_id="sq1", wc_scene_id="ws1", name="E", location="", description="", order_index=0, provenance=_prov()))
        scene_repo.mark_outdated("s1")
        assert scene_repo.get_scene("s1").status == "outdated"

class TestCharRepo:
    def test_save_get(self, char_repo):
        char_repo.save_character(CharacterReference(id="c1", project_id="p1", wc_character_id="wc1", name="D", description="", appearance="", provenance=_prov()))
        assert char_repo.get_characters_by_project("p1")[0].status == "active"

    def test_wc_char_id_unique_per_project(self, db, char_repo):
        char_repo.save_character(CharacterReference(id="c1", project_id="p1", wc_character_id="wc_same", name="A", description="", appearance="", provenance=_prov()))
        with pytest.raises(sqlite3.IntegrityError):
            with db.connection() as c:
                c.execute("INSERT INTO character_references (id,project_id,wc_character_id,name,description,appearance,face_ref_path,turnaround_paths,visual_anchors,status,prov_source_system,prov_source_project_id,prov_source_asset_id,prov_source_asset_version,prov_imported_at,prov_source_hash) VALUES ('c2','p1','wc_same','B','','',NULL,'[]','[]','active','w','p','a',1,'t','h')")

    def test_mark_outdated(self, char_repo):
        char_repo.save_character(CharacterReference(id="c1", project_id="p1", wc_character_id="wc1", name="D", description="", appearance="", provenance=_prov()))
        char_repo.mark_outdated("c1")
        assert char_repo.get_characters_by_project("p1")[0].status == "outdated"

class TestRestart:
    def test_data_survives(self, tmp_path):
        path = str(tmp_path / "r.db")
        d1 = Database(path); d1.init_schema()
        ProjectRepository(d1).save_project(ProductionProject(id="p1", wc_project_id="wc1", title="Survives", created_at="t", updated_at="t", provenance=_prov()))
        d2 = Database(path); d2.init_schema()
        assert ProjectRepository(d2).get_project("p1").title == "Survives"
```

- [ ] **Step 2: Implement database.py (with UNIQUE constraints)**

`src/film_director/persistence/database.py`:
```python
"""Our SQLite database — separate from Wind Comic's."""
import logging, os, sqlite3
from contextlib import contextmanager

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS production_projects (
    id TEXT PRIMARY KEY, wc_project_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', aspect TEXT NOT NULL DEFAULT '16:9',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    prov_source_system TEXT NOT NULL, prov_source_project_id TEXT NOT NULL,
    prov_source_asset_id TEXT NOT NULL, prov_source_asset_version INTEGER,
    prov_imported_at TEXT NOT NULL, prov_source_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sequences (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, order_index INTEGER NOT NULL,
    FOREIGN KEY (project_id) REFERENCES production_projects(id)
);
CREATE TABLE IF NOT EXISTS scenes (
    id TEXT PRIMARY KEY, sequence_id TEXT NOT NULL, wc_scene_id TEXT NOT NULL,
    name TEXT NOT NULL, location TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
    order_index INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
    prov_source_system TEXT NOT NULL, prov_source_project_id TEXT NOT NULL,
    prov_source_asset_id TEXT NOT NULL, prov_source_asset_version INTEGER,
    prov_imported_at TEXT NOT NULL, prov_source_hash TEXT NOT NULL,
    UNIQUE(sequence_id, wc_scene_id),
    FOREIGN KEY (sequence_id) REFERENCES sequences(id)
);
CREATE TABLE IF NOT EXISTS character_references (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, wc_character_id TEXT NOT NULL,
    name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', appearance TEXT NOT NULL DEFAULT '',
    face_ref_path TEXT, turnaround_paths TEXT NOT NULL DEFAULT '[]',
    visual_anchors TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'active',
    prov_source_system TEXT NOT NULL, prov_source_project_id TEXT NOT NULL,
    prov_source_asset_id TEXT NOT NULL, prov_source_asset_version INTEGER,
    prov_imported_at TEXT NOT NULL, prov_source_hash TEXT NOT NULL,
    UNIQUE(project_id, wc_character_id),
    FOREIGN KEY (project_id) REFERENCES production_projects(id)
);
"""

class Database:
    def __init__(self, db_path: str):
        self._db_path = db_path

    def init_schema(self) -> None:
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        with self.connection() as conn: conn.executescript(SCHEMA_SQL)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self._db_path); conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL"); conn.execute("PRAGMA foreign_keys=ON")
        try: yield conn; conn.commit()
        except: conn.rollback(); raise
        finally: conn.close()
```

- [ ] **Step 3: Implement repositories.py (ON CONFLICT DO UPDATE, no INSERT OR REPLACE)**

`src/film_director/persistence/repositories.py` — same structure as previous revision but with `ON CONFLICT(id) DO UPDATE SET ...` for all entities, plus `get_project_by_wc_id()`. Code omitted for brevity — identical pattern to previous revision with these corrections applied. All `save_*` methods use proper UPSERT. All repos have `mark_outdated()`.

- [ ] **Step 4: Run, commit**

```bash
pytest tests/unit/test_repositories.py -v
git add src/film_director/persistence/ tests/unit/test_repositories.py
git commit -m "M1.E: persistence — UPSERT, UNIQUE(wc_id) constraints, mark_outdated on all entities"
```

---

### Task 7: Import / Normalization Service (M1.F)

**Files:** Create `services/import_service.py`, `tests/integration/test_import_pipeline.py`

**Interfaces:**
- Consumes: `WindComicAdapter`, repositories, `provenance.py` builders
- Produces: `ImportService` with `import_project()`, `check_for_changes()` (added/modified/deleted), `apply_detected_changes()`
- Does NOT depend on LLM

- [ ] **Step 1: Write import + change detection tests (added/modified/deleted)**

`tests/integration/test_import_pipeline.py`:
```python
import json, sqlite3, pytest
from film_director.adapters.wind_comic import WindComicAdapter
from film_director.persistence.database import Database
from film_director.persistence.repositories import *
from film_director.services.import_service import ImportService

@pytest.fixture
def env(tmp_path, wc_db_path):
    db = Database(str(tmp_path / "our.db")); db.init_schema()
    a = WindComicAdapter(wc_db_path)
    return dict(adapter=a, db=db, wc_db_path=wc_db_path, project=ProjectRepository(db), sequence=SequenceRepository(db), scene=SceneRepository(db), character=CharacterRepository(db))

@pytest.fixture
def svc(env):
    return ImportService(adapter=env["adapter"], project_repo=env["project"], sequence_repo=env["sequence"], scene_repo=env["scene"], character_repo=env["character"])

class TestImport:
    def test_basic_import(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        assert r.scenes_imported == 2 and r.characters_imported == 2
        p = env["project"].get_project(r.project_id)
        assert p.provenance.source_hash and len(p.provenance.source_hash) == 64

    def test_idempotent(self, svc, env, wc_project_id):
        r1 = svc.import_project(wc_project_id)
        r2 = svc.import_project(wc_project_id)
        assert r1.project_id == r2.project_id and len(env["project"].list_projects()) == 1

class TestChangeDetectionModified:
    def test_scene_description(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        assert svc.check_for_changes(r.project_id) == []
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute("UPDATE project_assets SET data=? WHERE id='asset_scene_001'", (json.dumps({"description":"CHANGED","location":"X"}),))
        conn.commit(); conn.close()
        changes = svc.check_for_changes(r.project_id)
        assert any(c.entity_type == "scene" and c.change_type == "modified" for c in changes)

    def test_character_media_url(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute("UPDATE project_assets SET media_urls=? WHERE id='asset_char_001'", (json.dumps(["new.png"]),))
        conn.commit(); conn.close()
        assert any(c.entity_type == "character" and c.change_type == "modified" for c in svc.check_for_changes(r.project_id))

    def test_project_title(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute("UPDATE projects SET title='New' WHERE id=?", (wc_project_id,))
        conn.commit(); conn.close()
        assert any(c.entity_type == "project" and c.change_type == "modified" for c in svc.check_for_changes(r.project_id))

class TestChangeDetectionAdded:
    def test_new_scene_detected(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute("INSERT INTO project_assets (id,project_id,type,name,data,media_urls,persistent_url,shot_number,version,confirmed,stale) VALUES ('new_scene',?,'scene','New Room',?,'[]',NULL,NULL,1,0,0)", (wc_project_id, json.dumps({"description":"new","location":"new"})))
        conn.commit(); conn.close()
        changes = svc.check_for_changes(r.project_id)
        added = [c for c in changes if c.change_type == "added" and c.entity_type == "scene"]
        assert len(added) == 1 and added[0].entity_id is None

    def test_new_character_detected(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute("INSERT INTO project_assets (id,project_id,type,name,data,media_urls,persistent_url,shot_number,version,confirmed,stale) VALUES ('new_char',?,'character','Nurse',?,'[]',NULL,NULL,1,0,0)", (wc_project_id, json.dumps({"description":"nurse","appearance":"scrubs"})))
        conn.commit(); conn.close()
        assert any(c.change_type == "added" and c.entity_type == "character" for c in svc.check_for_changes(r.project_id))

class TestChangeDetectionDeleted:
    def test_deleted_scene_detected(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute("DELETE FROM project_assets WHERE id='asset_scene_001'")
        conn.commit(); conn.close()
        assert any(c.change_type == "deleted" and c.entity_type == "scene" for c in svc.check_for_changes(r.project_id))

class TestApplyChanges:
    def test_marks_outdated(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute("UPDATE project_assets SET data=? WHERE id='asset_scene_001'", (json.dumps({"description":"X","location":"Y"}),))
        conn.commit(); conn.close()
        changes = svc.check_for_changes(r.project_id)
        svc.apply_detected_changes(r.project_id, changes)
        seqs = env["sequence"].get_sequences_by_project(r.project_id)
        scenes = env["scene"].get_scenes_by_sequence(seqs[0].id)
        assert any(s.status == "outdated" for s in scenes)

    def test_added_marks_project_outdated(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute("INSERT INTO project_assets (id,project_id,type,name,data,media_urls,persistent_url,shot_number,version,confirmed,stale) VALUES ('new',?,'scene','X',?,'[]',NULL,NULL,1,0,0)", (wc_project_id, json.dumps({})))
        conn.commit(); conn.close()
        changes = svc.check_for_changes(r.project_id)
        svc.apply_detected_changes(r.project_id, changes)
        assert env["project"].get_project(r.project_id).status == "outdated"

    def test_check_is_side_effect_free(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute("UPDATE project_assets SET data=? WHERE id='asset_scene_001'", (json.dumps({"description":"X","location":"Y"}),))
        conn.commit(); conn.close()
        svc.check_for_changes(r.project_id)
        seqs = env["sequence"].get_sequences_by_project(r.project_id)
        assert all(s.status != "outdated" for s in env["scene"].get_scenes_by_sequence(seqs[0].id))

class TestReimport:
    def test_reimport_clears_outdated(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute("UPDATE project_assets SET data=? WHERE id='asset_scene_001'", (json.dumps({"description":"NEW","location":"NEW"}),))
        conn.commit(); conn.close()
        changes = svc.check_for_changes(r.project_id)
        svc.apply_detected_changes(r.project_id, changes)
        # Now reimport
        svc.import_project(wc_project_id)
        seqs = env["sequence"].get_sequences_by_project(r.project_id)
        scenes = env["scene"].get_scenes_by_sequence(seqs[0].id)
        assert all(s.status == "draft" for s in scenes)

    def test_deleted_upstream_stays_outdated_after_reimport(self, svc, env, wc_project_id):
        r = svc.import_project(wc_project_id)
        conn = sqlite3.connect(env["wc_db_path"])
        conn.execute("DELETE FROM project_assets WHERE id='asset_scene_001'")
        conn.commit(); conn.close()
        changes = svc.check_for_changes(r.project_id)
        svc.apply_detected_changes(r.project_id, changes)
        svc.import_project(wc_project_id)
        seqs = env["sequence"].get_sequences_by_project(r.project_id)
        scenes = env["scene"].get_scenes_by_sequence(seqs[0].id)
        outdated = [s for s in scenes if s.status == "outdated"]
        assert len(outdated) == 1  # deleted scene remains outdated, not physically removed
```

- [ ] **Step 2: Implement ImportService**

`src/film_director/services/import_service.py` — core logic:

- `import_project()`: reads WC, creates/updates canonical entities with provenance, sets status to draft/active. For entities deleted upstream (in our DB but not in WC), does NOT delete them — they remain with whatever status they have.
- `check_for_changes()`: side-effect-free, returns `ChangeDetection` with `change_type` in `{added, modified, deleted}`. `entity_id` is `None` for `added`.
- `apply_detected_changes()`: marks `modified`/`deleted` entities as OUTDATED; for `added`, marks parent project OUTDATED.
- `ChangeDetection.entity_id: str | None`

(Full code follows the patterns shown in tests — omitted from plan for length; implementer builds from test expectations.)

- [ ] **Step 3: Run, commit**

```bash
pytest tests/integration/test_import_pipeline.py -v
git add src/film_director/services/ tests/integration/
git commit -m "M1.F: import service — added/modified/deleted detection, reimport clears outdated"
```

---

### Task 8: LLM Provider (M1.G)

**Files:** Create `llm/provider.py`, `llm/ollama.py`, `tests/unit/test_llm_provider.py`, `tests/integration/test_ollama_live.py`

**Interfaces:**
- Consumes: `config.py`, `errors.py`
- Produces: `LLMResponse`, `parse_llm_json()`, `LLMProvider` protocol, `OllamaProvider`, `create_llm_provider()` (Ollama only, ConfigurationError for others)

(Content identical to previous revision Task 8. `expect_json: bool`, `max_retries` = additional attempts, `create_llm_provider` raises `ConfigurationError` for non-Ollama. Live test REQUIRED.)

- [ ] **Steps 1-5: TDD cycle as in previous revision**
- [ ] **Step 6: Commit**

```bash
git add src/film_director/llm/ tests/unit/test_llm_provider.py tests/integration/test_ollama_live.py
git commit -m "M1.G: LLM provider — Ollama only, ConfigurationError for others, live test required"
```

---

### Task 9: API Wiring + Integration Verification (M1.H)

**Files:** Create `api/routes.py`, modify `main.py` (add wiring), create `tests/integration/test_api.py`, `tests/integration/test_persistence_restart.py`, `tests/integration/test_m1_exit_criteria.py`

**Interfaces:**
- Consumes: ALL prior tasks
- Produces: complete wired application, all exit criteria verified

- [ ] **Step 1: Implement `api/routes.py`**

`create_router(adapter, import_service, project_repo, seq_repo, scene_repo, char_repo, llm_provider) -> APIRouter`

Routes:
- `GET /integrations/wind-comic/health`
- `GET /integrations/llm/health`
- `POST /imports/wind-comic/{wc_project_id}`
- `GET /projects`, `GET /projects/{id}`, `GET /projects/{id}/scenes`, `GET /projects/{id}/characters`
- `GET /projects/{id}/storyboard` — resolves `wc_project_id` from our project, 404 if not found
- `GET /projects/{id}/changes`
- `POST /projects/{id}/apply-changes`

- [ ] **Step 2: Wire in `main.py`**

After `/health` endpoint, add dependency wiring and `app.include_router(create_router(...))`.

- [ ] **Step 3: Write and run API tests, persistence restart tests, exit criteria tests**

(Same test patterns as previous revision, with the storyboard-requires-our-ID test.)

- [ ] **Step 4: Run REQUIRED live Ollama acceptance**

```bash
pytest tests/integration/test_ollama_live.py -v -m live
```

If `test_chat_structured_json` fails → M1 BLOCKED. Report, don't downgrade.

- [ ] **Step 5: Architecture violation scan**

```bash
grep -r "H3Prompt\|MiniMax\|ComfyUIAdapter\|BeatEnrich\|CoveragePlan" src/ --include="*.py" | grep -v "comfyui_base_url"
grep -r "INSERT\|UPDATE\|DELETE" src/film_director/adapters/ --include="*.py"
```

- [ ] **Step 6: Commit**

```bash
git add src/film_director/api/ src/film_director/main.py tests/integration/
git commit -m "M1.H: API wiring and exit criteria verification — all 5 criteria pass"
```

---

## Task Dependency Audit

| Task | All imports exist? | All fixtures exist? | Git state valid? | No forward refs? | No M2/M3? | Verdict |
|------|-------------------|--------------------|-----------------|-----------------|-----------| --------|
| 1. Repo Bootstrap | N/A | N/A | fresh dir | YES | YES | PASS |
| 2. Python Gate | git repo | N/A | 1 commit | YES | YES | PASS |
| 3. Backend Scaffold | venv | N/A | branch created | YES | YES | PASS |
| 4. Canonical Models | `errors.py` ✓ | N/A | YES | YES | YES | PASS |
| 5. WindComicAdapter | `wind_comic_dto.py` ✓, `errors.py` ✓ | creates fixture | YES | YES | YES | PASS |
| 6. Persistence | `canonical.py` ✓, `provenance.py` ✓ | N/A | YES | YES | YES | PASS |
| 7. Import Service | `wind_comic.py` ✓, `repositories.py` ✓, `provenance.py` ✓ | `wind_comic_fixture.py` ✓ | YES | YES | YES | PASS |
| 8. LLM Provider | `config.py` ✓, `errors.py` ✓ | N/A | YES | YES | YES | PASS |
| 9. API + Verification | ALL prior ✓ | ALL prior ✓ | YES | YES | YES | PASS |

**All tasks: PASS. Zero forward dependencies.**

## Architecture Self-Review

- [x] Zero forward task dependencies
- [x] No automatic Python installation — uses `py -0p`, blocks if unavailable
- [x] WC DB read-only at driver level (mode=ro), tested
- [x] Project provenance always REQUIRED (not Optional)
- [x] persistent_url read from WC DTOs, included in provenance hash scope
- [x] Added/modified/deleted upstream assets all detectable
- [x] Deleted imported entities preserved as OUTDATED, never physically deleted
- [x] Reimport clears stale status back to draft; deleted upstream stays outdated
- [x] SQLite UNIQUE constraints: wc_project_id, (sequence_id, wc_scene_id), (project_id, wc_character_id)
- [x] WindComicSchemaError tested (missing table, missing column)
- [x] Ollama live acceptance REQUIRED
- [x] OpenRouter not implemented — ConfigurationError
- [x] No Beat/Coverage (M2)
- [x] No H3/ComfyUI generation (M3)
- [x] No UI (M9)
