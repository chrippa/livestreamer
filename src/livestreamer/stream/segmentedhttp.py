from collections import namedtuple
from functools import partial
from threading import Event

from livestreamer.buffers import RingBuffer
from .http import HTTPStream
from .segmented import (SegmentedStreamReader,
                        SegmentedStreamWriter,
                        SegmentedStreamWorker)
from ..exceptions import StreamError
from requests.exceptions import RequestException

ByteRange = namedtuple("ByteRange", "first_byte last_byte")
Segment = namedtuple("Segment", "uri byte_range")


class SegmentedHTTPStreamWorker(SegmentedStreamWorker):
    def __init__(self, reader, seek_pos):
        SegmentedStreamWorker.__init__(self, reader)
        self.segment_size = reader.segment_size
        self.initial_seek_pos = seek_pos
        self.complete_length = self.stream.get_complete_length()

    def iter_segments(self):
        # Get initial position to stream from
        pos = self.initial_seek_pos
        if pos > 0:
            self.logger.debug("Received seek. Seek to pos: {0}", pos)

        while not self.closed and not self.writer.closed:
            segment = Segment(self.stream.url,
                              ByteRange(pos, pos + self.segment_size - 1))
            self.logger.debug("Adding segment {0}-{1} to queue",
                              *segment.byte_range)
            yield segment
            pos += self.segment_size

            # End of stream
            stream_end = pos >= self.complete_length
            if self.closed or stream_end:
                return


class StreamingResponse:
    def __init__(self, session, executor, segment, request_params,
                 chunk_size=8192, download_timeout=None, read_timeout=None,
                 retries=5):
        self.closed = False
        self.session = session
        self.executor = executor  # Not owned by the streaming response object
        self.segment = segment
        self.request_params = request_params
        self.chunk_size = chunk_size
        self.download_timeout = download_timeout
        self.read_timeout = read_timeout
        self.retries = retries
        self.logger = session.logger.new_module("stream.resp-stream")
        self.future = None
        self.exception = None
        self.buffered_data = 0
        self.consumed_data = 0
        self.segment_size = self.session.options.get("stream-segment-size")
        # TODO: Implement segment buffer pooling
        self.segment_buffer = RingBuffer(self.segment_size)

        if download_timeout is None:
            self.download_timeout = self.session.options.get("stream-segment-timeout")
        if read_timeout is None:
            self.read_timeout = self.session.options.get("http-stream-timeout")

    @property
    def consumed(self):
        return self.consumed_data == self.segment_size

    def start(self):
        self.future = self.executor.submit(self._stream_fetch, self.retries)
        return self

    def _stream_fetch(self, retries):
        if self.executor._shutdown or self.closed:
            self.close()
            return

        try:
            self.logger.debug("Started download of segment {0}-{1}",
                              *self.segment.byte_range)

            first_byte = self.segment.byte_range.first_byte
            last_byte = self.segment.byte_range.last_byte
            request_params = HTTPStream.add_range_hdr(first_byte, last_byte,
                                                      self.request_params)
            resp = self.session.http.get(self.segment.uri,
                                         timeout=self.download_timeout,
                                         exception=StreamError,
                                         stream=True,
                                         **request_params)

            self.segment_size = int(resp.headers["Content-Length"])
            if self.segment_buffer.buffer_size != self.segment_size:
                self.segment_buffer.resize(self.segment_size)

            for chunk in resp.iter_content(self.chunk_size):
                # Poll for executor shutdown event and terminate download if received
                if self.executor._shutdown or self.closed:
                    resp.close()
                    self.close()
                    return

                self.segment_buffer.write(chunk)
                self.buffered_data += len(chunk)

            self.logger.debug("Download of segment {0}-{1} complete",
                              *self.segment.byte_range)

        except (StreamError, RequestException, IOError) as err:
            exception = StreamError("Unable to download segment: {0}-{1} ({err})".format(
                                    self.segment.byte_range.first_byte,
                                    self.segment.byte_range.last_byte,
                                    err=err))
            exception.err = err
            self.logger.error("{0}", exception)
            if retries and not (self.executor._shutdown or self.closed):
                self.logger.error("Retrying segment {0}-{1}",
                                  *self.segment.byte_range)
                self._stream_fetch(retries - 1)
            # Couldn't recover from exception
            else:
                self.exception = exception
                self.close()
                raise self.exception

    def read(self, chunk_size):
        if self.closed and not self.exception:
            return b""

        try:
            result = self.segment_buffer.read(chunk_size,
                                              block=not self.future.done(),
                                              timeout=self.read_timeout)
        except IOError as err:
            self.close()
            raise StreamError("Failed to read data from stream: {0}".format(err))

        # The streaming response encountered an unrecoverable exception in one
        # of it's download threads. The stream should crash at this point
        if self.exception:
            self.close()
            raise self.exception

        self.consumed_data += len(result)
        if self.consumed:
            self.close()

        return result

    def iter_content(self, chunk_size):
        return iter(partial(self.read, chunk_size), b"")

    def close(self):
        if not self.closed:
            if self.consumed:
                self.logger.debug("Stream of segment {0}-{1} consumed",
                                  *self.segment.byte_range)
            else:
                self.logger.debug("Download of segment {0}-{1} cancelled",
                                  *self.segment.byte_range)

            self.closed = True
            self.segment_buffer.close()


class SegmentedHTTPStreamWriter(SegmentedStreamWriter):
    def __init__(self, reader, chunk_size=8192, **kwargs):
        SegmentedStreamWriter.__init__(self, reader, **kwargs)
        self.chunk_size = chunk_size
        self.segment_size = reader.segment_size

    def fetch(self, segment, retries=5):
        if self.closed or not retries:
            return

        return StreamingResponse(self.session, self.executor, segment,
                                 self.reader.request_params,
                                 retries=retries,
                                 download_timeout=self.timeout,
                                 read_timeout=self.reader.timeout,
                                 ).start()

    def write(self, segment, result):
        try:
            self.logger.debug("Streaming segment {0}-{1} to output buffer",
                              *segment.byte_range)

            for chunk in result.iter_content(self.chunk_size):
                self.reader.buffer.write(chunk)

            if result.consumed:
                self.logger.debug("Streaming of segment {0}-{1} to buffer complete",
                                  *segment.byte_range)
            else:
                self.logger.debug("Streaming of segment {0}-{1} to buffer stopped",
                                  *segment.byte_range)

        except StreamError as err:
            self.logger.error("Unable to recover stream: {0}",
                              err)
            self.close()


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

    def __repr__(self):
        return "<SegmentedHTTPStream({0!r})>".format(self.url)

    def open(self, seek_pos=0):
        # Signal that this stream type supports seek
        reader = SegmentedHTTPStreamReader(self)
        reader.open(seek_pos=seek_pos)

        self.complete_length = self.get_complete_length()
        if self.complete_length:
            self.supports_seek = True

        return reader
