import asyncio
import re
import shutil
from typing import Optional

from .. import system_pb2
from .. import system_pb2_grpc

from ..database import get_database
from ..utils import EventEmitter, get_logger

logger = get_logger(__name__)


class Spotify(EventEmitter):
    '''Class for starting and managing a spotifyd client.'''

    # Command-line options to pass to spotifyd with a default value for each
    _OPTIONS = {
        'username': '',
        'password': '',
        'bitrate': 160,
        'backend': 'pulseaudio',
        'device-name': 'Speaker',
        'no-daemon': None,
    }
    # Supported bitrates
    _BITRATES = (96, 160, 320)

    def __init__(self) -> None:
        super().__init__()
        self._spotifyd_path = None
        self._spotifyd_opts = self._OPTIONS.copy()
        self._process = None

    @property
    def spotifyd_path(self) -> Optional[str]:
        return self._spotifyd_path

    @spotifyd_path.setter
    def spotifyd_path(self, value: Optional[str]) -> None:
        self._spotifyd_path = value

    @property
    def spotifyd_opt_username(self) -> str:
        return self._spotifyd_opts['username']

    @spotifyd_opt_username.setter
    def spotifyd_opt_username(self, value: str) -> None:
        self._spotifyd_opts['username'] = value

    @property
    def spotifyd_opt_password(self) -> str:
        return self._spotifyd_opts['password']

    @spotifyd_opt_password.setter
    def spotifyd_opt_password(self, value: str) -> None:
        self._spotifyd_opts['password'] = value

    @property
    def spotifyd_opt_bitrate(self) -> int:
        return self._spotifyd_opts['bitrate']

    @spotifyd_opt_bitrate.setter
    def spotifyd_opt_bitrate(self, value: int) -> None:
        if value not in self._BITRATES:
            raise ValueError('Invalid bitrate')
        self._spotifyd_opts['bitrate'] = value

    @property
    def spotifyd_opt_name(self) -> str:
        return self._spotifyd_opts['device_name']

    @spotifyd_opt_name.setter
    def spotifyd_opt_name(self, value: str) -> None:
        self._spotifyd_opts['device_name'] = value

    @property
    def running(self) -> bool:
        return self._process is not None

    async def start(self) -> None:
        '''Start spotifyd.'''
        if self._process:
            return
        logger.debug('Starting spotifyd')
        await self._start()
        self._emit('started')

    async def stop(self) -> None:
        '''Stop spotifyd.'''
        if not self._process:
            return
        logger.debug('Stopping spotifyd')
        try:
            self._process.terminate()
            await self._process.wait()
        except:
            # No worries if the process has already died
            pass
        self._process = None
        self._emit('stopped')

    async def _start(self) -> None:
        program = self._spotifyd_path or shutil.which('spotifyd')
        if not program:
            logger.error('spotifyd binary not in $PATH')
            raise RuntimeError('Could not find the spotifyd binary')
        args = []
        for name, value in self._spotifyd_opts.items():
            args.append('--' + name)
            if value is not None:
                args.append(str(value))
        try:
            self._process = await asyncio.create_subprocess_exec(
                program, *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT)
        except Exception as e:
            logger.exception(e)
            self._process = None
            raise

        asyncio.create_task(self._read_spotifyd_stdout())

    async def _read_spotifyd_stdout(self) -> None:
        while True:
            line = await self._process.stdout.readline()
            if not line:
                break
            line = line.decode('utf-8').strip()
            match = re.match('\S+ \[(.+?)\] (.+)', line)
            if match:
                self._log_spotifyd_output(match.group(1), match.group(2))
            else:
                logger.debug('spotifyd: %s', line)
        self._process = None
        self._emit('stopped')

    @staticmethod
    def _log_spotifyd_output(sp_level: str, sp_msg: str) -> None:
        if sp_level.startswith('WARN'):
            log = logger.warning
        elif sp_level == 'ERROR':
            log = logger.error
        else:
            log = logger.debug

        log('spotifyd/%s: %s', sp_level.lower(), sp_msg)


class SpotifyServicer(system_pb2_grpc.SpotifyServicer):
    '''Implementation of the Spotify RPC service.'''

    def __init__(self, spotify: Spotify, loop) -> None:
        super().__init__()
        self._spotify = spotify
        self._loop = loop
        self._db = get_database()

    def GetCredentials(self, request, context):
        result = system_pb2.SpotifyCredentials(
            username=self._spotify.spotifyd_opt_username,
            password='<hidden>')
        return result

    def GetOptions(self, request, context):
        result = system_pb2.SpotifyOptions(
            name=self._spotify.spotifyd_opt_name)

        if self._spotify.spotifyd_opt_bitrate == 96:
            result.bitrate = system_pb2.SpotifyBitRate.BITRATE_96
        elif self._spotify.spotifyd_opt_bitrate == 160:
            result.bitrate = system_pb2.SpotifyBitRate.BITRATE_160
        else:
            result.bitrate = system_pb2.SpotifyBitRate.BITRATE_320
        return result

    def GetStatus(self, request, context):
        result = system_pb2.SpotifyStatus()
        if self._spotify.running:
            result.status = system_pb2.SpotifyStatus.Status.RUNNING
        else:
            result.status = system_pb2.SpotifyStatus.Status.STOPPED
        return result

    def Start(self, request, context):
        result = system_pb2.Result()
        try:
            # Pass to the main loop as this method is run in the
            # gRPC thread pool
            future = asyncio.run_coroutine_threadsafe(
                self._spotify.start(),
                self._loop)
            start_result = future.result()
            result.ok = True
            self._db.set('spotify-start', 1)
        except Exception as e:
            result.ok = False
            result.error = str(e)
        return result

    def Stop(self, request, context):
        result = system_pb2.Result()
        try:
            # Pass to the main loop as this method is run in the
            # gRPC thread pool
            future = asyncio.run_coroutine_threadsafe(
                self._spotify.stop(),
                self._loop)
            start_result = future.result()
            result.ok = True
            self._db.set('spotify-start', 0)
        except Exception as e:
            result.ok = False
            result.error = str(e)
        return result

    def SetCredentials(self, request, context):
        if not request.username or not request.password:
            result = system_pb2.Result(
                ok=False,
                error='Both username and password are required')
            return result
        self._spotify.spotifyd_opt_username = request.username
        self._spotify.spotifyd_opt_password = request.password
        self._db.set(
            'spotify-username',
            self._spotify.spotifyd_opt_username)
        self._db.set(
            'spotify-password',
            self._spotify.spotifyd_opt_password)

        result = system_pb2.Result(ok=True)
        return result

    def SetBitRate(self, request, context):
        if request.bitrate == system_pb2.SpotifyBitRate.BITRATE_96:
            self._spotify.spotifyd_opt_bitrate = 96
        elif request.bitrate == system_pb2.SpotifyBitRate.BITRATE_160:
            self._spotify.spotifyd_opt_bitrate = 160
        elif request.bitrate == system_pb2.SpotifyBitRate.BITRATE_320:
            self._spotify.spotifyd_opt_bitrate = 320
        else:
            result = system_pb2.Result(
                ok=False,
                error='Invalid bitrate')
            return result

        self._db.set(
            'spotify-bitrate',
            self._spotify.spotifyd_opt_bitrate)

        result = system_pb2.Result(ok=True)
        return result

    def SetName(self, request, context):
        self._spotify.spotifyd_opt_name = request.name
        self._db.set(
            'spotify-name',
            self._spotify.spotifyd_opt_name)

        result = system_pb2.Result(ok=True)
        return result


if __name__ == '__main__':
    # Simple command-line use to allow testing the player
    import sys
    if len(sys.argv) < 3:
        print('Usage: {} USERNAME PASSWORD [ SPOTIFYD_PATH ]'.format(
            sys.argv[0]))
        sys.exit(1)

    from ..utils import setup_logging
    setup_logging('DEBUG')

    player = Spotify()
    player.spotifyd_opt_username = sys.argv[1]
    player.spotifyd_opt_password = sys.argv[2]
    if len(sys.argv) > 3:
        player.spotifyd_path = sys.argv[3]

    async def _test(player: Spotify) -> None:
        await player.start()
        loop = asyncio.get_event_loop()
        while loop.is_running():
            await asyncio.sleep(1)

    try:
        asyncio.run(_test(player))
    except:
        pass
