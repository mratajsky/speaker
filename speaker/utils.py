import colorlog
import logging
import socket
from collections import defaultdict
from typing import Callable, Optional, Union


class EventEmitter:
    def __init__(self) -> None:
        self._event_handlers = defaultdict(list)

    def on(self, event: str, f: Callable) -> None:
        self._event_handlers[event].append(f)

    def off(self, event: str, f: Callable) -> None:
        self._event_handlers[event].remove(f)

    def _emit(self, event: str, *args) -> None:
        for f in self._event_handlers[event]:
            f(*args)


def get_ip_address() -> Optional[str]:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(('10.255.255.255', 1234))
        return sock.getsockname()[0]
    except OSError:
        pass


def get_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(name)


def setup_logging(level: Union[int, str]) -> None:
    '''Initialize the logging module with the given level.'''
    handler = logging.StreamHandler()
    handler.setFormatter(colorlog.ColoredFormatter(
        '%(bold)s%(asctime)s%(reset)s: %(log_color)s%(name)s%(reset)s: %(message)s',
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'red',
        }))
    # Set up the root logger only as it will catch all the messages
    # from module loggers
    logger = logging.getLogger()
    logger.setLevel(level)
    logger.addHandler(handler)
