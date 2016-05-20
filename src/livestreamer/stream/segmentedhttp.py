from collections import namedtuple

from ..buffers import RingBuffer
from .threadpool_manager import ThreadPoolManager
from .http import HTTPStream
from .segmented import (SegmentedStreamReader,
                        SegmentedStreamWriter,
                        SegmentedStreamWorker, SeekCoordinator)
from ..exceptions import StreamError, MailboxClosed
from .streaming_response import StreamingResponse

ByteRange = namedtuple("ByteRange", "first_byte last_byte")
Segment = namedtuple("Segment", "uri byte_range group_id")


class SegmentedHTTPStreamWorker(SegmentedStreamWorker):
    def __init__(self, reader):
        SegmentedStreamWorker.__init__(self, reader)
        self.segment_size = reader.segment_size
        self.complete_length = self.stream.complete_length
        self.msg_broker = self.stream.msg_broker
        self.mailbox = self.msg_broker.register("worker")
        self.mailbox.subscribe("seek_event")

    def iter_segments(self):
        pos = group_id = 0
        while not self.closed and not self.writer.closed:
            try:
                # Handle seek events
                with self.mailbox.get("seek_event") as seek_event:
                    if seek_event:
                        # Update work group
                        pos = seek_event.data
                        group_id += 1
                        self.logger.debug("Worker received a seek event, "
                                          "new segments start at pos {0}, group id {1}"
                                          .format(pos, group_id))

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
                    self.mailbox.wait_on_msg("seek_event", leave_msg=True)

            except MailboxClosed:
                break

        self.close()

    def close(self):
        if not self.closed:
            SegmentedStreamWorker.close(self)
            self.mailbox.close()


class SegmentedHTTPStreamWriter(SegmentedStreamWriter):
    def __init__(self, reader, **kwargs):
        SegmentedStreamWriter.__init__(self, reader, **kwargs)
        self.segment_size = reader.segment_size
        self.msg_broker = self.stream.msg_broker
        self.mailbox = self.msg_broker.register("writer")
        self.mailbox.subscribe("seek_event")
        self.complete_length = self.stream.complete_length

        threads = kwargs.setdefault("threads", None)
        if not threads:
            threads = self.session.options.get("stream-segment-threads")

        self.executor = ThreadPoolManager(max_workers=threads)

    def fetch(self, segment, retries=None):
        shutdown_event = self.executor.running_group != segment.group_id
        if shutdown_event or self.closed:
            return

        return StreamingResponse(self.session, self.executor, segment.uri,
                                 first_byte=segment.byte_range.first_byte,
                                 last_byte=segment.byte_range.last_byte,
                                 group_id=segment.group_id,
                                 request_params=self.reader.request_params,
                                 retries=retries,
                                 download_timeout=self.timeout,
                                 read_timeout=self.reader.timeout,
                                 ).start()

    def write(self, segment, result, chunk_size=8192):
        try:
            self.logger.debug("Streaming segment {0}-{1}, group id {id} to output buffer",
                              *segment.byte_range,
                              id=segment.group_id)

            for chunk in result.iter_content(chunk_size):
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
                # Close the buffer so that reads don't block and stream
                # terminates once the last byte has been read
                self.reader.buffer.close()
                self.logger.debug("End of stream reached, waiting for seek or close")

                # Idle waiting on seek events until shutdown
                self.mailbox.wait_on_msg("seek_event", leave_msg=True)

            # If got seek: wait for seek coordinator to restart this thread
            with self.mailbox.get("seek_event") as seek_event:
                if seek_event:
                    # Defer to seek coordinator
                    self.mailbox.send("waiting on restart", target="seek_coordinator")
                    self.logger.debug("Writer thread paused, waiting on seek coordinator")
                    self.mailbox.wait_on_msg("restart")
                    self.logger.debug("Writer thread resumed")

        except MailboxClosed:
            self.close()

        except StreamError as err:
            self.logger.error("Unable to recover stream: {0}",
                              err)
            self.close()

    def close(self):
        if not self.closed:
            SegmentedStreamWriter.close(self)
            self.mailbox.close()


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
            self.seek_coordinator = SeekCoordinator(self)
            self.seek_coordinator.start()

    def close(self):
        try:
            self.seek_coordinator.close()
            if self.seek_coordinator.is_alive():
                self.seek_coordinator.join()
        except AttributeError:
            pass
        SegmentedStreamReader.close(self)


class SegmentedHTTPStream(HTTPStream):
    """An HTTP stream using the requests library with multiple segment threads.

    *Attributes:*

    - :attr:`url`  The URL to the stream, prepared by requests.
    - :attr:`args` A :class:`dict` containing keyword arguments passed
      to :meth:`requests.request`, such as headers and cookies.

    """

    __shortname__ = "http"

    def __init__(self, session_, url, **kwargs):
        HTTPStream.__init__(self, session_, url, **kwargs)
        self.logger = self.session.logger.new_module("stream.seg_http")

    def __repr__(self):
        return "<SegmentedHTTPStream({0!r})>".format(self.url)

    def open(self):
        if self.complete_length is None:
            self.complete_length = HTTPStream.get_complete_length(self.session,
                                                                  self.url)
        if self.complete_length:
            self.supports_seek = True

        # Signal that this stream type supports seek
        reader = SegmentedHTTPStreamReader(self)
        reader.open()

        return reader
