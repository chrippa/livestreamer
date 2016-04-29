from collections import namedtuple

from .http import HTTPStream
from .segmented import (SegmentedStreamReader,
                        SegmentedStreamWriter,
                        SegmentedStreamWorker)
from ..exceptions import StreamError

ByteRange = namedtuple("ByteRange", "first_byte_pos last_byte_pos")
Segment = namedtuple("Segment", "uri byte_range")


class SegmentedHTTPStreamWorker(SegmentedStreamWorker):
    def __init__(self, *args, **kwargs):
        SegmentedStreamWorker.__init__(self, *args, **kwargs)
        self.segment_size = self.session.options.get("stream-segment-size")

        try:
            self.content_len = self.get_content_len()
        except StreamError as err:
            self.logger.error("Unable to retrieve content length: {0}", err)
            self.close()

    def get_content_len(self):
        self.logger.debug("Retrieving content length")
        res = self.session.http.head(self.stream.url,
                                     acceptable_status=[200, 206],
                                     exception=StreamError,
                                     **self.reader.request_params)
        content_length = int(res.headers["Content-Length"])
        self.logger.debug("Content length of {0} bytes retrieved",
                          content_length)
        return content_length

    def iter_segments(self):
        pos = 0
        while not self.closed:
            segment = Segment(self.stream.url,
                              ByteRange(pos, pos + self.segment_size - 1))
            self.logger.debug("Adding segment {0}-{1} to queue",
                              *segment.byte_range)
            yield segment
            pos += self.segment_size

            # End of stream
            stream_end = pos >= self.content_len
            if self.closed or stream_end:
                return


class SegmentedHTTPStreamWriter(SegmentedStreamWriter):
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
            return self.session.http.get(segment.uri,
                                         timeout=self.timeout,
                                         exception=StreamError,
                                         **request_params)
        except StreamError as err:
            self.logger.error("Failed to load segment {0}-{1}: {2}",
                              *segment.byte_range, err)
            return self.fetch(segment, retries - 1)

    def write(self, segment, result):
        self.reader.buffer.write(result.content)
        self.logger.debug("Download of segment {0}-{1} complete",
                          *segment.byte_range)


class SegmentedHTTPStreamReader(SegmentedStreamReader):
    __worker__ = SegmentedHTTPStreamWorker
    __writer__ = SegmentedHTTPStreamWriter

    def __init__(self, stream, *args, **kwargs):
        SegmentedStreamReader.__init__(self, stream, *args, **kwargs)
        self.logger = stream.session.logger.new_module("stream.shttp")
        self.request_params = dict(stream.args)
        self.timeout = stream.session.options.get("http-stream-timeout")

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

    __shortname__ = "shttp"

    def __init__(self, session_, url, **args):
        HTTPStream.__init__(self, session_, url, **args)

    def __repr__(self):
        return "<SegmentedHTTPStream({0!r})>".format(self.url)

    def open(self):
        reader = SegmentedHTTPStreamReader(self)
        reader.open()

        return reader
