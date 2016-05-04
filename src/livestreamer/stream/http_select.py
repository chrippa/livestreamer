from .stream import Stream
from .segmentedhttp import SegmentedHTTPStream
from .http import HTTPStream


class HTTPSelect(Stream):
    def __new__(cls, session, *args, **kwargs):
        if session.options.get("stream-segment-threads") > 1:
            return SegmentedHTTPStream(session, *args, **kwargs)
        else:
            return HTTPStream(session, *args, **kwargs)
