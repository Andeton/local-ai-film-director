"""Integration tests for PreproductionService (M4.E).

Uses recording fakes for WC client, adapter, ImportService, EnrichmentService.
Tests orchestration flow, error propagation, artifact validation, and boundaries.

No live WC, Ollama, or ComfyUI dependency — purely deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from film_director.adapters.wind_comic import WCProjectBundle
from film_director.adapters.wind_comic_preproduction import WCPreproductionResult
from film_director.errors import (
    EnrichmentError,
    HumanEditConflictError,
    NormalizationError,
    WindComicPreproductionError,
    WindComicUnavailableError,
)
from film_director.models.wind_comic_dto import (
    WCCharacter,
    WCDirectorPlan,
    WCProject,
    WCScene,
    WCScriptShot,
    WCStoryboardShot,
)
from film_director.services.enrichment_service import EnrichmentResult
from film_director.services.import_service import ImportResult
from film_director.services.preproduction_service import (
    PreproductionResult,
    PreproductionService,
)


# ---------------------------------------------------------------------------
# Recording fakes
# ---------------------------------------------------------------------------

class FakeWCClient:
    """Records calls and returns configured result or raises error."""

    def __init__(
        self,
        result: WCPreproductionResult | None = None,
        error: Exception | None = None,
    ):
        self._result = result
        self._error = error
        self.calls: list[tuple] = []

    def create_project(
        self, idea, style=None, aspect=None, language=None,
    ) -> WCPreproductionResult:
        self.calls.append(("create_project", idea, style, aspect, language))
        if self._error:
            raise self._error
        assert self._result is not None
        return self._result


class FakeAdapter:
    """Returns configured bundle or raises error on read_project_bundle."""

    def __init__(
        self,
        bundle: WCProjectBundle | None = None,
        error: Exception | None = None,
    ):
        self._bundle = bundle
        self._error = error
        self.calls: list[tuple] = []

    def read_project_bundle(self, project_id: str) -> WCProjectBundle:
        self.calls.append(("read_project_bundle", project_id))
        if self._error:
            raise self._error
        assert self._bundle is not None
        return self._bundle


class FakeImportService:
    """Records import calls."""

    def __init__(
        self,
        result: ImportResult | None = None,
        error: Exception | None = None,
    ):
        self._result = result
        self._error = error
        self.calls: list[tuple] = []

    def import_project(self, wc_project_id: str) -> ImportResult:
        self.calls.append(("import_project", wc_project_id))
        if self._error:
            raise self._error
        assert self._result is not None
        return self._result


class FakeEnrichmentService:
    """Records enrich calls."""

    def __init__(
        self,
        result: EnrichmentResult | None = None,
        error: Exception | None = None,
    ):
        self._result = result
        self._error = error
        self.calls: list[tuple] = []

    def enrich_project(self, project_id: str) -> EnrichmentResult:
        self.calls.append(("enrich_project", project_id))
        if self._error:
            raise self._error
        assert self._result is not None
        return self._result


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _wc_project(pid="wc-proj-1") -> WCProject:
    return WCProject(
        id=pid, title="Test Film", description="A test film about a hero.",
        status="complete", aspect="16:9",
        style_id=None, script_data={"shots": [
            {"shotNumber": 1, "sceneDescription": "desc", "characters": ["Hero"],
             "dialogue": "Hello", "action": "walks", "emotion": "happy"},
        ]}, locked_characters=[],
    )


def _wc_scene() -> WCScene:
    return WCScene(
        asset_id="scene-1", project_id="wc-proj-1", name="Scene 1",
        data={"location": "Office", "description": "Quiet office"},
        media_urls=[], persistent_url=None, version=1,
    )


def _wc_character() -> WCCharacter:
    return WCCharacter(
        asset_id="char-1", project_id="wc-proj-1", name="Hero",
        data={"description": "Protagonist"}, media_urls=[], persistent_url=None,
        version=1,
    )


def _wc_script_shot() -> WCScriptShot:
    return WCScriptShot(
        shot_number=1, scene_description="desc", characters=["Hero"],
        dialogue="Hello", action="walks", emotion="happy",
    )


def _wc_storyboard() -> WCStoryboardShot:
    return WCStoryboardShot(
        asset_id="sb-1", project_id="wc-proj-1", shot_number=1,
        data={"description": "A wide shot", "duration": 5.0},
        media_urls=["http://img/sb1.png"], persistent_url=None, version=1,
    )


def _valid_bundle(pid="wc-proj-1") -> WCProjectBundle:
    return WCProjectBundle(
        project=_wc_project(pid),
        scenes=[_wc_scene()],
        characters=[_wc_character()],
        script_shots=[_wc_script_shot()],
        storyboard_shots=[_wc_storyboard()],
        director_plan=WCDirectorPlan(genre="drama", style="noir", story_structure={}),
    )


def _import_result(pid="proj-canonical-1") -> ImportResult:
    return ImportResult(project_id=pid, scenes_imported=1, characters_imported=1)


def _enrichment_result(pid="proj-canonical-1") -> EnrichmentResult:
    return EnrichmentResult(project_id=pid, beats_created=1, shots_created=1, plans_created=1)


def _wc_result(pid="wc-proj-1") -> WCPreproductionResult:
    return WCPreproductionResult(wc_project_id=pid)


def _service(
    wc_client=None, adapter=None, import_svc=None, enrich_svc=None,
) -> PreproductionService:
    return PreproductionService(
        wc_client=wc_client or FakeWCClient(result=_wc_result()),
        adapter=adapter or FakeAdapter(bundle=_valid_bundle()),
        import_service=import_svc or FakeImportService(result=_import_result()),
        enrichment_service=enrich_svc or FakeEnrichmentService(result=_enrichment_result()),
    )


# ===========================================================================
# SUCCESS PATH
# ===========================================================================

class TestSuccessPath:
    def test_valid_idea_returns_preproduction_result(self):
        svc = _service()
        result = svc.create_from_idea("A detective investigates a haunted mansion")
        assert isinstance(result, PreproductionResult)

    def test_canonical_project_id_returned(self):
        svc = _service()
        result = svc.create_from_idea("A detective story")
        assert result.project_id == "proj-canonical-1"

    def test_wc_project_id_distinct_from_canonical(self):
        svc = _service()
        result = svc.create_from_idea("A detective story")
        assert result.wc_project_id == "wc-proj-1"
        assert result.project_id != result.wc_project_id

    def test_import_result_preserved(self):
        svc = _service()
        result = svc.create_from_idea("A detective story")
        assert result.import_result.scenes_imported == 1
        assert result.import_result.characters_imported == 1

    def test_enrichment_not_run_during_create(self):
        svc = _service()
        result = svc.create_from_idea("A detective story")
        assert result.enrichment_result is None

    def test_idea_passed_to_wc_client(self):
        client = FakeWCClient(result=_wc_result())
        svc = _service(wc_client=client)
        svc.create_from_idea("My film idea", style="anime", aspect="4:3", language="en")
        assert len(client.calls) == 1
        assert client.calls[0] == ("create_project", "My film idea", "anime", "4:3", "en")

    def test_wc_project_id_passed_to_adapter(self):
        adapter = FakeAdapter(bundle=_valid_bundle())
        svc = _service(adapter=adapter)
        svc.create_from_idea("A story")
        assert adapter.calls[0] == ("read_project_bundle", "wc-proj-1")

    def test_wc_project_id_passed_to_import(self):
        import_svc = FakeImportService(result=_import_result())
        svc = _service(import_svc=import_svc)
        svc.create_from_idea("A story")
        assert import_svc.calls[0] == ("import_project", "wc-proj-1")

    def test_enrichment_not_called_during_create(self):
        """Enrichment is now an explicit operator action, not automatic."""
        enrich_svc = FakeEnrichmentService(result=_enrichment_result())
        svc = _service(enrich_svc=enrich_svc)
        svc.create_from_idea("A story")
        assert len(enrich_svc.calls) == 0


# ===========================================================================
# CALL ORDER
# ===========================================================================

class TestCallOrder:
    def test_wc_create_before_read(self):
        """WC create must complete before reading persisted bundle."""
        order: list[str] = []

        class OrderClient(FakeWCClient):
            def create_project(self, *a, **kw):
                order.append("create")
                return super().create_project(*a, **kw)

        class OrderAdapter(FakeAdapter):
            def read_project_bundle(self, *a, **kw):
                order.append("read")
                return super().read_project_bundle(*a, **kw)

        svc = _service(
            wc_client=OrderClient(result=_wc_result()),
            adapter=OrderAdapter(bundle=_valid_bundle()),
        )
        svc.create_from_idea("A story")
        assert order.index("create") < order.index("read")

    def test_read_before_import(self):
        """Persisted validation before import."""
        order: list[str] = []

        class OrderAdapter(FakeAdapter):
            def read_project_bundle(self, *a, **kw):
                order.append("read")
                return super().read_project_bundle(*a, **kw)

        class OrderImport(FakeImportService):
            def import_project(self, *a, **kw):
                order.append("import")
                return super().import_project(*a, **kw)

        svc = _service(
            adapter=OrderAdapter(bundle=_valid_bundle()),
            import_svc=OrderImport(result=_import_result()),
        )
        svc.create_from_idea("A story")
        assert order.index("read") < order.index("import")

    def test_import_completes_without_enrich(self):
        """Import completes without enrichment (enrichment is now explicit)."""
        order: list[str] = []

        class OrderImport(FakeImportService):
            def import_project(self, *a, **kw):
                order.append("import")
                return super().import_project(*a, **kw)

        class OrderEnrich(FakeEnrichmentService):
            def enrich_project(self, *a, **kw):
                order.append("enrich")
                return super().enrich_project(*a, **kw)

        svc = _service(
            import_svc=OrderImport(result=_import_result()),
            enrich_svc=OrderEnrich(result=_enrichment_result()),
        )
        svc.create_from_idea("A story")
        assert "import" in order
        assert "enrich" not in order  # enrichment no longer automatic


# ===========================================================================
# INPUT VALIDATION
# ===========================================================================

class TestInputValidation:
    def test_blank_idea_rejected(self):
        svc = _service()
        with pytest.raises((NormalizationError, ValueError)):
            svc.create_from_idea("")

    def test_whitespace_idea_rejected(self):
        svc = _service()
        with pytest.raises((NormalizationError, ValueError)):
            svc.create_from_idea("   \n\t  ")

    def test_none_style_accepted(self):
        """Optional params can be None."""
        svc = _service()
        result = svc.create_from_idea("A story", style=None, aspect=None, language=None)
        assert result.project_id == "proj-canonical-1"


# ===========================================================================
# ARTIFACT VALIDATION (post-WC-complete)
# ===========================================================================

class TestArtifactValidation:
    def _bundle_without(self, **overrides) -> WCProjectBundle:
        base = dict(
            project=_wc_project(),
            scenes=[_wc_scene()],
            characters=[_wc_character()],
            script_shots=[_wc_script_shot()],
            storyboard_shots=[_wc_storyboard()],
            director_plan=None,
        )
        base.update(overrides)
        return WCProjectBundle(**base)

    def test_missing_scenes_raises(self):
        bundle = self._bundle_without(scenes=[])
        svc = _service(adapter=FakeAdapter(bundle=bundle))
        with pytest.raises(NormalizationError, match="(?i)scene"):
            svc.create_from_idea("A story")

    def test_missing_characters_raises(self):
        bundle = self._bundle_without(characters=[])
        svc = _service(adapter=FakeAdapter(bundle=bundle))
        with pytest.raises(NormalizationError, match="(?i)character"):
            svc.create_from_idea("A story")

    def test_missing_script_shots_raises(self):
        bundle = self._bundle_without(script_shots=[])
        svc = _service(adapter=FakeAdapter(bundle=bundle))
        with pytest.raises(NormalizationError, match="(?i)script"):
            svc.create_from_idea("A story")

    def test_missing_storyboard_raises(self):
        bundle = self._bundle_without(storyboard_shots=[])
        svc = _service(adapter=FakeAdapter(bundle=bundle))
        with pytest.raises(NormalizationError, match="(?i)storyboard"):
            svc.create_from_idea("A story")

    def test_error_includes_wc_project_id(self):
        bundle = self._bundle_without(scenes=[])
        svc = _service(adapter=FakeAdapter(bundle=bundle))
        with pytest.raises(NormalizationError) as exc_info:
            svc.create_from_idea("A story")
        assert "wc-proj-1" in str(exc_info.value.message) or "wc-proj-1" in str(exc_info.value.detail)

    def test_import_not_called_when_validation_fails(self):
        bundle = self._bundle_without(scenes=[])
        import_svc = FakeImportService(result=_import_result())
        svc = _service(adapter=FakeAdapter(bundle=bundle), import_svc=import_svc)
        with pytest.raises(NormalizationError):
            svc.create_from_idea("A story")
        assert len(import_svc.calls) == 0


# ===========================================================================
# WC FAILURES
# ===========================================================================

class TestWCFailures:
    def test_wc_unavailable_propagated(self):
        client = FakeWCClient(error=WindComicUnavailableError("WC down"))
        svc = _service(wc_client=client)
        with pytest.raises(WindComicUnavailableError):
            svc.create_from_idea("A story")

    def test_wc_sse_error_propagated(self):
        client = FakeWCClient(error=WindComicPreproductionError("SSE error"))
        svc = _service(wc_client=client)
        with pytest.raises(WindComicPreproductionError):
            svc.create_from_idea("A story")

    def test_wc_timeout_propagated(self):
        client = FakeWCClient(
            error=WindComicPreproductionError("SSE stream ended without terminal"),
        )
        svc = _service(wc_client=client)
        with pytest.raises(WindComicPreproductionError):
            svc.create_from_idea("A story")

    def test_adapter_read_failure_after_complete(self):
        """WC completes but reading persisted data fails."""
        adapter = FakeAdapter(error=NormalizationError("DB read error"))
        svc = _service(adapter=adapter)
        with pytest.raises(NormalizationError):
            svc.create_from_idea("A story")

    def test_no_import_on_wc_failure(self):
        client = FakeWCClient(error=WindComicUnavailableError("down"))
        import_svc = FakeImportService(result=_import_result())
        svc = _service(wc_client=client, import_svc=import_svc)
        with pytest.raises(WindComicUnavailableError):
            svc.create_from_idea("A story")
        assert len(import_svc.calls) == 0


# ===========================================================================
# IMPORT / ENRICHMENT FAILURES
# ===========================================================================

class TestImportEnrichFailures:
    def test_import_failure_propagated(self):
        import_svc = FakeImportService(error=NormalizationError("Import failed"))
        svc = _service(import_svc=import_svc)
        with pytest.raises(NormalizationError, match="Import failed"):
            svc.create_from_idea("A story")

    def test_enrichment_not_triggered_during_create(self):
        """Enrichment errors cannot propagate because enrichment is not called."""
        enrich_svc = FakeEnrichmentService(error=EnrichmentError("LLM failed"))
        svc = _service(enrich_svc=enrich_svc)
        # Should succeed — enrichment is not called
        result = svc.create_from_idea("A story")
        assert result.project_id == "proj-canonical-1"

    def test_human_edit_conflict_not_triggered_during_create(self):
        """Human edit conflicts cannot occur during create (no enrichment)."""
        enrich_svc = FakeEnrichmentService(
            error=HumanEditConflictError("Human edits exist"),
        )
        svc = _service(enrich_svc=enrich_svc)
        result = svc.create_from_idea("A story")
        assert result.project_id == "proj-canonical-1"

    def test_enrich_not_called_during_create(self):
        """Enrichment is now explicit — create_from_idea must not call it."""
        calls_with_args: list[dict] = []

        class SpyEnrich(FakeEnrichmentService):
            def enrich_project(self, project_id, **kwargs):
                calls_with_args.append(kwargs)
                return super().enrich_project(project_id)

        svc = _service(enrich_svc=SpyEnrich(result=_enrichment_result()))
        svc.create_from_idea("A story")
        assert len(calls_with_args) == 0

    def test_no_enrich_when_import_fails(self):
        enrich_svc = FakeEnrichmentService(result=_enrichment_result())
        import_svc = FakeImportService(error=NormalizationError("fail"))
        svc = _service(import_svc=import_svc, enrich_svc=enrich_svc)
        with pytest.raises(NormalizationError):
            svc.create_from_idea("A story")
        assert len(enrich_svc.calls) == 0


# ===========================================================================
# BOUNDARIES
# ===========================================================================

class TestBoundaries:
    def test_sse_payloads_not_used_as_canonical_source(self):
        """WC client returns only wc_project_id. Adapter reads persisted data.
        No SSE payload data reaches import/enrich."""
        # The WCPreproductionResult only has wc_project_id.
        # Adapter.read_project_bundle is used for canonical data.
        adapter = FakeAdapter(bundle=_valid_bundle())
        svc = _service(adapter=adapter)
        svc.create_from_idea("A story")
        # Adapter was called with the WC project ID (from SSE), proving
        # persisted data is the import source
        assert adapter.calls[0] == ("read_project_bundle", "wc-proj-1")

    def test_no_wc_db_write(self):
        """Service never writes to WC. Only reads via adapter."""
        # The adapter is read-only by construction (mode=ro).
        # This test verifies only read_project_bundle is called.
        adapter = FakeAdapter(bundle=_valid_bundle())
        svc = _service(adapter=adapter)
        svc.create_from_idea("A story")
        for call in adapter.calls:
            assert call[0] == "read_project_bundle"


# ===========================================================================
# REPEAT BEHAVIOR
# ===========================================================================

class TestRepeatBehavior:
    def test_two_calls_invoke_wc_twice(self):
        """Repeated create_from_idea calls each trigger a new WC project."""
        client = FakeWCClient(result=_wc_result())
        svc = _service(wc_client=client)
        svc.create_from_idea("Idea one")
        svc.create_from_idea("Idea two")
        assert len(client.calls) == 2
        assert client.calls[0][1] == "Idea one"
        assert client.calls[1][1] == "Idea two"

    def test_no_dedupe_by_idea_text(self):
        """Same idea text creates separate WC calls."""
        client = FakeWCClient(result=_wc_result())
        svc = _service(wc_client=client)
        svc.create_from_idea("Same idea")
        svc.create_from_idea("Same idea")
        assert len(client.calls) == 2
