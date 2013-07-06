from livestreamer.compat import str, bytes, urlparse
from livestreamer.exceptions import PluginError, NoStreamsError
from livestreamer.plugin import Plugin
from livestreamer.stream import RTMPStream
from livestreamer.utils import urlget, verifyjson, res_json

import re

class DailyMotion(Plugin):
    QualityMap = {
        "ld": "240p",
        "sd": "360p",
        "hq": "480p",
        "hd720": "720p",
        "hd1080": "1080p",
        "custom": "live"
    }

    StreamInfoURL = "http://www.dailymotion.com/sequence/full/{0}"
    MetadataURL = "https://api.dailymotion.com/video/{0}"

    @classmethod
    def can_handle_url(self, url):
        # valid urls are of the form dailymotion.com/video/[a-z]{5}.*
        # but we make "video/" optional and allow for dai.ly as shortcut
        # Gamecreds uses Dailymotion as backend so we support it through this plugin.
        return ("dailymotion.com" in url) or ("dai.ly" in url) or ("video.gamecreds.com" in url)

    def _check_channel_live(self, channelname):
        url = self.MetadataURL.format(channelname)
        res = urlget(url, params=dict(fields="mode"))
        json = res_json(res)

        if not isinstance(json, dict):
            raise PluginError("Invalid JSON response")

        mode = verifyjson(json, "mode")

        return mode == "live"

    def _get_channel_name(self, url):
        name = None
        if ("dailymotion.com" in url) or ("dai.ly" in url):
            rpart = urlparse(url).path.rstrip("/").rpartition("/")[-1].lower()
            name = re.sub("_.*", "", rpart)
        elif ("video.gamecreds.com" in url):
            res = urlget(url)
            # The HTML is broken (unclosed meta tags) and minidom fails to parse.
            # Since we are not manipulating the DOM, we get away with a simple grep instead of fixing it.
            match = re.search("<meta property=\"og:video\" content=\"http://www.dailymotion.com/swf/video/([a-z0-9]{6})", res.text)
            if match: name = match.group(1)

        return name

    def _get_node_by_name(self, parent, name):
        res = None
        for node in parent:
            if node["name"] == name:
                res = node
                break

        return res

    def _get_rtmp_streams(self, channelname):
        self.logger.debug("Fetching stream info")

        url = self.StreamInfoURL.format(channelname)

        self.logger.debug("JSON data url: {0}", url)

        res = urlget(url)
        json = res_json(res)

        if not isinstance(json, dict):
            raise PluginError("Invalid JSON response")

        if len(json) == 0:
            raise PluginError("JSON is empty")

        # This is ugly, not sure how to fix it.
        back_json_node = json["sequence"][0]["layerList"][0]
        if back_json_node["name"] != "background":
            raise PluginError("JSON data has unexpected structure")

        rep_node = self._get_node_by_name(back_json_node["sequenceList"], "reporting")["layerList"]
        main_node = self._get_node_by_name(back_json_node["sequenceList"], "main")["layerList"]

        if not (rep_node and main_node):
            raise PluginError("Error parsing stream RTMP url")

        swfurl = self._get_node_by_name(rep_node, "reporting")["param"]["extraParams"]["videoSwfURL"]
        feeds_params = self._get_node_by_name(main_node, "video")["param"]

        if not (swfurl and feeds_params):
            raise PluginError("Error parsing stream RTMP url")

        streams = {}

        # Different feed qualities are available are a dict under "live"
        # In some cases where there's only 1 quality available,
        # it seems the "live" is absent. We use the single stream available
        # under the "customURL" key.

        if "mode" in feeds_params and feeds_params["mode"] == "live":

            for key, quality in self.QualityMap.items():

                url_key = '{0}URL'.format(key)
                if url_key not in feeds_params:
                    continue
                else:
                    url = feeds_params[url_key]

                info = {}

                try:
                    res = urlget(url, exception=IOError)
                except IOError:
                    continue

                rtmpurl = res.text
                stream = RTMPStream(self.session, {
                    "rtmp": rtmpurl,
                    "swfVfy": swfurl,
                    "live": True
                })
                self.logger.debug("Adding URL: {0}", rtmpurl)

                streams[quality] = stream

        return streams

    def _get_streams(self):
        channelname = self._get_channel_name(self.url)

        if not channelname:
            raise NoStreamsError(self.url)

        if not self._check_channel_live(channelname):
            raise NoStreamsError(self.url)

        return self._get_rtmp_streams(channelname)


__plugin__ = DailyMotion


# vim: expandtab tabstop=4 shiftwidth=4
