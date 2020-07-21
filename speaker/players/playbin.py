import gi
import threading
from typing import Optional

from .. import system_pb2
from .. import system_pb2_grpc

from ..database import get_database
from ..utils import EventEmitter, get_logger

logger = get_logger(__name__)

gi.require_version('Gst', '1.0')
from gi.repository import GLib, Gst

Gst.init(None)


class Player(EventEmitter):
    '''Simple player component based on GStreamer playbin.'''

    def __init__(self, sink_name: Optional[str] = None) -> None:
        super().__init__()
        # The sink name is a name of a pulseaudio sink, rather than
        # a GStreamer sink; GStreamer always uses pulsesink
        self._sink_name = sink_name
        self._location = None
        self._running = False
        self._restart_on_eos = True
        self._restart_on_error = True
        self._gst_thread = None
        self._gst_lock = threading.Lock()
        self._meta = {}
        self._meta_title_tmp = {}
        self._meta_lock = threading.Lock()
        self._restart_source = None

    def start_gst_thread(self) -> None:
        '''Start the GStreamer main loop thread.'''
        if self._gst_thread:
            return
        self._gst_thread = threading.Thread(target=self._run_gstreamer)
        self._gst_ready = threading.Event()
        self._gst_thread.start()
        # Wait until the main loop is running; this ensures the instance
        # is initialized correctly and fully usable when this
        # function returns
        self._gst_ready.wait()
        self._gst_ready = None

    def stop_gst_thread(self) -> None:
        '''Stop the GStreamer main loop thread.'''
        if not self._gst_thread:
            return
        self.stop()
        self._gst_loop.quit()
        self._gst_thread.join()
        self._gst_thread = None

    @property
    def location(self) -> str:
        return self._location

    @location.setter
    def location(self, location: str) -> None:
        if location == self._location:
            return
        self._location = location
        if self._running:
            self._stop()
            self._start()
        self._emit('location-changed', location)

    @property
    def sink(self) -> str:
        return self._sink_name

    @sink.setter
    def sink(self, sink_name: Optional[str]) -> None:
        if sink_name == self._sink_name:
            return
        self._sink_name = sink_name
        if self._running:
            self._stop()
            self._start()
        self._emit('sink-changed', sink_name)

    @property
    def running(self) -> bool:
        return self._running

    @property
    def metadata(self) -> dict:
        with self._meta_lock:
            return self._meta.copy()

    @property
    def mute(self) -> bool:
        return self._playbin.get_property('mute')

    @mute.setter
    def mute(self, value: bool) -> None:
        self._playbin.set_property('mute', value)

    @property
    def volume(self) -> float:
        return self._playbin.get_property('volume')

    @volume.setter
    def volume(self, value: float) -> None:
        self._playbin.set_property('volume', value)

    @property
    def restart_on_eos(self) -> bool:
        return self._restart_on_eos

    @restart_on_eos.setter
    def restart_on_eos(self, value: bool) -> None:
        self._restart_on_eos = value

    @property
    def restart_on_error(self) -> bool:
        return self._restart_on_error

    @restart_on_error.setter
    def restart_on_error(self, value: bool) -> None:
        self._restart_on_error = value

    def start(self) -> None:
        '''Start the player, location must already be set.'''
        if self._running:
            return
        if not self._gst_thread:
            raise RuntimeError('GStreamer thread has not been started')
        if not self._location:
            raise RuntimeError('Location not set')

        logger.debug('Starting playbin player for %s', self._location)
        self._start()
        self._emit('started')

    def stop(self) -> None:
        '''Stop the player.'''
        with self._gst_lock:
            if self._restart_source:
                # If we're trying to restart a failed stream, stop doing it
                self._restart_source.destroy()
                self._restart_source = None
        if not self._running:
            return
        logger.debug('Stopping playbin player')
        self._stop()
        self._emit('stopped')

    def _start(self) -> None:
        with self._gst_lock:
            self._output.set_property('device', self._sink_name)
            self._playbin.set_property('uri', self._location)
            self._playbin.set_state(Gst.State.PLAYING)
            self._running = True

    def _stop(self) -> None:
        with self._gst_lock:
            self._playbin.set_state(Gst.State.NULL)
            self._running = False

    def _run_gstreamer(self) -> None:
        # This function is invoked in a separate thread and doesn't
        # return until self._gst_loop is stopped
        ctx = GLib.MainContext.new()
        ctx.push_thread_default()
        self._gst_loop = GLib.MainLoop(ctx)

        self._playbin = Gst.ElementFactory.make('playbin')
        self._fix_playbin_flags()
        # GStreamer output is always pulseaudio
        self._output = Gst.ElementFactory.make('pulsesink')
        self._playbin.set_property('audio-sink', self._output)

        # Connect to the message bus to get event notifications
        bus = self._playbin.get_bus()
        bus.add_signal_watch()
        bus.connect('message::eos', self._on_bus_eos)
        bus.connect('message::error', self._on_bus_error)
        bus.connect('message::state-changed', self._on_bus_state_changed)
        bus.connect('message::tag', self._on_bus_tag)
        self._gst_ready.set()
        try:
            self._gst_loop.run()
        except (KeyboardInterrupt, SystemExit):
            self._gst_loop.quit()

    def _fix_playbin_flags(self) -> None:
        flags = self._playbin.get_property('flags')
        logger.debug('Playbin flags before: %s', hex(flags))
        # Remove flags:
        #  0x01 video
        #  0x04 text
        #  0x08 vis
        flags &= ~(0x1 | 0x4 | 0x8)
        logger.debug('Playbin flags after: %s', hex(flags))
        self._playbin.set_property('flags', flags)

    def _schedule_restart(self) -> None:
        def try_restart(_=None) -> bool:
            logger.debug('Attempting to restart playbin stream')
            self._start()
            return True

        with self._gst_lock:
            if not self._restart_source:
                self._restart_source = GLib.timeout_source_new_seconds(2)
                self._restart_source.set_callback(try_restart)
                self._restart_source.attach(self._gst_loop.get_context())

    def _on_bus_eos(self, _, message) -> None:
        logger.info('Stopping playbin player: end of stream')
        self._stop()
        if self._restart_on_eos:
            self._schedule_restart()
        else:
            self._emit('stopped-eos')

    def _on_bus_error(self, _, message) -> None:
        err, _ = message.parse_error()
        logger.error('Stopping playbin player: %s', err)
        self._stop()
        if self._restart_on_error:
            self._schedule_restart()
        else:
            self._emit('stopped-error', err)

    def _on_bus_state_changed(self, _, message) -> None:
        if message.src != self._playbin:
            return
        prev, curr, _ = message.parse_state_changed()
        prev_name = Gst.Element.state_get_name(prev)
        curr_name = Gst.Element.state_get_name(curr)
        logger.debug('Playbin pipeline state changed: %s -> %s',
                     prev_name, curr_name)
        if curr_name == 'READY':
            with self._gst_lock:
                if self._restart_source:
                    self._restart_source.destroy()
                    self._restart_source = None

        self._emit('state-changed', prev_name, curr_name)

    def _on_bus_tag(self, _, message) -> None:
        # This function is called whenever the stream metadata is updated
        tags = message.parse_tag()
        logger.debug('Updated metadata: %s', tags.to_string())
        changed, update_title = False, False
        with self._meta_lock:
            for tag_idx in range(tags.n_tags()):
                name = tags.nth_tag_name(tag_idx)
                key, valid, value = None, False, None
                is_title = False
                if name == 'audio-codec':
                    key = 'codec'
                    valid, value = tags.get_string(name)
                elif name == 'nominal-bitrate':
                    key = 'bitrate'
                    valid, value = tags.get_uint(name)
                elif name in ('genre', 'location'):
                    key = name
                    valid, value = tags.get_string(name)
                elif name in ('artist', 'title', 'organization'):
                    # These elements are used to build a title string
                    key = name
                    valid, value = tags.get_string(name)
                    is_title = True
                if not valid:
                    continue
                if is_title:
                    if self._meta_title_tmp.get(key) != value:
                        self._meta_title_tmp[key] = value
                        update_title = True
                elif self._meta.get(key) != value:
                    self._meta[key] = value
                    changed = True
            if update_title:
                if self._update_meta_title():
                    changed = True
        if changed:
            self._emit('metadata-updated')

    def _update_meta_title(self) -> bool:
        title = None
        # Build the title depending on which fields are set
        if 'artist' in self._meta_title_tmp:
            title = self._meta_title_tmp['artist']
            if 'title' in self._meta_title_tmp:
                title += ' - ' + self._meta_title_tmp['title']
        elif 'title' in self._meta_title_tmp:
            title = self._meta_title_tmp['title']
        elif 'organization' in self._meta_title_tmp:
            title = self._meta_title_tmp['organization']

        if self._meta.get('title') != title:
            if title:
                self._meta['title'] = title
            else:
                self._meta.pop('title', None)
            return True
        else:
            return False


class PlayerServicer(system_pb2_grpc.PlayerServicer):
    '''Implementation of the Player RPC service.'''
    _VOLUME_MIN = 0.0
    _VOLUME_MAX = 1.0

    def __init__(self, player: Player) -> None:
        super().__init__()
        self._player = player
        self._db = get_database()

    def GetStatus(self, request, context):
        result = system_pb2.PlayerStatus(
            mute=self._player.mute,
            volume=self._player.volume,
            location=self._player.location or '')
        if self._player.running:
            result.status = system_pb2.PlayerStatus.Status.RUNNING
        else:
            result.status = system_pb2.PlayerStatus.Status.STOPPED

        for key, value in self._player.metadata.items():
            # gRPC requires string values for all metadata
            result.metadata[key] = str(value)
        return result

    def Start(self, request, context):
        result = system_pb2.Result()
        try:
            self._player.start()
            result.ok = True
        except Exception as e:
            result.ok = False
            result.error = str(e)
        return result

    def Stop(self, request, context):
        self._player.stop()
        result = system_pb2.Result(ok=True)
        return result

    def SetLocation(self, request, context):
        self._player.location = request.location
        self._db.set('playbin-location', self._player.location)
        result = system_pb2.Result(ok=True)
        return result

    def SetMute(self, request, context):
        self._player.mute = request.value
        result = system_pb2.Result(ok=True)
        return result

    def SetVolume(self, request, context):
        volume = max(self._VOLUME_MIN, min(request.value, self._VOLUME_MAX))
        self._player.volume = request.value
        result = system_pb2.Result(ok=True)
        return result


if __name__ == '__main__':
    # Simple command-line use to allow testing the player
    import sys
    if len(sys.argv) < 2:
        print('Usage: {} URI'.format(sys.argv[0]))
        sys.exit(1)

    from ..utils import setup_logging
    setup_logging('DEBUG')

    player = Player()
    player.start_gst_thread()
    player.location = sys.argv[1]
    from pprint import pprint
    player.on('metadata-updated', lambda: pprint(player.metadata))

    async def _test(player: Player) -> None:
        player.start()
        loop = asyncio.get_event_loop()
        while loop.is_running():
            await asyncio.sleep(1)

    import asyncio
    try:
        asyncio.run(_test(player))
    except:
        player.stop_gst_thread()
