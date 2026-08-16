"""Error taxonomy for Local AI Film Director."""


class FilmDirectorError(Exception):
    def __init__(self, message: str, detail: str | None = None):
        self.message = message
        self.detail = detail
        super().__init__(message)


class WindComicUnavailableError(FilmDirectorError):
    """WC service unreachable, database missing, or authentication failed."""


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


class EnrichmentError(FilmDirectorError):
    """Valid JSON-object but Beat/Coverage domain contract invalid after repair attempt."""


class HumanEditConflictError(FilmDirectorError):
    """Re-enrichment blocked: human-edited content exists and force=False."""


class ComfyUIUnavailableError(FilmDirectorError):
    """ComfyUI runtime unreachable."""


class ComfyUIExecutionError(FilmDirectorError):
    """ComfyUI job execution failed."""


class ComfyUIResultError(FilmDirectorError):
    """ComfyUI result retrieval/download failed."""


class UnsupportedStrategyError(FilmDirectorError):
    """Generation strategy not implemented in current milestone."""


class ReferenceResolutionError(FilmDirectorError):
    """Required R2V reference cannot be resolved/validated."""


class ParameterResolutionError(FilmDirectorError):
    """Generic plan cannot be converted to verified provider parameters."""


class WorkflowTemplateError(FilmDirectorError):
    """Workflow template missing, invalid, or fingerprint mismatch."""


class GenerationError(FilmDirectorError):
    """Internal generation orchestration failure."""


class MediaProcessingError(FilmDirectorError):
    """FFmpeg, media staging, or verification failure."""


class WindComicPreproductionError(FilmDirectorError):
    """Wind Comic pre-production pipeline error, SSE stream failure, or timeout."""


class ReferenceGenerationError(FilmDirectorError):
    """ComfyUI reference image generation failure."""


class ReferenceIngestError(FilmDirectorError):
    """Reference file validation, download, or storage failure."""
