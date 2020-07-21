import pulsectl
import re

from . import system_pb2
from . import system_pb2_grpc

from .gstreamer import GStreamerServerProcess
from .utils import get_logger

logger = get_logger(__name__)


class Reader:
    _NULL_SINK_NAME = 'speaker-null'

    def __init__(self, host: str, port: int) -> None:
        self._gstreamer = GStreamerServerProcess(host, port)
        self._gstreamer.on('started', self._on_rtsp_started)
        # Start the GStreamer process immediately as we must not touch
        # pulseaudio before the fork
        self._gstreamer.start()
        self._pulse = None
        self._pulse_null_mod = None
        self._pulse_loopback_mods = []
        self._stream_location = None
        self._running = False

    def __del__(self):
        self._gstreamer.stop()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def null_sink_name(self) -> str:
        return self._NULL_SINK_NAME

    @property
    def null_mute(self) -> bool:
        return self._null_sink.mute

    @null_mute.setter
    def null_mute(self, value: bool) -> None:
        self._pulse.mute(self._null_sink, value)

    @property
    def null_volume(self) -> float:
        return self._pulse.volume_get_all_chans(self._null_sink)

    @null_volume.setter
    def null_volume(self, value: float) -> None:
        self._pulse.volume_set_all_chans(self._null_sink, value)

    @property
    def sources(self) -> list:
        names = []
        for _, name in self._pulse_loopback_mods:
            names.append(self._pulse.get_source_by_name(name))
        return names

    @property
    def stream_location(self) -> str:
        return self._stream_location

    @property
    def stream_running(self) -> str:
        return self._gstreamer.stream_running

    def start(self) -> None:
        '''Start the reader service.'''
        if self._running:
            return
        if not self._gstreamer.running:
            self._gstreamer.start()

        try:
            self._setup_pulseaudio()
        except:
            self._gstreamer.stop()
            raise
        self._gstreamer.start_stream(self._null_monitor_name)
        self._running = True

    def stop(self) -> None:
        '''Stop the reader service.'''
        if not self._running:
            return
        self._gstreamer.stop_stream()
        self._gstreamer.stop()
        self._tear_down_pulseaudio()
        self._running = False

    @property
    def _null_sink(self):
        return self._pulse.sink_info(self._pulse_null_idx)

    @property
    def _null_monitor_name(self) -> str:
        return self._null_sink.monitor_source_name

    def _setup_pulseaudio(self) -> None:
        if not self._pulse:
            # Might reuse an old connection
            self._pulse = pulsectl.Pulse()

        self._setup_pulseaudio_null(self._NULL_SINK_NAME)

        null_sink = self._pulse.get_sink_by_name(self._NULL_SINK_NAME)
        self._pulse_null_idx = null_sink.index

    def _setup_pulseaudio_null(self, name: str) -> None:
        def get_mod_argument_by_name(args, name: str) -> str:
            for arg in args.split():
                if arg.startswith(f'{name}='):
                    return arg.split('=', 1)[1]

        self._pulse_loopback_mods = []

        modules = self._pulse.module_list()
        for mod in modules:
            if mod.name == 'module-null-sink':
                sink_name = get_mod_argument_by_name(
                    mod.argument, 'sink_name')
                if sink_name == name:
                    self._pulse_null_mod = mod.index
                    break
        else:
            # Not yet loaded
            self._pulse_null_mod = self._pulse.module_load(
                'module-null-sink',
                f'sink_name={name}')

        logger.debug(f'Reader sink: {name}')

        # Create a list a current input->output loopbacks
        current_loopback_sources = []
        for mod in modules:
            if mod.name == 'module-loopback':
                sink = re.search(rf'sink=(\S+)', mod.argument).group(1)
                if sink != name:
                    continue
                source = re.search(rf'source=(\S+)', mod.argument).group(1)
                try:
                    source = self._pulse.get_source_by_name(source)
                    if source.name in current_loopback_sources:
                        # One loopback is sufficient
                        self._pulse.module_unload(mod.index)
                        continue
                    current_loopback_sources.append(source.name)
                    self._pulse_loopback_mods.append((mod.index, source.name))
                except pulsectl.PulseIndexError:
                    # Non-existent source apparently
                    self._pulse.module_unload(mod.index)

        # Create extra loopbacks to make sure there is one for each input
        for source in self._pulse.source_list():
            if source.name.endswith('.monitor'):
                # Skip output monitors
                continue
            if source.name in current_loopback_sources:
                # Already loaded
                continue
            index = self._pulse.module_load(
                'module-loopback',
                f'source={source.name} sink={name}')
            self._pulse_loopback_mods.append((index, source.name))

    def _tear_down_pulseaudio(self) -> None:
        try:
            if self._pulse_null_mod:
                self._pulse.module_unload(self._pulse_null_mod)
            for index, _ in self._pulse_loopback_mods:
                self._pulse.module_unload(index)
        except Exception as e:
            # Be sure to free up pulse even if the above throws to
            # avoid deadlocking
            logger.exception(e)

        self._pulse = None

    def _on_rtsp_started(self, url: str) -> None:
        logger.debug('RTSP stream attached at %s', url)
        self._stream_location = url


class ReaderServicer(system_pb2_grpc.ReaderServicer):
    '''Implementation of the Reader RPC service.'''

    _VOLUME_MIN = 0.0
    _VOLUME_MAX = 1.0

    def __init__(self, reader: Reader) -> None:
        super().__init__()
        self._reader = reader
        assert self._reader.running

    def GetInputList(self, request, context):
        for source in self._reader.sources:
            result = system_pb2.InputInfo(
                name=source.name,
                description=source.description,
                mute=bool(source.mute))
            for value in source.channel_list:
                result.channels.append(value)
            for value in source.volume.values:
                result.volume.append(value)
            if source.state == pulsectl.PulseStateEnum['idle']:
                result.status = system_pb2.InputInfo.Status.IDLE
            elif source.state == pulsectl.PulseStateEnum['invalid']:
                result.status = system_pb2.InputInfo.Status.INVALID
            elif source.state == pulsectl.PulseStateEnum['running']:
                result.status = system_pb2.InputInfo.Status.RUNNING
            elif source.state == pulsectl.PulseStateEnum['suspended']:
                result.status = system_pb2.InputInfo.Status.SUSPENDED
            yield result

    def GetStatus(self, request, context):
        result = system_pb2.ReaderStatus(
            mute=self._reader.null_mute,
            volume=self._reader.null_volume,
            stream_location=self._reader.stream_location or '')
        if self._reader.stream_running:
            result.stream_status = system_pb2.ReaderStatus.StreamStatus.RUNNING
        else:
            result.stream_status = system_pb2.ReaderStatus.StreamStatus.STOPPED
        return result

    def SetMute(self, request, context):
        self._reader.null_mute = request.value
        result = system_pb2.Result(ok=True)
        return result

    def SetVolume(self, request, context):
        volume = max(self._VOLUME_MIN, min(request.value, self._VOLUME_MAX))
        self._reader.null_volume = volume
        result = system_pb2.Result(ok=True)
        return result
