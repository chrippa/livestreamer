import io
import json


class Stream(object):
    __shortname__ = "stream"

    """
    This is a base class that should be inherited when implementing
    different stream types. Should only be created by plugins.
    """

    def __init__(self, session):
        self.session = session
        self._supports_seek = False
        self._complete_length = None
        self._seek_event = None

    def _set_seek_supported(self, complete_length):
        """
        Should be called by the stream's open method just before it returns.
        This allows the stream type to do it's own checking with the remote
        stream as to whether seeking will be supported or not.

        :param complete_length: The complete length of the content to be
                                streamed
        """
        self._supports_seek = True
        self._complete_length = complete_length

    @property
    def supports_seek(self):
        return self._supports_seek

    @property
    def complete_length(self):
        return self._complete_length

    def __repr__(self):
        return "<Stream()>"

    def __json__(self):
        return dict(type=type(self).shortname())

    def open(self, seek_pos=0):
        """
        Attempts to open a connection to the stream.
        Returns a file-like object that can be used to read the stream data.

        Raises :exc:`StreamError` on failure.
        """
        raise NotImplementedError

    @property
    def json(self):
        obj = self.__json__()
        return json.dumps(obj)

    @classmethod
    def shortname(cls):
        return cls.__shortname__


class StreamIO(io.IOBase):
    pass


__all__ = ["Stream", "StreamIO"]
