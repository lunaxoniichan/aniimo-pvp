import logging
import logging.handlers
from pathlib import Path


def setup_logging(logs_dir: str, level: int = logging.INFO) -> None:
    Path(logs_dir).mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    file = logging.handlers.RotatingFileHandler(
        Path(logs_dir) / "bot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file.setFormatter(fmt)
    root.handlers = [console, file]
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
