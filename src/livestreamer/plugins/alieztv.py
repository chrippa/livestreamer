import re

from os.path import splitext

from livestreamer.compat import urlparse, unquote
from livestreamer.plugin import Plugin
from livestreamer.plugin.api import http, validate
from livestreamer.stream import HTTPSelect, RTMPStream

_url_re = re.compile("""
    http(s)?://(\w+\.)?aliez.tv
    (?:
        /live/[^/]+
    )?
    (?:
        /video/\d+/[^/]+
    )?
""", re.VERBOSE)
_file_re = re.compile("\"?file\"?:\s+['\"]([^'\"]+)['\"]")
_swf_url_re = re.compile("swfobject.embedSWF\(\"([^\"]+)\",")

_schema = validate.Schema(
    validate.union({
        "urls": validate.all(
            validate.transform(_file_re.findall),
            validate.map(unquote),
            [validate.url()]
        ),
        "swf": validate.all(
            validate.transform(_swf_url_re.search),
            validate.any(
                None,
                validate.all(
                    validate.get(1),
                    validate.url(
                        scheme="http",
                        path=validate.endswith("swf")
                    )
                )
            )
        )
    })
)


class Aliez(Plugin):
    @classmethod
    def can_handle_url(self, url):
        return _url_re.match(url)

    def _get_streams(self):
        res = http.get(self.url, schema=_schema)
        streams = {}
        for url in res["urls"]:
            parsed = urlparse(url)
            if parsed.scheme.startswith("rtmp"):
                params = {
                    "rtmp": url,
                    "pageUrl": self.url,
                    "live": True
                }
                if res["swf"]:
                    params["swfVfy"] = res["swf"]

                stream = RTMPStream(self.session, params)
                streams["live"] = stream
            elif parsed.scheme.startswith("http"):
                name = splitext(parsed.path)[1][1:]
                stream = HTTPSelect(self.session, url)
                streams[name] = stream

        return streams

__plugin__ = Aliez
