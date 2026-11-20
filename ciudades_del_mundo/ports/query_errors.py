"""Data lookup errors independent of the HTTP transport."""


class DataNotFoundError(LookupError):
    """The requested SQL entity or hierarchy node does not exist."""
