from livestreamer.stream import RTMPStream
from livestreamer.plugins import Plugin, PluginError, NoStreamsError

import re

class RawRtmp(Plugin):

    @classmethod
    def can_handle_url(self, url):
        #rtmp://, rtmpt://, rtmpe://, rtmpte://, and rtmps://
        return re.match("^rtmp([t|e|s]|te)?://.+", url) is not None

    def _get_streams(self):
        streams = {}
        try:
            streams["live"] = RTMPStream(self.session, self._parseOptions())
        except IOError:
            raise NoStreamsError(self.url)

        return streams

    def _parseOptions(self):
        options = {}
        optionsURL = self.url.split()
        options['rtmp'] = optionsURL[0].strip()
        for pv in optionsURL[1:]:
            (param, value) = pv.split("=")
            options[param.strip()] = value.strip()
        return options


__plugin__ = RawRtmp
