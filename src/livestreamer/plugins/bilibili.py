import re

from livestreamer.plugin import Plugin
from livestreamer.plugin.api import http
from livestreamer.stream import HTTPStream


_url_re = re.compile("""http://live\.bilibili\.com/(?P<channel>\d+)""", re.VERBOSE)

API_URL = "http://live.bilibili.com/api/playurl?cid={0}"
CHANNEL_RE = re.compile("""ROOMID\s*=\s*(?P<channel>\d+)\s*;""")


class Bilibili(Plugin):
    @classmethod
    def can_handle_url(self, url):
        return _url_re.match(url)

    def _get_streams(self):
        match = _url_re.match(self.url)
        channel = int(match.group("channel"))
        if channel <= 5000:
            res = http.get(self.url)
            match = CHANNEL_RE.search(res.text)
            if not match:
                return
            channel = int(match.group("channel"))

        res = http.get(API_URL.format(channel))
        xmldoc = http.xml(res)
        meta = xmldoc.find(".//durl")
        for ele in meta:
            if ele.text.startswith("http"):
                yield ele.tag, HTTPStream(self.session, ele.text)


__plugin__ = Bilibili
