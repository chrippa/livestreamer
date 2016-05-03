from collections import namedtuple
from functools import partial
from threading import Thread

import requests

from livestreamer.buffers import RingBuffer
from .http import HTTPStream
from .segmented import (SegmentedStreamReader,
                        SegmentedStreamWriter,
                        SegmentedStreamWorker)
from ..exceptions import StreamError

ByteRange = namedtuple("ByteRange", "first_byte_pos last_byte_pos")
Segment = namedtuple("Segment", "uri byte_range")


class SegmentedHTTPStreamWorker(SegmentedStreamWorker):
    def __init__(self, reader, *args, **kwargs):
        SegmentedStreamWorker.__init__(self, reader, *args, **kwargs)
        self.segment_size = reader.segment_size

    def iter_segments(self):
        # Get initial position to stream from
        pos = self.initial_seek_pos
        if pos > 0:
            self.logger.debug("Received seek. Seek to pos: {0}", pos)

        while not self.closed:
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
    def __init__(self, executor, segment, response, session, chunk_size=8192, timeout=None):
        self.closed = False
        self.executor = executor  # Not owned by the streaming response object
        self.segment = segment
        self.response = response  # Owned by object, must cleanup on close
        self.logger = session.logger.new_module("respstream")
        self.chunk_size = chunk_size
        self.timeout = timeout
        self.future = None
        self.buffered_data = 0
        self.consumed_data = 0
        self.segment_size = int(response.headers["Content-Length"])
        # TODO: Implement segment buffer pool
        self.segment_buffer = RingBuffer(self.segment_size)  # Owned by object, must cleanup on close

    def start(self):
        self.future = self.executor.submit(self._stream_fetch)
        return self

    def _stream_fetch(self):
        self.logger.debug("Started download of segment {0}-{1}",
                          *self.segment.byte_range)
        for chunk in self.response.iter_content(self.chunk_size):
            # Poll for executor shutdown event and terminate download if received
            if self.executor._shutdown:
                self.close()
                return

            self.segment_buffer.write(chunk)
            self.buffered_data += len(chunk)

        self.logger.debug("Download of segment {0}-{1} complete",
                          *self.segment.byte_range)

    def read(self, chunk_size):
        if self.closed:
            return b""

        result = self.segment_buffer.read(chunk_size,
                                          block=not self.future.done(),
                                          timeout=30)

        self.consumed_data += len(result)
        if self.consumed_data == self.segment_size:
            self.close()

        return result

    def iter_content(self, chunk_size):
        return iter(partial(self.read, chunk_size), b"")

    def close(self):
        if self.consumed_data == self.segment_size:
            self.logger.debug("Stream of segment {0}-{1} consumed",
                              *self.segment.byte_range)
        else:
            self.logger.debug("Download of segment {0}-{1} cancelled",
                              *self.segment.byte_range)

        self.closed = True
        self.response.close()
        self.segment_buffer.close()


class SegmentedHTTPStreamWriter(SegmentedStreamWriter):
    def __init__(self, reader, chunk_size=8192, **kwargs):
        SegmentedStreamWriter.__init__(self, reader, **kwargs)
        self.chunk_size = chunk_size
        self.segment_size = reader.segment_size

    def create_request_params(self, segment):
        request_params = dict(self.reader.request_params)
        headers = request_params.pop("headers", {})

        headers["Range"] = "bytes={0}-{1}".format(*segment.byte_range)

        request_params["headers"] = headers

        return request_params

    def fetch(self, segment, retries=None):
        if self.closed or not retries:
            return

        try:
            request_params = self.create_request_params(segment)
            resp = self.session.http.get(segment.uri,
                                         timeout=self.timeout,
                                         exception=StreamError,
                                         stream=True,  # This must be true to use with executor
                                         **request_params)
            return StreamingResponse(self.executor, segment, resp, self.session).start()
        except StreamError as err:
            self.logger.error("Failed to load segment {0}-{1}: {2}",
                              segment.byte_range.first_byte_pos,
                              segment.byte_range.last_byte_pos,
                              err)
            return self.fetch(segment, retries - 1)

    def write(self, segment, result):
        self.logger.debug("Streaming segment {0}-{1} to buffer",
                          *segment.byte_range)

        for chunk in result.iter_content(self.chunk_size):
            self.reader.buffer.write(chunk)

        self.logger.debug("Streaming of segment {0}-{1} to buffer complete",
                          *segment.byte_range)


class SegmentedHTTPStreamReader(SegmentedStreamReader):
    __worker__ = SegmentedHTTPStreamWorker
    __writer__ = SegmentedHTTPStreamWriter

    def __init__(self, stream, *args, **kwargs):
        SegmentedStreamReader.__init__(self, stream, *args, **kwargs)
        self.logger = stream.session.logger.new_module("stream.shttp")
        self.request_params = dict(stream.args)
        self.timeout = stream.session.options.get("http-stream-timeout")
        self.segment_size = self.session.options.get("stream-segment-size")

        # These params are reserved for internal use
        self.request_params.pop("exception", None)
        self.request_params.pop("stream", None)
        self.request_params.pop("timeout", None)
        self.request_params.pop("url", None)

    def _get_complete_length(self):
        self.logger.debug("Retrieving complete content length")
        res = self.session.http.head(self.stream.url,
                                     acceptable_status=[200, 206],
                                     exception=StreamError,
                                     **self.request_params)
        complete_length = int(res.headers["Content-Length"])
        self.logger.debug("Complete content length of {0} bytes retrieved",
                          complete_length)
        return complete_length


class SegmentedHTTPStream(HTTPStream):
    """An HTTP stream using the requests library with multiple segment threads.

    *Attributes:*

    - :attr:`url`  The URL to the stream, prepared by requests.
    - :attr:`args` A :class:`dict` containing keyword arguments passed
      to :meth:`requests.request`, such as headers and cookies.

    """

    __shortname__ = "shttp"

    def __init__(self, session_, url, **args):
        HTTPStream.__init__(self, session_, url, **args)

    def __repr__(self):
        return "<SegmentedHTTPStream({0!r})>".format(self.url)

    def open(self, seek_pos=0):
        reader = SegmentedHTTPStreamReader(self)
        reader.open(seek_pos)

        # Signal that this stream type always supports seeking
        self._set_seek_supported(reader.complete_length)

        return reader
