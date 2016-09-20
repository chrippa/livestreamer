import re

from livestreamer.compat import urljoin
from livestreamer.plugin import Plugin
from livestreamer.plugin.api import http, validate
from livestreamer.stream import HDSStream, HLSStream, HTTPStream, RTMPStream

USER_CHANNEL_API = "/api-user/v1/{0}/channel"
VOD_VOD_API = "/api-vod/v1/vod/{0}"

_url_re = re.compile("https://www.stream.me/(?:(?:archive/[^/]+/[^/]+/(?P<vod_id>[^/?]+))|(?:(?P<channel_id>[^/?]+)))")

_channel_schema = validate.Schema(
    {
        "_embedded": {
            "streams": validate.all(
                [{
                    "active": bool,
                    "clientSlug": validate.text,
                    "manifest": validate.any(
                        "",
                        validate.url(scheme="https", path=validate.endswith(".json"))
                    )
                }],
                validate.filter(
                    lambda stream: stream["clientSlug"] == "web"
                )
            )
        }
    },
    validate.get("_embedded")
)

_vod_schema = validate.Schema(
    {
        "_embedded": {
            "vod": [
                validate.all(
                    {
                        "_links": validate.all(
                            {
                                "manifest": validate.all(
                                    {
                                        "href": validate.url(scheme="https", path=validate.endswith(".json"))
                                    },
                                    validate.get("href")
                                )
                            }
                        )
                    },
                    validate.get("_links")
                )
            ]
        }
    },
    validate.get("_embedded")
)

_manifest_schema = validate.Schema(
    {
        "formats": {
            validate.optional("mp4-hls"): {
                "manifest": validate.url(scheme="https", path=validate.endswith(".m3u8"))
            },
            validate.optional("mp4-http"): {
                "encodings": [{
                    "videoHeight": int,
                    "location": validate.url(scheme="https", path=validate.endswith(".mp4"))
                }]
            },
            validate.optional("mp4-rtmp"): {
                "encodings": [{
                    "videoHeight": int,
                    "location": validate.url(scheme="rtmp")
                }]
            }
        }
    },
    validate.get("formats")
)

class StreamMe(Plugin):
    @classmethod
    def can_handle_url(cls, url):
        return _url_re.match(url)

    def _get_streams(self):
        match = _url_re.match(self.url)
        if match.group("channel_id"):
            return self._get_live_streams(match.group("channel_id"))
        elif match.group("vod_id"):
            return self._get_vod_streams(match.group("vod_id"))

    def _get_live_streams(self, channel_id):
        res = http.get(urljoin(self.url, USER_CHANNEL_API.format(channel_id)))
        if not res:
            return

        channel = http.json(res, schema=_channel_schema)
        for stream in channel["streams"]:
            if not stream["active"]:
                continue

            for _ in self._create_streams(stream["manifest"]):
                yield _

    def _get_vod_streams(self, vod_id):
        res = http.get(urljoin(self.url, VOD_VOD_API.format(vod_id)))
        if not res:
            return

        vods = http.json(res, schema=_vod_schema)
        for vod in vods["vod"]:
            for _ in self._create_streams(vod["manifest"]):
                yield _

    def _create_streams(self, manifest):
            res = http.get(manifest)
            formats = http.json(res, schema=_manifest_schema)
            for stream_format in formats:
                if stream_format == "mp4-hls":
                    streams = HLSStream.parse_variant_playlist(self.session, formats[stream_format]["manifest"])
                    for _ in streams.items():
                        yield _
                elif stream_format == "mp4-http":
                    streams = self._create_http_streams(formats[stream_format]["encodings"])
                    for _ in streams:
                        yield _
                elif stream_format == "mp4-rtmp":
                    streams = self._create_rtmp_streams(formats[stream_format]["encodings"])
                    for _ in streams:
                        yield _

    def _create_http_streams(self, encodings):
        for encoding in encodings:
            yield "{0}p".format(encoding["videoHeight"]), HTTPStream(self.session, encoding["location"])

    def _create_rtmp_streams(self, encodings):
        for encoding in encodings:
            params = {
                "rtmp": encoding["location"],
                "pageUrl": self.url
            }
            yield "{0}p".format(encoding["videoHeight"]), RTMPStream(self.session, params)

__plugin__ = StreamMe
