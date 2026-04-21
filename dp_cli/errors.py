from __future__ import annotations


class CliError(Exception):
    """Structured application error for JSON responses."""

    def __init__(self, code: str, message: str, details: dict | None = None, exit_code: int = 1) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.exit_code = exit_code


class BrowserConfigError(CliError):
    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__("browser_config_error", message, details, exit_code=2)


class ElementNotFoundError(CliError):
    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__("element_not_found", message, details, exit_code=3)


class InvalidInputError(CliError):
    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__("invalid_input", message, details, exit_code=4)


class RefNotFoundError(CliError):
    def __init__(self, ref: str) -> None:
        super().__init__("ref_not_found", f"Element ref '{ref}' is not known in this session.", {"ref": ref}, exit_code=5)


class RefStaleError(CliError):
    def __init__(self, ref: str, details: dict | None = None) -> None:
        payload = {"ref": ref}
        if details:
            payload.update(details)
        super().__init__(
            "ref_stale",
            f"Element ref '{ref}' is stale for the current runtime or page. Re-run snapshot or find first.",
            payload,
            exit_code=6,
        )
