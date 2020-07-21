import grpc
import pulsectl
from typing import List, Optional

from . import system_pb2
from . import system_pb2_grpc

from .database import get_database
from .gstreamer import GStreamerClientProcess
from .utils import get_logger

logger = get_logger(__name__)


class Speaker:
    def __init__(self, null_sink_name: str) -> None:
        self._gstreamer = GStreamerClientProcess()
        # Start the GStreamer process immediately as we must not touch
        # pulseaudio before the fork
        self._gstreamer.start()
        self._db = get_database()
        self._null_sink_name = null_sink_name
        self._pulse = None
        self._loopback_index = None
        self._running = False

    def __del__(self):
        self._gstreamer.stop()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def channels(self) -> List[str]:
        return self._speaker_sink.channel_list

    @property
    def mute(self) -> bool:
        return self._speaker_sink.mute

    @mute.setter
    def mute(self, value: bool) -> None:
        self._pulse.mute(self._speaker_sink, value)

    @property
    def volume(self) -> List[float]:
        return self._speaker_sink.volume.values

    @volume.setter
    def volume(self, values) -> None:
        # Allow per-channel volume as well as setting all to the same value
        if isinstance(values, (list, tuple)):
            volume = pulsectl.PulseVolumeInfo(values)
            self._pulse.volume_set(self._speaker_sink, volume)
        else:
            volume = float(values)
            self._pulse.volume_set_all_chans(self._speaker_sink, volume)

    def start(self) -> None:
        '''Start the speaker service.'''
        if self._running:
            return
        if not self._gstreamer.running:
            self._gstreamer.start()

        try:
            self._setup_pulseaudio()
        except:
            self._gstreamer.stop()
            raise
        streams = self._db.hgetall('streams')
        for ident, host in streams.items():
            self.connect_stream(host.decode('utf-8'), ident.decode('utf-8'))

        self._running = True

    def stop(self) -> None:
        '''Stop the speaker service.'''
        if not self._running:
            return
        self._gstreamer.stop()
        self._tear_down_pulseaudio()
        self._running = False

    def connect_stream(
            self,
            host: str,
            old_ident: Optional[str] = None) -> None:
        '''Create a stream from the given RPC host.'''
        # We have the RPC host, but we need the ident and stream location
        try:
            channel = grpc.insecure_channel(host)
            stub = system_pb2_grpc.DeviceStub(channel)
            data = stub.GetInfo(
                system_pb2_grpc.google_dot_protobuf_dot_empty__pb2.Empty())
            ident = data.ident

            stub = system_pb2_grpc.ReaderStub(channel)
            data = stub.GetStatus(
                system_pb2_grpc.google_dot_protobuf_dot_empty__pb2.Empty())
            location = data.stream_location
        except Exception as e:
            logger.debug('Not connecting to unavailable stream of %s', host)
            if old_ident:
                self._db.hdel('streams', old_ident)
            return

        logger.debug('Adding stream from %s at %s', ident, location)
        with self._db.lock('streams-lock'):
            # Cope with the case where the ident of the same host is
            # different for a stored stream host
            if old_ident:
                self._db.hdel('streams', old_ident)

            self._db.hset('streams', ident, host)

        self._gstreamer.add_client(ident, location)

    def disconnect_stream(self, ident: str) -> None:
        '''Disconnect stream from the given device ident.'''
        self._gstreamer.del_client(ident)
        self._db.hdel('streams', ident)

    def disconnect_all_streams(self) -> None:
        '''Disconnect streams from all servers.'''
        self._gstreamer.del_all_clients()
        self._db.delete('streams')

    def _setup_pulseaudio(self) -> None:
        if not self._pulse:
            # Might reuse an old connection
            self._pulse = pulsectl.Pulse()

        # Use the default sink reported by the Pulse server as
        # the speaker output
        info = self._pulse.server_info()
        speaker_sink = info.default_sink_name
        if speaker_sink == self._null_sink_name:
            # Our null sink has become the default for some reason,
            # see if we can pick a different sink for the output.
            #
            # There is no way to know which one is the right one, so
            # just pick the first. If there is no other sink, there is
            # no sound card in the system.
            for sink in self._pulse.sink_list():
                if sink.name != self._null_sink_name:
                    speaker_sink = sink.name
                    self._pulse.sink_default_set(speaker_sink)
                    break
            else:
                raise RuntimeError('Could not find a usable speaker sink')

        logger.debug('Speaker sink: %s', speaker_sink)

        # Create a loopback from the null monitor to the output sink
        null_sink = self._pulse.get_sink_by_name(self._null_sink_name)
        self._loopback_index = self._pulse.module_load(
            'module-loopback',
            f'source={null_sink.monitor_source_name} sink={speaker_sink}')

    def _tear_down_pulseaudio(self) -> None:
        try:
            if self._loopback_index:
                self._pulse.module_unload(self._loopback_index)
        except Exception as e:
            # Be sure to free up pulse even if the above throws to
            # avoid deadlocking
            logger.exception(e)

        self._pulse = None

    @property
    def _speaker_sink(self):
        # This is a property retrieving new sink information each
        # time as it is needed to get current volume and such
        info = self._pulse.server_info()
        return self._pulse.get_sink_by_name(info.default_sink_name)


class SpeakerServicer(system_pb2_grpc.SpeakerServicer):
    '''Implementation of the Speaker RPC service.'''

    _VOLUME_MIN = 0.0
    _VOLUME_MAX = 1.0

    def __init__(self, speaker: Speaker):
        super().__init__()
        self._speaker = speaker
        self._db = get_database()
        assert self._speaker.running

    def GetConnectedStreams(self, request, context):
        streams = self._db.hgetall('streams')
        for ident, host in streams.items():
            result = system_pb2.SpeakerStreamInfo(ident=ident, host=host)
            yield result

    def GetStatus(self, request, context):
        result = system_pb2.SpeakerStatus(mute=self._speaker.mute)
        for value in self._speaker.volume:
            result.volume.append(value)
        for value in self._speaker.channels:
            result.channels.append(value)
        return result

    def SetMute(self, request, context):
        self._speaker.mute = request.value
        result = system_pb2.Result(ok=True)
        return result

    def SetVolume(self, request, context):
        volumes = []
        for value in request.value:
            volumes.append(max(
                self._VOLUME_MIN,
                min(value, self._VOLUME_MAX)))
        self._speaker.volume = volumes
        result = system_pb2.Result(ok=True)
        return result

    def SetVolumeUniform(self, request, context):
        volume = max(self._VOLUME_MIN, min(request.value, self._VOLUME_MAX))
        self._speaker.volume = request.value
        result = system_pb2.Result(ok=True)
        return result

    def ConnectStreams(self, request_iterator, context):
        for item in request_iterator:
            self._speaker.connect_stream(item.host)

        result = system_pb2.Result(ok=True)
        return result

    def DisconnectStreams(self, request_iterator, context):
        for item in request_iterator:
            self._speaker.disconnect_stream(item.ident)

        result = system_pb2.Result(ok=True)
        return result

    def DisconnectAllStreams(self, request, context):
        self._speaker.disconnect_all_streams()
        result = system_pb2.Result(ok=True)
        return result
