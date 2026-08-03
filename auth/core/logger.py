import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path('logs')
LOG_DIR.mkdir(exist_ok=True)


def setup_logger():
    logger = logging.getLogger("fastapi-auth")

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s "
        "[%(levelname)s] "
        "%(name)s "
        "%(filename)s:%(lineno)d "
        "- %(message)s"
    )
    file_handler = RotatingFileHandler(
        filename=LOG_DIR / "auth.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    # 控制台输出
    console__handler = logging.StreamHandler()

    console__handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console__handler)
    return logger


logger = setup_logger()
