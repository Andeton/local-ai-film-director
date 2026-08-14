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
