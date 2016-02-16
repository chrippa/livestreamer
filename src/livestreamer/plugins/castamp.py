import re

from livestreamer.plugin import Plugin
from livestreamer.plugin.api import http
from livestreamer.stream import RTMPStream

EMBED_URL = "http://www.castamp.com/embed.php?c={}"

_url_re = re.compile("http(s)?://(\w+\.)?castamp.com/live/(?P<channel>[^/?]+)")
_comment_re = re.compile("/\\*(.|[\\r\\n])*\\*/")
_file_re = re.compile("'file':\s*'(.+)',")
_streamer_re = re.compile("'streamer':\s*'(.+)',")

class CastAmp(Plugin):
    @classmethod
    def can_handle_url(cls, url):
        return _url_re.match(url)

    def _get_streams(self):
        channel = _url_re.match (self.url).group (3)

        embed_url = EMBED_URL.format (channel)

        embed_res = http.get (embed_url).text
        if not embed_res: return

        clean_embed_res = _comment_re.sub ("", embed_res)

        file = _file_re.search (clean_embed_res)
        if not file: return
        file = file.group (1)

        streamer = _streamer_re.search (clean_embed_res)
        if not streamer: return
        streamer = streamer.group (1)

        rtmp_url = streamer + "/" + file

        stream = RTMPStream (self.session, {
            "rtmp": rtmp_url,
            "pageUrl": embed_url,
            "live": True,
        })

        return dict (live=stream)

__plugin__ = CastAmp
