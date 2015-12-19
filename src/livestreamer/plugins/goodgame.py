import re

from livestreamer.plugin import Plugin
from livestreamer.plugin.api import http
from livestreamer.stream import HLSStream

HLS_URL_FORMAT = "http://hls.goodgame.ru/hls/{0}.m3u8"
API_URL_FORMAT = "http://goodgame.ru/api/getchannelstatus?fmt=json&id={0}"
QUALITIES = {
    "1080p": "",
}

_url_re = re.compile("http://(?:www\.)?goodgame.ru/channel/(?P<user>\w+)")
_stream_re = re.compile(
    "\"stream_id\":\"(\d+)\""
)

class GoodGame(Plugin):
    @classmethod
    def can_handle_url(self, url):
        return _url_re.match(url)

    def _check_stream(self, url):
        res = http.get(url, acceptable_status=(200, 404))
        if res.status_code == 200:
            return True

    def _get_streams(self):
        match = _url_re.search(self.url)
        id = match.group(1)
        url = API_URL_FORMAT.format(id)
        res = http.get(url)
        match = _stream_re.search(res.text)
        if not match:
            return

        stream_id = match.group(1)
        url = HLS_URL_FORMAT.format(stream_id)
        if not self._check_stream(url):
            return
        streams = {}
        streams["1080p"] = HLSStream(self.session, url)

        return streams

__plugin__ = GoodGame
