import re

from livestreamer.compat import urljoin
from livestreamer.plugin import Plugin
from livestreamer.plugin.api import http, validate
from livestreamer.stream import HLSStream

CHANNEL_DETAILS_URI = "https://api.streamup.com/v1/channels/{0}?access_token={1}"
CHANNEL_MANIFEST_URI = "https://lancer.streamup.com/api/channels/{0}/playlists"

_url_re = re.compile("http(s)?://(\w+\.)?streamup.com/(?P<channel>[^/?]+)")

_channel_details_schema = validate.Schema({
    "channel": {
        "live": bool,
        "slug": validate.text
    }
})

_channel_manifest_schema = validate.Schema({
    "hls": validate.text
})

class StreamupCom(Plugin):
    @classmethod
    def can_handle_url(cls, url):
        return _url_re.match(url)

    def _get_streams(self):
        match = _url_re.match(self.url)
        params = match.groupdict()

        # Check if the stream is online
        res = http.get(CHANNEL_DETAILS_URI.format(params["channel"], ""))
        channel_details = http.json(res, schema=_channel_details_schema)
        if not channel_details["channel"]["live"]:
            return

        res = http.get(CHANNEL_MANIFEST_URI.format(channel_details["channel"]["slug"]))
        channel_manifest = http.json(res, schema=_channel_manifest_schema)

        streams = HLSStream.parse_variant_playlist(self.session, channel_manifest["hls"])
        return streams

__plugin__ = StreamupCom
