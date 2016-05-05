import inspect

import requests

from .stream import Stream
from .wrappers import StreamIOThreadWrapper, StreamIOIterWrapper
from ..exceptions import StreamError


def normalize_key(keyval):
    key, val = keyval
    key = hasattr(key, "decode") and key.decode("utf8", "ignore") or key

    return key, val


def valid_args(args):
    argspec = inspect.getargspec(requests.Request.__init__)

    return dict(filter(lambda kv: kv[0] in argspec.args, args.items()))


class HTTPStream(Stream):
    """A HTTP stream using the requests library.

    *Attributes:*

    - :attr:`url`  The URL to the stream, prepared by requests.
    - :attr:`args` A :class:`dict` containing keyword arguments passed
      to :meth:`requests.request`, such as headers and cookies.

    """

    __shortname__ = "http"

    # Make sure we always use the correct HTTP stream type
    def __new__(cls, session, *args, **kwargs):
        if (cls is HTTPStream and
                session.options.get("stream-segment-threads") > 1):
            from .segmentedhttp import SegmentedHTTPStream
            return Stream.__new__(SegmentedHTTPStream)
        else:
            return Stream.__new__(cls)

    def __init__(self, session_, url, buffered=True, **args):
        Stream.__init__(self, session_)
        self.logger = self.session.logger.new_module("stream.http")

        self.args = dict(url=url, **args)
        self.buffered = buffered
        self.complete_length = None

    def __repr__(self):
        return "<HTTPStream({0!r})>".format(self.url)

    def __json__(self):
        method = self.args.get("method", "GET")
        req = requests.Request(method=method, **valid_args(self.args))

        # prepare_request is only available in requests 2.0+
        if hasattr(self.session.http, "prepare_request"):
            req = self.session.http.prepare_request(req)
        else:
            req = req.prepare()

        headers = dict(map(normalize_key, req.headers.items()))

        return dict(type=type(self).shortname(), url=req.url,
                    method=req.method, headers=headers,
                    body=req.body)

    @property
    def url(self):
        method = self.args.get("method", "GET")
        return requests.Request(method=method,
                                **valid_args(self.args)).prepare().url

    @staticmethod
    def add_range_hdr(first_byte, last_byte, request_params):
        headers = request_params.pop("headers", {})
        headers["Range"] = "bytes={0}-{1}".format(first_byte, last_byte)
        request_params["headers"] = headers

        return request_params

    def get_complete_length(self):
        """
        Gets the total content length of all media segments. This method
        will communicate with the stream server to try to work out the content
        length. The content length may be unavailable, such as when streaming
        a live stream. In this case the method should return None.

        :returns: None for unknown content length.\r\n
                  The total content length of all media segments
                  for known content length.
        """
        if not self.complete_length:
            self.logger.debug("Retrieving complete content length")
            res = self.session.http.head(self.url,
                                         acceptable_status=[200, 206],
                                         exception=StreamError)
            try:
                self.complete_length = int(res.headers.get("Content-Length"))
                self.logger.debug("Complete content length of {0} bytes retrieved",
                                  self.complete_length)
            except (ValueError, TypeError):
                self.complete_length = None
                self.logger.debug("Unable to get content length")

        return self.complete_length

    def open(self, seek_pos=0, *args, **kwargs):
        self.complete_length = self.get_complete_length()
        if self.complete_length:
            self.args = self.add_range_hdr(seek_pos,
                                           self.complete_length - 1,
                                           self.args)
            self.supports_seek = True

        method = self.args.get("method", "GET")
        timeout = self.session.options.get("http-timeout")
        res = self.session.http.request(method=method,
                                        stream=True,
                                        exception=StreamError,
                                        timeout=timeout,
                                        **self.args)

        fd = StreamIOIterWrapper(res.iter_content(8192))
        if self.buffered:
            fd = StreamIOThreadWrapper(self.session, fd, timeout=timeout)

        return fd

