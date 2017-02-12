import re

from livestreamer.compat import urlparse
from livestreamer.plugin import Plugin
from livestreamer.plugin.api import http, validate
from livestreamer.stream import HLSStream

CHINFO_URL = "http://www.filmon.com/ajax/getChannelInfo"
SWF_URL = "http://www.filmon.com/tv/modules/FilmOnTV/files/flashapp/filmon/FilmonPlayer.swf"
VODINFO_URL = "http://www.filmon.com/api/vod/movie?id={0}"

AJAX_HEADERS = {
    "Referer": "http://www.filmon.com",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0"
}
QUALITY_WEIGHTS = {
    "high": 720,
    "low": 480
}

_url_re = re.compile("http(s)?://(\w+\.)?filmon.com/(channel|tv|vod)/")
_channel_id_re = re.compile("/channels/(\d+)/extra_big_logo.png")
_vod_id_re = re.compile("movie_id=(\d+)")

_channel_schema = validate.Schema({
    "streams": [{
        "name": validate.text,
        "quality": validate.text,
        "url": validate.url(scheme="http")
    }]
})
_vod_schema = validate.Schema(
    {
        "response": {
            validate.optional("streams"): {
                validate.text: {
                    "name": validate.text,
                    "url": validate.url(scheme="http")
                }
            }
        }
    },
    validate.get("response")
)


def ajax(*args, **kwargs):
    kwargs["headers"] = AJAX_HEADERS
    return http.post(*args, **kwargs)


class Filmon(Plugin):
    @classmethod
    def can_handle_url(cls, url):
        return _url_re.match(url)

    @classmethod
    def stream_weight(cls, key):
        weight = QUALITY_WEIGHTS.get(key)
        if weight:
            return weight, "filmon"

        return Plugin.stream_weight(key)

    def _create_rtmp_stream(self, stream, live=True):
        rtmp = stream["url"]
        playpath = stream["name"]
        parsed = urlparse(rtmp)
        if parsed.query:
            app = "{0}?{1}".format(parsed.path[1:], parsed.query)
        else:
            app = parsed.path[1:]

        if playpath.endswith(".mp4"):
            playpath = "mp4:" + playpath

        params = {
            "rtmp": rtmp,
            "pageUrl": self.url,
            "swfUrl": SWF_URL,
            "playpath": playpath,
            "app": app,
        }
        if live:
            params["live"] = True

        return RTMPStream(self.session, params)

    def _get_live_streams(self, channel_id):
        params = {"channel_id": channel_id}
        res = ajax(CHINFO_URL, data=params)
        channel = http.json(res, schema=_channel_schema)

        # TODO: Replace with "yield from" when dropping Python 2.
        for stream in self._parse_live_streams(channel):
            yield stream

    def _parse_live_streams(self, channel):
        for stream in channel["streams"]:
            name = stream["quality"]
            scheme = urlparse(stream["url"]).scheme

            yield name, HLSStream(self.session, stream["url"])

    def _get_vod_streams(self, movie_id):
        res = ajax(VODINFO_URL.format(movie_id))
        vod = http.json(res, schema=_vod_schema)

        # TODO: Replace with "yield from" when dropping Python 2.
        for stream in self._parse_vod_streams(vod):
            yield stream

    def _parse_vod_streams(self, vod):
        for name, stream in vod["streams"].items():
            scheme = urlparse(stream["url"]).scheme

            yield name, HLSStream(self.session, stream["url"])

    def _get_streams(self):
        res = http.get(self.url)

        match = _vod_id_re.search(res.text)
        if match:
            return self._get_vod_streams(match.group(1))

        match = _channel_id_re.search(res.text)
        if match:
            return self._get_live_streams(match.group(1))

__plugin__ = Filmon
