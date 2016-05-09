from threading import Thread

from livestreamer.buffers import RingBuffer
from livestreamer.stream.threadpool_manager import ThreadPoolManager
from .http import HTTPStream
from .segmented import (SegmentedStreamReader,
                        SegmentedStreamWriter,
                        SegmentedStreamWorker)
from ..exceptions import StreamError, MailboxTimeout
from .streaming_response import Segment, ByteRange, StreamingResponse
from ..compat import queue


class _SeekCoordinator(Thread):
    def __init__(self, reader):
        self.work_queue = reader.writer.futures
        self.logger = reader.logger
        self.mailbox = reader.session.msg_broker.register("seek_coordinator")
        self.mailbox.subscribe("seek_event")
        self.closed = False

        Thread.__init__(self)
        self.daemon = True

    def run(self):
        while not self.closed:
            try:
                seek_event = self.mailbox.get("seek_event", wait=True, timeout=1)
            except MailboxTimeout:
                # TODO: Replace poll with mailbox close event that wakes thread with an exception
                continue  # Used to poll for close event
            else:
                # Wait for the writer to enter a ready state first so it can't
                # block trying to fetch work from an empty work queue
                # TODO: Replace with barrier (needs to be compatible with python 2.7)
                self.mailbox.wait_on_msg("waiting on restart", source="writer")

                # Flush work queue and poll worker for ready state
                queue_empty = False
                worker_ready = False
                while not worker_ready or not queue_empty:
                    if not worker_ready:
                        # TODO: Replace with barrier (needs to be compatible with python 2.7)
                        worker_ready = self.mailbox.get("waiting on restart",
                                                        source="worker")
                    try:
                        work_item = self.work_queue.get(block=False)
                        segment = work_item[0]
                        self.logger.debug("Dropping segment {0}-{1}, "
                                          "group id {id} from the queue"
                                          .format(*segment.byte_range,
                                                  id=segment.group_id))
                        queue_empty = self.work_queue.empty()
                    except queue.Empty:
                        queue_empty = self.work_queue.empty()

                # Queue has been flush so it is safe to restart threads
                # TODO: Replace with barrier wait (needs to be compatible with python 2.7)
                self.mailbox.send("restart", target="worker")
                self.mailbox.send("restart", target="writer")

                # Notify waiting threads that the seek event has been handled
                seek_event.set_handled()

    def close(self):
        self.closed = True
        # TODO: Replace with mailbox close that wakes waiting threads with an exception
        self.mailbox.unsubscribe("seek_event")


class SegmentedHTTPStreamWorker(SegmentedStreamWorker):
    def __init__(self, reader):
        SegmentedStreamWorker.__init__(self, reader)
        self.segment_size = reader.segment_size
        self.complete_length = self.stream.get_complete_length()
        self.msg_broker = self.session.msg_broker
        self.mailbox = self.msg_broker.register("worker")
        self.mailbox.subscribe("seek_event")

    def iter_segments(self):
        pos = group_id = 0
        while not self.closed and not self.writer.closed:
            # Handle seek events
            with self.mailbox.get("seek_event") as seek_event:
                if seek_event:
                    # Update work group
                    pos = seek_event.data
                    group_id += 1
                    self.logger.debug("Worker received a seek event, "
                                      "new segments start at pos {0}, group id {1}"
                                      .format(pos, group_id))

                    # Starts shutdown of prev work group
                    self.writer.executor.set_running_group(group_id, wait_shutdown=False)

                    # Defer to seek coordinator
                    self.mailbox.send("waiting on restart", target="seek_coordinator")
                    self.logger.debug("Worker thread paused, waiting on seek coordinator")
                    self.mailbox.wait_on_msg("restart")
                    self.logger.debug("Worker thread resumed")

            segment = Segment(self.stream.url,
                              ByteRange(pos, pos + self.segment_size - 1),
                              group_id)

            self.logger.debug("Adding segment {0}-{1}, group id {id} to queue",
                              *segment.byte_range,
                              id=segment.group_id)

            yield segment
            pos += self.segment_size

            # End of stream
            stream_end = pos >= self.complete_length
            if stream_end:
                # Idle waiting on seek events until shutdown
                # TODO: Replace poll with mailbox close event that wakes thread with an exception
                idle = True
                while idle and not self.closed and not self.writer.closed:
                    try:
                        self.mailbox.wait_on_msg("seek_event", leave_msg=True,
                                                 timeout=1)
                        idle = False
                    except MailboxTimeout:
                        continue

        self.close()

    def close(self):
        if not self.closed:
            SegmentedStreamWorker.close(self)
            # TODO: Replace with mailbox close that wakes waiting threads with an exception
            self.mailbox.unsubscribe("seek_event")


class SegmentedHTTPStreamWriter(SegmentedStreamWriter):
    def __init__(self, reader, **kwargs):
        SegmentedStreamWriter.__init__(self, reader, **kwargs)
        self.chunk_size = kwargs.setdefault("chunk_size", 8192)
        self.segment_size = reader.segment_size
        self.msg_broker = self.session.msg_broker
        self.mailbox = self.msg_broker.register("writer")
        self.mailbox.subscribe("seek_event")
        self.complete_length = self.stream.get_complete_length()

        threads = kwargs.setdefault("threads", None)
        if not threads:
            threads = self.session.options.get("stream-segment-threads")

        self.executor = ThreadPoolManager(max_workers=threads)

    def fetch(self, segment, retries=5):
        shutdown_event = self.executor.running_group != segment.group_id
        if shutdown_event or self.closed or not retries:
            return

        return StreamingResponse(self.session, self.executor, segment,
                                 self.reader.request_params,
                                 retries=retries,
                                 download_timeout=self.timeout,
                                 read_timeout=self.reader.timeout,
                                 ).start()

    def put(self, segment):
        """Adds a segment to the download pool and write queue."""
        if self.closed:
            return

        if segment is not None:
            future = self.executor.submit(self.fetch, segment,
                                          retries=self.retries,
                                          work_group_id=segment.group_id)
        else:
            future = None

        self.queue(self.futures, (segment, future))

    def seek_event(self):
        with self.mailbox.get("seek_event") as seek_event:
            if seek_event:
                return True
            else:
                return False

    def write(self, segment, result):
        got_seek = self.seek_event()  # Needed in case loop skips due to cancelled downloads
        try:
            if not got_seek:
                self.logger.debug("Streaming segment {0}-{1}, group id {id} to output buffer",
                                  *segment.byte_range,
                                  id=segment.group_id)

                for chunk in result.iter_content(self.chunk_size):
                    # Break on seek events
                    if self.seek_event():
                        got_seek = True
                        break

                    # Write to main buffer
                    self.reader.buffer.write(chunk)

                if result.consumed:
                    self.logger.debug("Streaming of segment {0}-{1}, "
                                      "group id {id} to buffer complete",
                                      *segment.byte_range,
                                      id=segment.group_id)
                else:
                    self.logger.debug("Streaming of segment {0}-{1}, "
                                      "group id {id} to buffer cancelled",
                                      *segment.byte_range,
                                      id=segment.group_id)

                # End of stream
                last_byte = segment.byte_range.last_byte
                if last_byte >= (self.complete_length - 1):
                    self.reader.buffer.close()
                    self.logger.info("End of stream reached")

                    # Idle waiting on seek events until shutdown
                    # TODO: Replace poll with mailbox close event that wakes thread with an exception
                    idle = True
                    while idle and not self.closed:
                        try:
                            self.mailbox.wait_on_msg("seek_event", timeout=1)
                            got_seek = True
                            idle = False
                        except MailboxTimeout:
                            continue

            # Wait for seek coordinator to restart this thread
            if got_seek:
                # Need a new buffer once thread restarts
                buffer_size = self.reader.buffer.buffer_size
                self.reader.buffer.close()
                self.reader.buffer = RingBuffer(buffer_size)

                # Defer to seek coordinator
                self.mailbox.send("waiting on restart", target="seek_coordinator")
                self.logger.debug("Writer thread paused, waiting on seek coordinator")
                self.mailbox.wait_on_msg("restart")
                self.logger.debug("Writer thread resumed")

        except StreamError as err:
            self.logger.error("Unable to recover stream: {0}",
                              err)
            self.close()

    def close(self):
        if not self.closed:
            SegmentedStreamWriter.close(self)
            # TODO: Replace with mailbox close that wakes waiting threads with an exception
            self.mailbox.unsubscribe("seek_event")


class SegmentedHTTPStreamReader(SegmentedStreamReader):
    __worker__ = SegmentedHTTPStreamWorker
    __writer__ = SegmentedHTTPStreamWriter

    def __init__(self, stream, *args, **kwargs):
        SegmentedStreamReader.__init__(self, stream, *args, **kwargs)
        self.logger = stream.logger
        self.request_params = dict(stream.args)
        self.timeout = stream.session.options.get("http-stream-timeout")
        self.segment_size = self.session.options.get("stream-segment-size")

        # These params are reserved for internal use
        self.request_params.pop("exception", None)
        self.request_params.pop("stream", None)
        self.request_params.pop("timeout", None)
        self.request_params.pop("url", None)

    def open(self):
        SegmentedStreamReader.open(self)
        if self.stream.supports_seek:
            self.seek_coordinator = _SeekCoordinator(self)
            self.seek_coordinator.start()

    def close(self):
        if self.stream.supports_seek:
            self.seek_coordinator.close()
            if self.seek_coordinator.is_alive():
                self.seek_coordinator.join()
        SegmentedStreamReader.close(self)


class SegmentedHTTPStream(HTTPStream):
    """An HTTP stream using the requests library with multiple segment threads.

    *Attributes:*

    - :attr:`url`  The URL to the stream, prepared by requests.
    - :attr:`args` A :class:`dict` containing keyword arguments passed
      to :meth:`requests.request`, such as headers and cookies.

    """

    __shortname__ = "http"

    def __init__(self, session_, url, **args):
        HTTPStream.__init__(self, session_, url, **args)
        self.logger = self.session.logger.new_module("stream.seg_http")

    # Make sure we always use the correct HTTP stream type
    def __new__(cls, session, *args, **kwargs):
        if session.options.get("stream-segment-threads") == 1:
            return HTTPStream(session, *args, **kwargs)
        else:
            return HTTPStream.__new__(cls, session, *args, **kwargs)

    def __repr__(self):
        return "<SegmentedHTTPStream({0!r})>".format(self.url)

    def open(self):
        self.complete_length = self.get_complete_length()
        if self.complete_length:
            self.supports_seek = True

        # Signal that this stream type supports seek
        reader = SegmentedHTTPStreamReader(self)
        reader.open()

        return reader
