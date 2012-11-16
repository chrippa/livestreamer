from livestreamer.options import Options
import sys

SpecialQualityWeights = {
    "live": 1080,
    "hd": 1080,
    "hq": 576,
    "hqtest": 576,
    "sd": 576,
    "sq": 360,
    "sqtest": 240,
    "iphonehigh": 240,
    "iphonelow": 180,
}

def qualityweight(quality):
    if quality.endswith("k"):
        bitrate = int(quality[:-1])

        # These calculations are very rough
        if bitrate > 2000:
            return bitrate / 3.4
        elif bitrate > 1000:
            return bitrate / 2.6
        else:
            return bitrate / 1.7
    elif quality.endswith("p"):
        return int(quality[:-1])
    elif quality in SpecialQualityWeights:
        return SpecialQualityWeights[quality]

    return 1

class Plugin(object):
    """
        A plugin can retrieve stream information from the *url* specified.
    """

    options = Options()

    def __init__(self, url):
        self.url = url
        self.logger = self.session.logger.new_module("plugin." + self.module)

    @classmethod
    def can_handle_url(cls, url):
       raise NotImplementedError

    @classmethod
    def set_option(cls, key, value):
        cls.options.set(key, value)

    @classmethod
    def get_option(cls, key):
        return cls.options.get(key)

    def get_streams(self, prot = None):
        """
            Retrieves and returns a :class:`dict` containing the streams.

            The key is the name of the stream, most commonly the quality.
            The value is a :class:`Stream` object.

            The stream with key *best* is a reference to the stream most likely
            to be of highest quality and vice versa for *worst*.
        """

        streams = self._get_streams(prot)

        best = (0, None)
        worst = (sys.maxint, None)
        for name, stream in streams.items():
            if name[-4:] == '_hls': name = name[:-4]
            weight = qualityweight(name)

            if weight > best[0]:
                best = (weight, stream)

            if weight < worst[0]:
                worst = (weight, stream)

        if best[1] is not None:
            streams["best"] = best[1]
        if worst[1] is not None:
            streams["worst"] = worst[1]

        return streams

    def _get_streams(self, prot):
        raise NotImplementedError

class PluginError(Exception):
    pass

class NoStreamsError(PluginError):
    def __init__(self, url):
        PluginError.__init__(self, ("No streams found on this URL: {0}").format(url))

class NoPluginError(PluginError):
    pass

__all__ = ["Plugin", "PluginError", "NoStreamsError", "NoPluginError"]
