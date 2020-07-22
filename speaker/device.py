import asyncio
import grpc
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from . import system_pb2
from . import system_pb2_grpc

from .database import get_database
from .discovery import DiscoveryServer
from .reader import Reader, ReaderServicer
from .speaker import Speaker, SpeakerServicer
from .players.playbin import Player, PlayerServicer
from .players.spotify import Spotify, SpotifyServicer
from .utils import get_logger

logger = get_logger(__name__)


class Device:
    _DEFAULT_GRPC_PORT = 24542
    _DEFAULT_RTSP_PORT = 8554

    def __init__(self, host: str, ip_addr: str, args) -> None:
        self._host = host
        self._ip_addr = ip_addr
        self._grpc_server = None
        self._grpc_port = args.grpc_port or self._DEFAULT_GRPC_PORT
        self._rtsp_port = args.rtsp_port or self._DEFAULT_RTSP_PORT

        self._db = get_database()
        # Pass the actual IP address to the reader as it will be included
        # in the stream location sent to other devices
        self._reader = Reader(self._ip_addr, self._rtsp_port)
        self._init_player()
        self._init_spotify(args.spotifyd_path)

        self._speaker = Speaker(self._reader.null_sink_name)
        self._discovery = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        '''Start all the device services.'''
        if self._running:
            return
        # Reader must be started first to initialize its null sink
        self._reader.start()
        try:
            # Speaker initialization might fail if there is nowhere
            # to output the audio
            self._speaker.start()
            if self._spotify_start:
                try:
                    await self._spotify.start()
                except Exception as e:
                    logger.exception(e)
                    self._db.set('spotify-start', 0)
                    logger.warning('Automatic spotify startup disabled')
        except Exception:
            self._reader.stop()
            raise
        self._player.start_gst_thread()
        self._start_server()
        self._running = True

    async def stop(self) -> None:
        '''Stop the device services.'''
        if not self._running:
            return
        self._stop_server()
        self._player.stop_gst_thread()
        self._speaker.stop()
        self._reader.stop()
        self._player.stop()
        self._player.stop_gst_thread()
        await self._spotify.stop()
        self._running = False

    def _init_player(self) -> None:
        self._player = Player()
        # Load the player settings
        self._player.location = self._db.get_str('playbin-location')

    def _init_spotify(self, spotifyd_path: Optional[str]) -> None:
        self._spotify = Spotify()
        if spotifyd_path:
            self._spotify.spotifyd_path = spotifyd_path

        # Load the spotify settings
        try:
            value = self._db.get_int('spotify-bitrate')
            self._spotify.spotifyd_opt_bitrate = value
        except:
            pass
        self._spotify.spotifyd_opt_username = self._db.get_str(
            'spotify-username')
        self._spotify.spotifyd_opt_password = self._db.get_str(
            'spotify-password')
        self._spotify.spotifyd_opt_name = self._db.get_str(
            'spotify-name')
        self._spotify_start = self._db.get_bool(
            'spotify-start')

    def _start_server(self) -> None:
        # Register all services and start the gRPC server
        self._grpc_server = grpc.server(
            ThreadPoolExecutor(max_workers=10))
        system_pb2_grpc.add_DeviceServicer_to_server(
            DeviceServicer(),
            self._grpc_server)
        system_pb2_grpc.add_ReaderServicer_to_server(
            ReaderServicer(self._reader),
            self._grpc_server)
        system_pb2_grpc.add_SpeakerServicer_to_server(
            SpeakerServicer(self._speaker),
            self._grpc_server)
        system_pb2_grpc.add_PlayerServicer_to_server(
            PlayerServicer(self._player),
            self._grpc_server)
        system_pb2_grpc.add_SpotifyServicer_to_server(
            SpotifyServicer(self._spotify, asyncio.get_event_loop()),
            self._grpc_server)

        host = '{}:{}'.format(self._host, self._grpc_port)

        logger.debug('Starting gRPC service on %s', host)
        self._grpc_server.add_insecure_port(host)
        self._grpc_server.start()

        # Start zeroconf
        self._discovery = DiscoveryServer(
            self._ip_addr, self._grpc_port,
            self._db.get_str('ident'))
        self._discovery.start()

    def _stop_server(self) -> None:
        if self._discovery:
            self._discovery.stop()
            self._discovery = None

        if self._grpc_server:
            self._grpc_server.stop(None)
            self._grpc_server = None


class DeviceServicer(system_pb2_grpc.DeviceServicer):
    '''Implementation of the Device RPC service.'''

    def __init__(self) -> None:
        super().__init__()
        self._db = get_database()

    def GetInfo(self, request, context):
        info = system_pb2.DeviceInfo(
            ident=self._db.get_str('ident'),
            name=self._db.get_str('name'))
        return info

    def SetName(self, request, context):
        self._db.set('name', request.name)
        result = system_pb2.Result(ok=True)
        return result
