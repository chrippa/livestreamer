import re

from livestreamer.plugin import Plugin
from livestreamer.plugin.api import http, validate
from livestreamer.plugin.api.utils import parse_json
from livestreamer.stream import HTTPStream

API_URL = "http://www.douyutv.com/api/client/room/{0}"
SWF_URL = "http://staticlive.douyutv.com/common/simplayer/WebRoom.swf?v=2903.3"

_url_re = re.compile("""
    http(s)?://(www\.)?douyutv.com
    /(?P<channel>[^/]+)
""", re.VERBOSE)

_room_schema = validate.Schema(
    {
        "data": validate.any(None, {
            "show_status": validate.all(
                validate.text,
                validate.transform(int)
            ),
            "rtmp_url": validate.text,
            "rtmp_live": validate.text
        })
    }
)

class Douyutv(Plugin):
    @classmethod
    def can_handle_url(self, url):
        return _url_re.match(url)

    def _get_quality(self, label):
        return "live"

    def _get_streams(self):
        match = _url_re.match(self.url)
        if not match:
            return

        channel = match.group("channel")
        res = http.get(API_URL.format(channel))
        room = http.json(res, schema=_room_schema)
        if not room["data"]:
            return

        room = room["data"]
        if room["show_status"] != 1: # 1 is live, 2 is offline
            return

        streams = {
            "live": HTTPStream(self.session, room["rtmp_url"] + "/" + room["rtmp_live"])
        }
        return streams

__plugin__ = Douyutv
