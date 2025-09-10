"""TMux-related exception classes."""


class TMuxError(Exception):
    """Base exception for all TMux-related errors."""
    pass


class TMuxNotRunningError(TMuxError):
    """Raised when TMux is not running or not available."""
    pass


class SessionNotFoundError(TMuxError):
    """Raised when a requested TMux session cannot be found."""
    pass


class SessionCreationError(TMuxError):
    """Raised when a TMux session cannot be created."""
    pass


class CommandExecutionError(TMuxError):
    """Raised when a TMux command fails to execute properly."""
    pass