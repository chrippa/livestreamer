import re
import json

from livestreamer.plugin import Plugin
from livestreamer.plugin.api import http, validate
from livestreamer.stream import HLSStream

_url_re = re.compile("(http(s)?://(\w+\.)?antenna.gr)/minisites/[^/]+/watch/.+")
_playlist_re = re.compile("/Services/jwplayer/getplaylistJson.ashx\?mid=[^&]+&show=[^&]+")

class Antenna(Plugin):
    @classmethod
    def can_handle_url(self, url):
        return _url_re.match(url)

    def _get_streams(self):

        # Discover site root
        match = _url_re.search(self.url)
        root = match.group(1)

        # Download main URL
        res = http.get(self.url)

        # Find URL of JSON playlist
        match = _playlist_re.search(res.text)
        playlist_url = root + match.group(0)

        # Download JSON playlist
        res = http.get(playlist_url)

        # Get URL of m3u8 playlist
        res = json.loads(res.text)
        res = res['url']

        return HLSStream.parse_variant_playlist(self.session, res)

__plugin__ = Antenna
