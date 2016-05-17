from .segmentedhttp import SegmentedHTTPStream
from .http import HTTPStream


class HTTPSelect(HTTPStream):
    def __new__(cls, session, url, *args, **kwargs):
        complete_length = HTTPStream.get_complete_length(session, url)
        kwargs["complete_length"] = complete_length
        if session.options.get("stream-segment-threads") > 1 and complete_length:
            return SegmentedHTTPStream(session, url, **kwargs)
        else:
            return HTTPStream(session, url, *args, **kwargs)
