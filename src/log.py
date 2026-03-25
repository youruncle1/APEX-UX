import logging

import colorama
from colorama import Fore, Style

colorama.init(strip=False)

_BANNED = (
    "httpcore",
    "httpx",
    "openai._base_client",
    "openai.http_client",
    "aiohttp.access",
    "PIL",
)


def _filter_noisy(record: logging.LogRecord) -> bool:
    return not any(record.name.startswith(b) for b in _BANNED)


def initialize_logging(loglevel: str | int = logging.INFO) -> None:
    if isinstance(loglevel, str):
        numeric_level = getattr(logging, loglevel.upper(), None)
        if not isinstance(numeric_level, int):
            raise ValueError(f"Invalid log level: {loglevel!r}")
    else:
        numeric_level = loglevel

    console = logging.StreamHandler()
    console.addFilter(_filter_noisy)

    logging.basicConfig(
        level=numeric_level,
        format=(
            f"{Fore.LIGHTBLACK_EX}%(asctime)s{Style.RESET_ALL} "
            f"{Style.BRIGHT}%(levelname)s "
            f"{Fore.LIGHTYELLOW_EX}[%(name)s]{Style.RESET_ALL} "
            f"%(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
        handlers=[console],
    )

    logging.addLevelName(logging.DEBUG, f"{Fore.BLUE}DEBUG{Style.RESET_ALL}")
    logging.addLevelName(logging.INFO, f"{Fore.GREEN}INFO{Style.RESET_ALL}")
    logging.addLevelName(logging.WARNING, f"\033[38;5;208mWARNING{Style.RESET_ALL}")
    logging.addLevelName(logging.ERROR, f"{Fore.RED}ERROR{Style.RESET_ALL}")
    logging.addLevelName(logging.CRITICAL, f"{Fore.MAGENTA}CRITICAL{Style.RESET_ALL}")

    # Redirect uvicorn loggers
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv = logging.getLogger(name)
        uv.handlers.clear()
        uv.propagate = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
