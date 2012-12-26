from . import Stream, StreamIOWrapper, StreamError
from ..utils import urlget

class HTTPStream(Stream):
    def __init__(self, session, url, **args):
        Stream.__init__(self, session)

        self.url = url
        self.args = args

    def cmd(self):
        return self.url
        
    def open(self):
        res = urlget(self.url, stream=True,
                     exception=StreamError,
                     **self.args)

        return StreamIOWrapper(res.raw)

