import logging
import os
from datetime import datetime
from config import Config

os.makedirs(Config.LOGS_DIR, exist_ok=True)

_log_file = os.path.join(
    Config.LOGS_DIR, f"pipeline_{datetime.now().strftime('%Y-%m-%d')}.log"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(_log_file),
        logging.StreamHandler(),
    ],
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
