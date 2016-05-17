import inspect
from threading import Thread

import requests

from .stream import Stream
from .wrappers import StreamIOThreadWrapper, StreamIOIterWrapper
from ..exceptions import StreamError, MailboxClosed


def normalize_key(keyval):
    key, val = keyval
    key = hasattr(key, "decode") and key.decode("utf8", "ignore") or key

    return key, val


def valid_args(args):
    argspec = inspect.getargspec(requests.Request.__init__)

    return dict(filter(lambda kv: kv[0] in argspec.args, args.items()))


class SeekCoordinator(Thread):
    def __init__(self, stream):
        self.stream = stream
        self.logger = stream.logger
        self.session = stream.session
        self.mailbox = self.stream.msg_broker.register("seek_coordinator")
        self.mailbox.subscribe("seek_event")
        self.request_params = stream.args
        self.closed = False

        Thread.__init__(self)
        self.daemon = True

    def run(self):
        while not self.closed and not self.stream.fd.closed:
            try:
                seek_event = self.mailbox.get("seek_event", block=True)
            except MailboxClosed:
                self.close()
                break
            else:
                first_byte = seek_event.data
                HTTPStream.add_range_hdr(first_byte, "",
                                         self.request_params)
                self.logger.debug("Seek coordinator received a seek event, "
                                  "re-initializing stream at pos {0}"
                                  .format(first_byte))

                # Get stream iterator
                res = HTTPStream.send_request(self.session,
                                              self.request_params,
                                              stream=True)

                # Re-initialize stream io at new position
                if self.stream.buffered:
                    fd = StreamIOIterWrapper(res.iter_content(8192))
                    self.stream.fd.re_init(fd)
                else:
                    self.stream.fd.re_init(res.iter_content(8192))

                # Close down incomplete request
                self.stream.res.close()
                self.stream.res = res

                seek_event.set_handled()

        self.close()

    def close(self):
        if not self.closed:
            self.closed = True
            self.mailbox.close()


class HTTPStream(Stream):
    """A HTTP stream using the requests library.

    *Attributes:*

    - :attr:`url`  The URL to the stream, prepared by requests.
    - :attr:`args` A :class:`dict` containing keyword arguments passed
      to :meth:`requests.request`, such as headers and cookies.

    """

    __shortname__ = "http"

    def __init__(self, session_, url, buffered=True, **kwargs):
        Stream.__init__(self, session_)
        self.logger = self.session.logger.new_module("stream.http")

        self.complete_length = kwargs.pop("complete_length", None)
        self.args = dict(url=url, **kwargs)
        self.buffered = buffered
        self.mailbox = self.msg_broker.register("http")
        self.fd = None
        self.res = None

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
        request_params = dict(request_params)
        headers = request_params.pop("headers", {})
        headers["Range"] = "bytes={0}-{1}".format(first_byte, last_byte)
        request_params["headers"] = headers

        return request_params

    @staticmethod
    def get_complete_length(session, url):
        """
        Gets the total content length of all media segments. This method
        will communicate with the stream server to try to work out the content
        length. The content length may be unavailable, such as when streaming
        a live stream. In this case the method should return None.

        :returns: None for unknown content length.\r\n
                  The total content length of all media segments
                  for known content length.
        """
        logger = session.logger.new_module("stream.http")
        logger.debug("Retrieving complete content length")

        try:
            res = session.http.head(url,
                                    acceptable_status=[200, 206],
                                    exception=StreamError)

            complete_length = int(res.headers.get("Content-Length"))
            logger.debug("Complete content length of {0} bytes retrieved",
                         complete_length)
        except (StreamError, ValueError, TypeError):
            complete_length = None
            logger.debug("Unable to get content length")

        return complete_length

    def open(self):
        if self.complete_length is None:
            self.complete_length = HTTPStream.get_complete_length(self.session,
                                                                  self.url)

        timeout = self.session.options.get("http-timeout")
        self.res = self.send_request(self.session, self.args, stream=True)

        self.fd = StreamIOIterWrapper(self.res.iter_content(8192))
        if self.buffered:
            self.fd = StreamIOThreadWrapper(self.session, self.fd, timeout=timeout)

        if self.complete_length:
            self.supports_seek = True
            self.args = self.add_range_hdr(0, "", self.args)

            self.seek_coordinator = SeekCoordinator(self)
            self.seek_coordinator.start()

        return self.fd

    @staticmethod
    def send_request(session, args, stream=False, timeout=None):
        method = args.get("method", "GET")
        if timeout is None:
            timeout = session.options.get("http-timeout")
        return session.http.request(method=method,
                                    stream=stream,
                                    exception=StreamError,
                                    timeout=timeout,
                                    **args)

