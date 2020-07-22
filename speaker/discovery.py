import socket
from zeroconf import ServiceInfo, ServiceBrowser, Zeroconf
from typing import Optional

from .utils import EventEmitter, get_logger

logger = get_logger(__name__)

_SERVICE_TYPE = '_speaker1-grpc._tcp.local.'


class DiscoveryClient(EventEmitter):
    '''Zeroconf discovery client.'''

    def __init__(self) -> None:
        super().__init__()
        self._zeroconf = Zeroconf()
        self._listener = self.ServiceListener(self)
        self._browser = None

    def start(self) -> None:
        self._browser = ServiceBrowser(self._zeroconf,
                                       _SERVICE_TYPE,
                                       self._listener)

    def stop(self) -> None:
        if self._browser is not None:
            self._browser.cancel()
            self._browser = None

    class ServiceListener:
        def __init__(self, client: 'DiscoveryClient') -> None:
            self._client = client

        def add_service(
                self,
                zeroconf: Zeroconf,
                service_type: str,
                name: str) -> None:
            info = zeroconf.get_service_info(service_type, name)
            if not info or not info.addresses:
                return
            self._client._emit('service-added', info)

        def remove_service(
                self,
                zeroconf: Zeroconf,
                service_type: str,
                name: str) -> None:
            info = zeroconf.get_service_info(service_type, name)
            if not info or not info.addresses:
                return
            self._client._emit('service-removed', info)

        def update_service(
                self,
                zeroconf: Zeroconf,
                service_type: str,
                name: str) -> None:
            info = zeroconf.get_service_info(service_type, name)
            if not info or not info.addresses:
                return
            self._client._emit('service-updated', info)


class DiscoveryServer:
    '''Zeroconf discovery server.'''

    def __init__(self, host: str, port: int, name: str) -> None:
        self._host = host
        self._port = port
        self._name = name + '.' + _SERVICE_TYPE
        self._zeroconf = None
        self._info = None
        self._registered = False

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def name(self) -> str:
        return self._name

    @property
    def service_info(self) -> Optional[ServiceInfo]:
        return self._info

    def start(self) -> None:
        if not self._registered:
            self._zeroconf = Zeroconf()
            self._start_service()

    def stop(self) -> None:
        if self._registered:
            self._zeroconf.unregister_all_services()
            self._zeroconf = None
            self._registered = False

    def _start_service(self) -> None:
        try:
            host = socket.inet_pton(socket.AF_INET, self._host)
        except Exception as e:
            logger.error('Invalid discovery host %s', self._host)
            logger.exception(e)
            self.stop()
            return
        try:
            self._info = ServiceInfo(_SERVICE_TYPE,
                                     self._name,
                                     self._port,
                                     addresses=[host])
            self._zeroconf.register_service(self._info)
        except Exception as e:
            logger.error('Failed to register discovery server')
            logger.exception(e)
            self.stop()
            return
        logger.debug('Registered zeroconf service %s for host %s:%d',
                     self._name,
                     self._host,
                     self._port)
        self._registered = True
