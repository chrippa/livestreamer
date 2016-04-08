import re

from livestreamer.plugin import Plugin
from livestreamer.plugin.api import http, validate
from livestreamer.stream import HTTPStream

CHANNEL_INFO_URI = "https://web.immomo.com/webmomo/api/scene/profile/infos"

_url_re = re.compile(r"https://web.immomo.com/live/(?P<rt>\d+)-(?P<rd>\d+)")

_info_schema = validate.Schema({
    "ec": 200,
    "data": {
        "live": bool,
        "url": validate.any(
            "",
            validate.url(scheme="http")
        )
    }
})

class Immomo(Plugin):
    @classmethod
    def can_handle_url(cls, url):
        return _url_re.match(url)

    def _get_streams(self):
        match = _url_re.match(self.url)
        if match is None:
            return

        # load session cookies
        res = http.get(self.url)
        if res.status_code != 200:
            return

        data = {
            "rt": match.group("rt"),
            "rd": match.group("rd")
        }
        res = http.post(CHANNEL_INFO_URI, data=data)
        info = http.json(res, schema=_info_schema)

        if not info["data"]["live"] or info["data"]["url"] == "":
            return

        url = info["data"]["url"].rstrip()
        return {"live": HTTPStream(self.session, url)}

__plugin__ = Immomo
