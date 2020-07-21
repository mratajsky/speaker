import asyncio
import logging
import signal
import sys

from .device import Device
from .utils import get_ip_address, get_logger, setup_logging

logger = get_logger(__name__)


async def get_valid_ip_address() -> str:
    first_wait = True
    while True:
        ip = get_ip_address()
        if ip is not None:
            return ip
        await asyncio.sleep(0.1)
        if first_wait:
            logger.info('Waiting for network...')
            first_wait = False


async def main(args) -> None:
    def stop():
        nonlocal running
        running = False

    running = True

    # Find our external IP address.
    #
    # Even if an IP is given as an argument, it is still worth waiting
    # until there's a usable address available to make sure the
    # network works.
    ip_addr = await get_valid_ip_address()
    host = args.host or '0.0.0.0'
    if host != '0.0.0.0':
        # Prefer the IP that is explicitly given
        logger.debug('Overriding known IP %s with user-specified IP %s',
                     ip_addr, host)
        ip_addr = host

    device = Device(host, ip_addr, args)
    try:
        await device.start()
    except Exception as e:
        logger.exception(e)
        return
    logger.info('Speaker running, hit CTRL+C to quit')

    # Run the asyncio loop until a terminating signal is caught
    loop = asyncio.get_running_loop()
    for signame in ('SIGINT', 'SIGTERM'):
        loop.add_signal_handler(getattr(signal, signame), stop)
    while running:
        await asyncio.sleep(0.1)

    await device.stop()


def run(args) -> None:
    setup_logging('DEBUG' if args.debug else 'INFO')
    try:
        asyncio.run(main(args))
    except Exception as e:
        logger.exception(e)
