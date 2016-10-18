import re
import json

from livestreamer.compat import urljoin
from livestreamer.plugin import Plugin
from livestreamer.plugin.api import http, validate
from livestreamer.stream import RTMPStream

_url_re = re.compile("http(s)?://(\w+\.)?connectcast.tv/")
_user_id_re = re.compile(r'\sdata-channel="([^"]+)"')

_smil_schema = validate.Schema(
    validate.union({
        "base": validate.all(
            validate.xml_find("head/meta"),
            validate.get("base"),
            validate.url(scheme="rtmp")
        ),
        "video": validate.all(
            validate.xml_find("body/video"),
            validate.all(
                validate.get("src"),
                validate.text
            )
        )
    })
)

class ConnectCast(Plugin):
    @classmethod
    def can_handle_url(self, url):
        return _url_re.match(url)

    def _get_streams(self):
        res = http.get(self.url)
        match = _user_id_re.search(res.text)
        if not match:
            return
        user_id = match.group(1)

        res = http.get(urljoin(self.url, "/channel/stream/%s" % user_id))
        smil = http.xml(res, schema=_smil_schema)
        print(smil)

        stream = RTMPStream(self.session, {
            "rtmp": "{0}/{1}".format(smil["base"], smil["video"]),
            "playpath": smil["video"],
            "pageUrl": self.url,
            "live": True
        })

        return {"live": stream}

__plugin__ = ConnectCast
