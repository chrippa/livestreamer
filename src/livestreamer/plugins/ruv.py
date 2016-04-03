"""Plugin for RUV, the Icelandic national television."""

import re

from livestreamer.plugin import Plugin
from livestreamer.stream import HLSStream

from livestreamer.plugin.api import http

HLS_RUV_LIVE_URL = "http://ruv{0}-live.hls.adaptive.level3.net/ruv/{1}/index/stream{2}.m3u8"
HLS_RADIO_LIVE_URL = "http://sip-live.hds.adaptive.level3.net/hls-live/ruv-{0}/_definst_/live/stream1.m3u8"
HLS_SARPURINN_URL = "http://sip-ruv-vod.dcp.adaptive.level3.net/{0}/{1}{2}.mp4.m3u8"


_live_url_re = re.compile(r"""^(?:https?://)?(?:www\.)?ruv\.is/
                                (?P<stream_id>
                                    ruv/?$|
                                    ruv2/?$|
                                    ruv-2/?$|
                                    ras1/?$|
                                    ras2/?$|
                                    rondo/?$
                                )
                                /?
                           """,
                          re.VERBOSE
                         )

_sarpurinn_url_re = re.compile(r"""^(?:https?://)?(?:www\.)?ruv\.is/sarpurinn/
                                    (?P<stream_id>
                                        ruv|
                                        ruv2|
                                        ruv-2|
                                        ruv-aukaras
                                    )
                                    /
                                    [a-zA-Z0-9_-]+
                                    /
                                    [0-9]+
                                    /?
                                """,
                               re.VERBOSE
                              )

_hls_url_re = re.compile(r"""(?:http://)?sip-ruv-vod.dcp.adaptive.level3.net/
                             (?P<status>
                                 lokad|
                                 opid
                             )
                             /
                             (?P<date>[0-9]+/[0-9][0-9]/[0-9][0-9]/)?
                             (?P<id>[A-Z0-9\$_]+)
                             \.mp4\.m3u8
                          """,
                         re.VERBOSE
                        )


class Ruv(Plugin):
    @classmethod
    def can_handle_url(cls, url):
        if _live_url_re.match(url):
            return True
        else:
            return _sarpurinn_url_re.match(url)

    def __init__(self, url):
        Plugin.__init__(self, url)
        live_match = _live_url_re.match(url)

        if live_match:
            self.live = True
            self.stream_id = live_match.group("stream_id").replace("/", "").replace("-", "")

            if self.stream_id == "rondo":
                self.stream_id = "ras3"
        else:
            self.live = False

    def _get_live_streams(self):
        if self.stream_id == "ruv" or self.stream_id == "ruv2":
            qualities_hls = ["240p", "360p", "480p", "720p"]
            for i, quality_hls in enumerate(qualities_hls):
                yield quality_hls, HLSStream(
                    self.session,
                    HLS_RUV_LIVE_URL.format(self.stream_id, self.stream_id, i+1)
                )
        else:
            yield "audio", HLSStream(
                self.session,
                HLS_RADIO_LIVE_URL.format(self.stream_id)
            )

    def _get_sarpurinn_streams(self):
        res = http.get(self.url)
        match = _hls_url_re.search(res.text)

        if match:
            token = match.group("id")
            status = match.group("status")
            date = match.group("date")
            key = "576p"

            # If date was not found it is "None", but we rather want it to be an empty string
            if not date:
                date = ""

            yield key, HLSStream(
                self.session,
                HLS_SARPURINN_URL.format(status, date, token)
            )

    def _get_streams(self):
        if self.live:
            return self._get_live_streams()
        else:
            return self._get_sarpurinn_streams()


__plugin__ = Ruv
