import re

from livestreamer.plugin import Plugin
from livestreamer.plugin.api import http, validate
from livestreamer.stream import RTMPStream

EMBED_URL = "http://www.castamp.com/embed.php?c={}"

_url_re = re.compile("http(s)?://(\w+\.)?castamp.com/live/(?P<channel>[^/?]+)")
_embed_re = re.compile("http(s)?://(\w+\.)?castamp.com/embed\.php\?c=(?P<channel>[^&]+)")
_comment_re = re.compile("/\\*(.|[\\r\\n])*\\*/")
_filename_re = re.compile("'file':\s*'(?P<filename>.+)',")
_streamer_re = re.compile("'streamer':\s*'(?P<streamer>.+)',")
_string_re = re.compile("^'(.*)'$")
_var_re = re.compile("var\s+(?P<var>[^\s]+)\s+=\s+(?P<value>'.+')\s*;[\\n|\\r]")

_schema = validate.Schema(
    validate.transform(lambda x: _comment_re.sub("", x)),
    validate.union({
        "data": validate.text,
        "var_matches": validate.transform(_var_re.findall)
    }),
)

_filename_schema = validate.Schema(
        validate.transform(_filename_re.search),
        validate.any(
            None,
            validate.get("filename")
        ),
)

_streamer_schema = validate.Schema(
        validate.transform(_streamer_re.search),
        validate.any(
            None,
            validate.all(
                validate.get("streamer"),
                validate.url(scheme="rtmp")
            )
        ),
)

class CastAmp(Plugin):
    @classmethod
    def can_handle_url(cls, url):
        return _url_re.match(url) or _embed_re.match(url)

    def _get_streams(self):
        channel = _url_re.match(self.url)
        if not channel: channel = _embed_re.match(self.url)
        channel = channel.group("channel")

        embed_url = EMBED_URL.format(channel)
        res = http.get(embed_url, schema=_schema)

        if not res or not res["data"]:
            return
        for var_match in res["var_matches"]:
            var, value = var_match
            res["data"] = re.sub (var, value, res["data"])

        filename = _filename_schema.validate(res["data"])

        streamer = _streamer_schema.validate(res["data"])

        if not validate.startswith(channel)(filename):
            return

        rtmp_url = streamer + "/" + filename

        stream = RTMPStream(self.session, {
            "rtmp": rtmp_url,
            "pageUrl": embed_url,
            "live": True,
        })

        print (rtmp_url)

        return dict(live=stream)

__plugin__ = CastAmp
