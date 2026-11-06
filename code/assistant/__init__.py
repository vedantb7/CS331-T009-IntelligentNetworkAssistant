"""Natural language assistant module for INA v2."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from assistant.logger import RequestLogger, get_default_logger, log_request

__all__ = ["RequestLogger", "get_default_logger", "log_request"]


def __getattr__(name: str):
    if name in ("RequestLogger", "get_default_logger", "log_request"):
        from assistant import logger
        return getattr(logger, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
