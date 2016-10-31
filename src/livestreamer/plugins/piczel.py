import re

from livestreamer.plugin import Plugin
from livestreamer.plugin.api import http, validate
from livestreamer.stream import RTMPStream, HLSStream

STREAMS_URL = "https://piczel.tv:3000/streams/{0}?&page=1&sfw=false&live_only=true"
HLS_URL = "https://5810b93fdf674.streamlock.net:1936/live/{0}/playlist.m3u8"
RTMP_URL = "rtmp://piczel.tv:1935/live/{0}"

_url_re = re.compile("https://piczel.tv/watch/(\w+)")

_streams_schema = validate.Schema(
	{
		"type": validate.text,
		"data": [
			{
				"id": int,
				"live": bool
			}
		]
	}
)

class Piczel(Plugin):
	@classmethod
	def can_handle_url(cls, url):
		return _url_re.match(url)

	def _get_streams(self):
		match = _url_re.match(self.url)
		if not match:
			return

		res = http.get(STREAMS_URL.format(match.group(1)))
		streams = http.json(res, schema=_streams_schema)
		if streams["type"] != "stream":
			return

		stream_info = streams["data"][0]
		if not stream_info["live"]:
			return

		streams = {}

		try:
			streams.update(HLSStream.parse_variant_playlist(self.session, HLS_URL.format(stream_info["id"])))
		except IOError as e:
			# fix for hosted offline streams
			if "404 Client Error" in str(e):
				return
			raise

		streams["rtmp"] = RTMPStream(self.session, {
			"rtmp": RTMP_URL.format(stream_info["id"]),
			"pageUrl": self.url,
			"live": True
		})

		return streams

__plugin__ = Piczel
