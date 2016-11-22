import re

from livestreamer.plugin import Plugin
from livestreamer.plugin.api import http, validate
from livestreamer.stream import HTTPStream

API_URL = "http://www.panda.tv/api_room?roomid={0}"
STATUS_ONLINE = 2
STATUS_OFFLINE = 3

_url_re = re.compile("""
    http(s)?://(www\.)?panda.tv
    /(?P<channel>[^/]+)
""", re.VERBOSE)

_room_schema = validate.Schema(
    {
        "data": {
            "videoinfo": validate.any(None, {
                "status": validate.all(
                    validate.text,
                    validate.transform(int)
                ),
                "room_key": validate.text
            })
        }
    },
    validate.get("data"),
    validate.get("videoinfo")
)


class Pandatv(Plugin):
    @classmethod
    def can_handle_url(self, url):
        return _url_re.match(url)

    def _get_streams(self):
        match = _url_re.match(self.url)
        channel = match.group("channel")

        res = http.get(API_URL.format(channel))
        room = http.json(res, schema=_room_schema)
        if not room:
            return

        if room["status"] != STATUS_ONLINE:
            return

        http_url = "http://pl3.live.panda.tv/live_panda/{room[room_key]}.flv".format(room=room)
        http_stream = HTTPStream(self.session, http_url)
        if http_stream:
            yield "http", http_stream

__plugin__ = Pandatv
