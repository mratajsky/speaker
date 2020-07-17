import asyncio
import gi
import multiprocessing
import queue
from functools import partial
from typing import Optional

from .utils import EventEmitter, get_logger

logger = get_logger(__name__)

gi.require_version('Gst', '1.0')
gi.require_version('GstRtspServer', '1.0')
from gi.repository import GLib, Gst, GstRtspServer

Gst.init(None)

_DEFAULT_LATENCY = 200


class Message:
    '''Helper class describing an IPC message.'''

    def __init__(self, t: str, *args):
        self.t = t
        self.args = args


class GStreamerClientProcess:
    '''Client process managing clients connected to our RTSP server.'''
    # Message types
    MSG_T_STOP = 0             # Stop the client process
    MSG_T_ADD_CLIENT = 1       # Add a client stream
    MSG_T_DEL_CLIENT = 2       # Remove a client stream
    MSG_T_DEL_ALL_CLIENTS = 3  # Remove a client stream

    def __init__(self, sink: Optional[str] = None) -> None:
        self._sink = sink
        self._clients = {}
        self._queue = multiprocessing.Queue()
        self._process = multiprocessing.Process(target=self._child_entry)
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def sink(self) -> str:
        return self._sink

    def start(self) -> None:
        '''Start the process.'''
        if self._running:
            return
        logger.debug('Starting gstreamer client process')
        self._process.start()
        self._running = True

    def stop(self) -> None:
        '''Stop the process.'''
        if not self._running:
            return
        logger.debug('Stopping gstreamer client process')
        self._queue.put(Message(self.MSG_T_STOP))
        self._process.join()
        self._running = False

    def add_client(
            self,
            ident: str,
            location: str,
            latency: Optional[int] = None) -> None:
        '''Create a client connecting to the given server ident/location.'''
        self._queue.put(Message(
            self.MSG_T_ADD_CLIENT,
            ident, location, latency))

    def del_client(self, ident: str) -> None:
        '''Remove a client with the given ident.'''
        self._queue.put(Message(
            self.MSG_T_DEL_CLIENT,
            ident))

    def del_all_clients(self) -> None:
        '''Remove all clients.'''
        self._queue.put(Message(self.MSG_T_DEL_ALL_CLIENTS))

    def _child_watch_ipc(self, _=None) -> None:
        try:
            message = self._queue.get_nowait()
            if message.t in (self.MSG_T_STOP, self.MSG_T_DEL_ALL_CLIENTS):
                for client in self._clients.values():
                    client.stop()
                self._clients.clear()
            if message.t == self.MSG_T_STOP:
                self._loop.quit()
            elif message.t == self.MSG_T_ADD_CLIENT:
                self._add_client(*message.args)
            elif message.t == self.MSG_T_DEL_CLIENT:
                self._del_client(*message.args)
        except queue.Empty:
            pass
        # Return True so that this function is run again
        return True

    def _child_entry(self) -> None:
        ctx = GLib.MainContext.new()
        ctx.push_thread_default()
        self._loop = GLib.MainLoop(ctx)
        source = GLib.timeout_source_new(100)
        source.set_callback(self._child_watch_ipc)
        source.attach(ctx)
        try:
            self._loop.run()
        except (KeyboardInterrupt, SystemExit):
            self._loop.quit()

    def _add_client(
            self,
            ident: str,
            location: str,
            latency: int) -> None:
        if ident in self._clients:
            logger.debug('Skipping connected client stream for %s', ident)
            return
        client = GStreamerClient(location, latency)
        client.start(self._sink)
        self._clients[ident] = client

    def _del_client(self, ident: str) -> None:
        client = self._clients.pop(ident, None)
        if client:
            client.stop()
        else:
            logger.debug('Not removing unknown client stream for %s', ident)


class GStreamerServerProcess(EventEmitter):
    '''Server process controlling the RTSP stream provider.'''
    # Message types
    MSG_T_STOP = 0             # Stop the process
    MSG_T_START_STREAM = 1     # Start the stream
    MSG_T_STOP_STREAM = 2      # Stop the stream
    MSG_T_EVENT = 3            # Relay an event

    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        self._host = host
        self._port = port
        self._pipe_p, self._pipe_c = multiprocessing.Pipe()
        self._process = multiprocessing.Process(target=self._child_entry)
        self._running = False
        self._stream_running = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def stream_running(self) -> bool:
        return self._stream_running

    def start(self) -> None:
        '''Start the process.'''
        if self._running:
            return
        logger.debug('Starting gstreamer server process')
        self._process.start()
        self._running = True
        # Check the IPC pipe periodically
        asyncio.create_task(self._parent_watch_ipc())

    def stop(self) -> None:
        '''Stop the process.'''
        if not self._running:
            return
        logger.debug('Stopping gstreamer server process')
        self._pipe_p.send(Message(self.MSG_T_STOP))
        self._process.join()
        self._running = False

    def start_stream(
            self,
            source: str,
            latency: Optional[int] = None) -> None:
        '''Create the RTSP server stream.'''
        self._pipe_p.send(Message(
            self.MSG_T_START_STREAM,
            source, latency))

    def stop_stream(self) -> None:
        '''Stop the RTSP server stream.'''
        self._pipe_p.send(Message(self.MSG_T_STOP_STREAM))

    async def _parent_watch_ipc(self) -> None:
        while self._running:
            if self._pipe_p.poll():
                message = self._pipe_p.recv()
                if message.t == self.MSG_T_EVENT:
                    if message.args[0] == 'started':
                        self._stream_running = True
                    if message.args[0] == 'stopped':
                        self._stream_running = False
                    # Relay event from the server class
                    self._emit(message.args[0], *message.args[1:])
            await asyncio.sleep(0.1)

    def _child_watch_ipc(self, _=None) -> None:
        if self._pipe_c.poll():
            message = self._pipe_c.recv()
            if message.t == self.MSG_T_STOP:
                self._gstreamer.stop()
                self._loop.quit()
            elif message.t == self.MSG_T_START_STREAM:
                self._gstreamer.start(*message.args)
            elif message.t == self.MSG_T_STOP_STREAM:
                self._gstreamer.stop()
        # Return True so that this function is run again
        return True

    def _child_on_gst_event(self, event: str, *args) -> None:
        # Relay event through the process pipe
        self._pipe_c.send(Message(self.MSG_T_EVENT, event, *args))

    def _child_entry(self) -> None:
        ctx = GLib.MainContext.new()
        ctx.push_thread_default()
        self._loop = GLib.MainLoop(ctx)
        self._gstreamer = GStreamerServer(self._host, self._port)
        for event in ('started', 'stopped'):
            self._gstreamer.on(
                event,
                partial(self._child_on_gst_event, event))
        # Check the IPC pipe regularly
        source = GLib.timeout_source_new(100)
        source.set_callback(self._child_watch_ipc)
        source.attach(ctx)
        try:
            self._loop.run()
        except (KeyboardInterrupt, SystemExit):
            self._loop.quit()


class GStreamerClient(EventEmitter):
    '''GStreamer RTSP client.'''

    def __init__(self, url: str, latency: Optional[int] = None) -> None:
        super().__init__()
        self._url = url
        self._pipeline = None
        self._latency = latency or _DEFAULT_LATENCY
        self._restart_source = None

        logger.debug('Using latency %d for %s', self._latency, url)

    @property
    def running(self) -> bool:
        return self._pipeline is not None

    def start(self, sink: Optional[str] = None) -> bool:
        '''Start the stream, return False if pipeline failed to be built.'''
        if not self.running:
            try:
                self._build_stream(sink)
            except RuntimeError as e:
                logger.exception(e)
                return False
            logger.debug('Starting client stream %s', self._url)
            self._start()
        return True

    def stop(self) -> None:
        '''Stop the stream.'''
        if self._restart_source:
            # If we're trying to restart a failed stream, stop doing it
            self._restart_source.destroy()
            self._restart_source = None
        if not self.running:
            return
        logger.debug('Stopping client stream %s', self._url)
        self._stop()
        self._pipeline = None

    def _start(self) -> None:
        self._pipeline.set_state(Gst.State.PLAYING)

    def _stop(self) -> None:
        self._pipeline.set_state(Gst.State.NULL)

    def _build_stream(self, sink: Optional[str] = None) -> None:
        if sink:
            pipeline = '''
                rtspsrc location={} latency={} !
                    rtpopusdepay !
                    opusdec !
                    pulsesink device={}
            '''.format(self._url, self._latency, sink)
        else:
            pipeline = '''
                rtspsrc location={} latency={} !
                    rtpopusdepay !
                    opusdec !
                    pulsesink
            '''.format(self._url, self._latency)
        self._pipeline = Gst.parse_launch(pipeline)

        # Watch the message bus for errors and state changes
        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message::eos', self._on_bus_eos)
        bus.connect('message::error', self._on_bus_error)
        bus.connect('message::state-changed', self._on_bus_state_changed)

    def _schedule_restart(self) -> None:
        def try_restart(_=None) -> bool:
            logger.debug('Attempting to restart the client stream')
            self._start()
            return True

        if not self._restart_source:
            self._restart_source = GLib.timeout_source_new_seconds(2)
            self._restart_source.set_callback(try_restart)
            self._restart_source.attach(self._gst_loop.get_context())

    def _on_bus_eos(self, _, message) -> None:
        logger.info('Client stream %s: end of stream', self._url)
        self._stop()
        self._schedule_restart()

    def _on_bus_error(self, _, message) -> None:
        err, _ = message.parse_error()
        logger.error('Client stream %s error: %s', self._url, err)
        self._stop()
        self._schedule_restart()

    def _on_bus_state_changed(self, _, message) -> None:
        # Only care about state changes of the whole pipeline
        if message.src != self._pipeline:
            return
        prev, curr, _ = message.parse_state_changed()
        prev_name = Gst.Element.state_get_name(prev)
        curr_name = Gst.Element.state_get_name(curr)
        logger.debug('Client pipeline state changed: %s -> %s',
                     prev_name, curr_name)
        if curr_name == 'READY' and self._restart_source:
            self._restart_source.destroy()
            self._restart_source = None


class GStreamerServer(EventEmitter):
    '''GStreamer RTSP server.'''

    def __init__(self, host: str, port: int, mount: str = 'stream') -> None:
        super().__init__()
        self._server = GstRtspServer.RTSPServer()
        self._server.set_address(host)
        self._server.set_service(str(port))
        self._mount = '/' + mount
        self._factory = GstRtspServer.RTSPMediaFactory()
        self._factory.set_shared(True)
        self._server.get_mount_points().add_factory(
            self._mount,
            self._factory)
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def url(self) -> str:
        return 'rtsp://{0}:{1}{2}'.format(self._server.get_address(),
                                          self._server.get_service(),
                                          self._mount)

    def start(self, source: str, latency: Optional[int] = None) -> None:
        '''Attach the RTSP server.'''
        pipeline = '''
            pulsesrc device={} !
                opusenc !
                rtpopuspay name=pay0
        '''.format(source)

        self._factory.props.latency = latency or _DEFAULT_LATENCY
        self._factory.set_launch(pipeline)
        logger.debug('RTSP server pipeline: %s', pipeline)
        try:
            self._server_source = self._server.create_source(None)
            self._server_source.attach(GLib.MainContext.get_thread_default())
        except:
            logger.error('Failed to attach RTSP server')
            return
        self._running = True
        self._emit('started', self.url)

    def stop(self) -> None:
        '''Stop the RTSP server.'''
        if not self._running:
            return
        logger.debug('Stopping server stream')
        self._server_source.destroy()
        self._server_source = None
        self._running = False
        self._emit('stopped')
