import re

from livestreamer.plugin import Plugin
from livestreamer.plugin.api import http, validate
from livestreamer.stream import RTMPStream

EMBED_URL = "http://www.castamp.com/embed.php?c={}"

_url_re = re.compile("http(s)?://(\w+\.)?castamp.com/live/(?P<channel>[^/?]+)")
_comment_re = re.compile("/\\*(.|[\\r\\n])*\\*/")
_file_re = re.compile("'file':\s*'(?P<file>.+)',")
_streamer_re = re.compile("'streamer':\s*'(?P<streamer>.+)',")

_embed_schema = validate.Schema(
    validate.transform(lambda x: _comment_re.sub("", x)),
    validate.union({
        "file": validate.all(
            validate.transform(_file_re.search),
            validate.any(
                None,
                validate.get("file")
            ),
        ),
        "streamer": validate.all(
            validate.transform(_streamer_re.search),
            validate.any(
                None,
                validate.all(
                    validate.get("streamer"),
                    validate.url(scheme="rtmp")
                )
            ),
        )
    }),
)

class CastAmp(Plugin):
    @classmethod
    def can_handle_url(cls, url):
        return _url_re.match(url)

    def _get_streams(self):
        channel = _url_re.match(self.url).group("channel")
        embed_url = EMBED_URL.format(channel)
        res = http.get(embed_url, schema=_embed_schema)

        if not res["file"] or not res["streamer"]:
            return
        if not validate.startswith(channel)(res["file"]):
            return

        rtmp_url = res["streamer"] + "/" + res["file"]

        stream = RTMPStream(self.session, {
            "rtmp": rtmp_url,
            "pageUrl": embed_url,
            "live": True,
        })

        return dict(live=stream)

__plugin__ = CastAmp
