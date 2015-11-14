import re

from livestreamer.plugin import Plugin
from livestreamer.plugin.api import http, validate
from livestreamer.plugin.api.utils import parse_json
from livestreamer.stream import HLSStream

PLAYLIST_URL = "https://streamup.global.ssl.fastly.net/app/{0}/playlist.m3u8"

_url_re = re.compile("http(s)?://(\w+\.)?streamup.com/(?P<channel>[^/?]+)")
_roomparams_re = re.compile(r"window.Room\s*=\s*({[^}]*?})")

_schema = validate.Schema(
    validate.transform(_roomparams_re.search),
    validate.any(
        None,
        validate.all(
            validate.get(1),
            validate.transform(parse_json),
            {
                "roomSlug": validate.text,
                "isBroadcasting": bool
            }
        )
    )
)

class StreamupCom(Plugin):
    @classmethod
    def can_handle_url(cls, url):
        return _url_re.match(url)

    def _get_streams(self):
        res = http.get(self.url, schema=_schema)
        if not res:
            return
        if not res["isBroadcasting"]:
            return

        return HLSStream.parse_variant_playlist(self.session, PLAYLIST_URL.format(res["roomSlug"]))

__plugin__ = StreamupCom
