import re

from livestreamer.plugin import Plugin
from livestreamer.plugin.api import http, validate
from livestreamer.stream import RTMPStream

EMBED_URL = "http://www.castamp.com/embed.php?c={}"
VAR_RE = "var {} = '(.*)';"

_url_re = re.compile("http(s)?://(\w+\.)?castamp.com/live/(?P<channel>[^/?]+)")
_embed_re = re.compile("http(s)?://(\w+\.)?castamp.com/embed\.php\?c=(?P<channel>[^&]+)")
_comment_re = re.compile("/\\*(.|[\\r\\n])*\\*/")
_filename_re = re.compile("'file':\s*(?P<filename>'?.+'?),")
_streamer_re = re.compile("'streamer':\s*(?P<streamer>'?.+'?),")
_string_re = re.compile("^'(.*)'$")

class CastAmp(Plugin):
    @classmethod
    def can_handle_url(cls, url):
        return _url_re.match(url) or _embed_re.match(url)

    def _get_streams(self):
        channel = _url_re.match(self.url).group("channel")
        embed_url = EMBED_URL.format(channel)
        res = http.get(embed_url)

        if not res or not res.text:
            return
        res = res.text
        res = _comment_re.sub ("", res)

        filename = _filename_re.search(res)
        if not filename: return
        filename = filename.group('filename')
        if not _string_re.search(filename):
            _filename_var_re = re.compile(VAR_RE.format(filename))
            filename = _filename_var_re.search(res)
            if not filename: return
            filename = filename.group(1)
        else:
            filename = _string_re.search(filename).group(1)

        streamer = _streamer_re.search(res)
        if not streamer: return
        streamer = streamer.group('streamer')
        if not _string_re.search(streamer):
            _streamer_var_re = re.compile(VAR_RE.format(streamer))
            streamer = _streamer_var_re.search(res)
            if not streamer: return
            streamer = streamer.group(1)
        else:
            streamer = _string_re.search(streamer).group(1)

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
