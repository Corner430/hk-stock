"""
统一日志配置

用法：
    from hkstock.core.logging import setup_logging, get_logger
    setup_logging()
    logger = get_logger(__name__)
    logger.info("message")
"""
import logging
import sys


_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger with consistent format for all modules."""
    global _configured
    if _configured:
        return
    fmt = "[%(asctime)s] %(name)s %(levelname)s: %(message)s"
    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Get a named logger, ensuring logging is configured."""
    setup_logging()
    return logging.getLogger(name)
