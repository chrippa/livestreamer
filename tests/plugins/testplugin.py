from livestreamer.plugins import Plugin
from livestreamer.options import Options
from livestreamer.stream import *

class TestPlugin(Plugin):
    options = Options({
        "a_option": "default"
    })

    @classmethod
    def can_handle_url(self, url):
        return "test.se" in url

    def _get_streams(self):
        streams = {}
        streams["rtmp"] = RTMPStream(self.session, dict(rtmp="rtmp://test.se"))
        streams["hls"] = HLSStream(self.session, "http://test.se/playlist.m3u8")
        streams["http"] = HTTPStream(self.session, "http://test.se/stream")
        streams["akamaihd"] = AkamaiHDStream(self.session, "http://test.se/stream")

        streams["240p"] = HTTPStream(self.session, "http://test.se/stream")
        streams["360p"] = HTTPStream(self.session, "http://test.se/stream")
        streams["480p"] = HTTPStream(self.session, "http://test.se/stream")
        streams["1080p"] = HTTPStream(self.session, "http://test.se/stream")

        streams["350k"] = HTTPStream(self.session, "http://test.se/stream")
        streams["800k"] = HTTPStream(self.session, "http://test.se/stream")
        streams["1500k"] = HTTPStream(self.session, "http://test.se/stream")
        streams["3000k"] = HTTPStream(self.session, "http://test.se/stream")

        return streams

__plugin__ = TestPlugin
