import re

from collections import defaultdict, namedtuple
from functools import partial
from io import BytesIO
from random import randint
from time import sleep

from livestreamer.compat import urlparse, urljoin, range
from livestreamer.exceptions import StreamError, PluginError, NoStreamsError
from livestreamer.options import Options
from livestreamer.plugin import Plugin
from livestreamer.plugin.api import http
from livestreamer.stream import RTMPStream, HLSStream, HTTPStream, Stream
from livestreamer.stream.flvconcat import FLVTagConcat
from livestreamer.stream.segmented import (SegmentedStreamReader,
                                           SegmentedStreamWriter,
                                           SegmentedStreamWorker)
from livestreamer.packages.flashmedia import AMFPacket, AMFError

try:
    import librtmp
    HAS_LIBRTMP = True
except ImportError:
    HAS_LIBRTMP = False


CDN_KEYS = ["cdnStreamUrl", "cdnStreamName"]
PROVIDER_KEYS = ["streams", "name", "url"]

AMF_URL = "http://cgw.ustream.tv/Viewer/getStream/1/{0}.amf"
HLS_PLAYLIST_URL = "http://iphone-streaming.ustream.tv/uhls/{0}/streams/live/iphone/playlist.m3u8"
RECORDED_URL = "http://tcdn.ustream.tv/video/{0}"
RECORDED_URL_PATTERN = r"^(http(s)?://)?(www\.)?ustream.tv/recorded/(?P<video_id>\d+)"
RTMP_URL = "rtmp://r{0}.1.{1}.channel.live.ums.ustream.tv:80/ustream"
SWF_URL = "http://static-cdn1.ustream.tv/swf/live/viewer.rsl:505.swf"


Chunk = namedtuple("Chunk", "num url offset")


def valid_cdn(item):
    name, cdn = item
    return all(cdn.get(key) for key in CDN_KEYS)


def valid_provider(info):
    return isinstance(info, dict) and all(info.get(key) for key in PROVIDER_KEYS)


def validate_module_info(result):
    if (result and isinstance(result, list) and result[0].get("stream")):
        return result[0]


def create_ums_connection(app, media_id, page_url, password,
                          exception=PluginError):
    url = RTMP_URL.format(randint(0, 0xffffff), media_id)
    params = dict(application=app, media=str(media_id), password=password)
    conn = librtmp.RTMP(url, connect_data=params,
                        swfurl=SWF_URL, pageurl=page_url)

    try:
        conn.connect()
    except librtmp.RTMPError:
        raise exception("Failed to connect to RTMP server")

    return conn


class UHSStreamWriter(SegmentedStreamWriter):
    def __init__(self, *args, **kwargs):
        SegmentedStreamWriter.__init__(self, *args, **kwargs)

        self.concater = FLVTagConcat(flatten_timestamps=True,
                                     sync_headers=True,
                                     has_audio=not self.stream.video_only)

    def open_chunk(self, chunk, retries=3):
        if not retries or self.closed:
            return

        try:
            params = {}
            if chunk.offset:
                params["start"] = chunk.offset

            return http.get(chunk.url,  params=params, timeout=10,
                            exception=StreamError)
        except StreamError as err:
            self.logger.error("Failed to open chunk {0}: {1}", chunk.num, err)
            return self.open_chunk(chunk, retries - 1)

    def write(self, chunk, chunk_size=8192):
        res = self.open_chunk(chunk)
        if not res:
            return

        try:
            for data in self.concater.iter_chunks(buf=res.content,
                                                  skip_header=not chunk.offset):
                self.reader.buffer.write(data)

                if self.closed:
                    break
            else:
                self.logger.debug("Download of chunk {0} complete", chunk.num)
        except IOError as err:
            self.logger.error("Failed to read chunk {0}: {1}", chunk.num, err)


class UHSStreamWorker(SegmentedStreamWorker):
    def __init__(self, *args, **kwargs):
        SegmentedStreamWorker.__init__(self, *args, **kwargs)

        self.chunk_ranges = {}
        self.chunk_id = None
        self.chunk_id_max = None
        self.chunks = []
        self.filename_format = ""
        self.module_info_reload_time = 2
        self.process_module_info()

    def fetch_module_info(self):
        self.logger.debug("Fetching module info")
        conn = create_ums_connection("channel",
                                     self.stream.channel_id,
                                     self.stream.page_url,
                                     self.stream.password,
                                     exception=StreamError)

        try:
            result = conn.process_packets(invoked_method="moduleInfo",
                                          timeout=10)
        except (IOError, librtmp.RTMPError) as err:
            raise StreamError("Failed to get module info: {0}".format(err))
        finally:
            conn.close()

        return validate_module_info(result)

    def process_module_info(self):
        if self.closed:
            return

        result = self.fetch_module_info()
        if not result:
            return

        providers = result.get("stream")
        if providers == "offline":
            self.logger.debug("Stream went offline")
            self.close()
            return
        elif not isinstance(providers, list):
            return

        for provider in filter(valid_provider, providers):
            if provider.get("name") == self.stream.provider:
                break
        else:
            return

        try:
            stream = provider.get("streams")[self.stream.stream_index]
        except IndexError:
            self.logger.error("Stream index not in result")
            return

        filename_format = stream.get("streamName").replace("%", "%s")
        filename_format = urljoin(provider.get("url"), filename_format)

        self.filename_format = filename_format
        self.update_chunk_info(stream)

    def update_chunk_info(self, result):
        chunk_range = result.get("chunkRange")

        if not chunk_range:
            return

        chunk_id = int(result.get("chunkId"))
        chunk_offset = int(result.get("offset"))
        chunk_range = dict(map(partial(map, int), chunk_range.items()))

        self.chunk_ranges.update(chunk_range)
        self.chunk_id_min = sorted(chunk_range)[0]
        self.chunk_id_max = int(result.get("chunkId"))
        self.chunks = [Chunk(i, self.format_chunk_url(i),
                             not self.chunk_id and i == chunk_id and chunk_offset)
                       for i in range(self.chunk_id_min, self.chunk_id_max + 1)]

        if self.chunk_id is None and self.chunks:
            self.chunk_id = chunk_id

    def format_chunk_url(self, chunk_id):
        chunk_hash = ""
        for chunk_start in sorted(self.chunk_ranges):
            if chunk_id >= chunk_start:
                chunk_hash = self.chunk_ranges[chunk_start]

        return self.filename_format % (chunk_id, chunk_hash)

    def valid_chunk(self, chunk):
        return self.chunk_id and chunk.num >= self.chunk_id

    def iter_segments(self):
        while not self.closed:
            for chunk in filter(self.valid_chunk, self.chunks):
                self.logger.debug("Adding chunk {0} to queue", chunk.num)
                yield chunk

                # End of stream
                if self.closed:
                    return

                self.chunk_id = chunk.num + 1

            if self.wait(self.module_info_reload_time):
                try:
                    self.process_module_info()
                except StreamError as err:
                    self.logger.warning("Failed to process module info: {0}", err)


class UHSStreamReader(SegmentedStreamReader):
    __worker__ = UHSStreamWorker
    __writer__ = UHSStreamWriter

    def __init__(self, stream, *args, **kwargs):
        self.logger = stream.session.logger.new_module("stream.uhs")

        SegmentedStreamReader.__init__(self, stream, *args, **kwargs)


class UHSStream(Stream):
    __shortname__ = "uhs"

    def __init__(self, session, channel_id, page_url, provider,
                 stream_index, password="", video_only=False):
        Stream.__init__(self, session)

        self.channel_id = channel_id
        self.page_url = page_url
        self.provider = provider
        self.stream_index = stream_index
        self.password = password
        self.video_only = video_only

    def __repr__(self):
        return ("<UHSStream({0!r}, {1!r}, "
                "{2!r}, {3!r}, {4!r}, {5!r})>").format(self.channel_id,
                                                self.page_url,
                                                self.provider,
                                                self.stream_index,
                                                self.password,
                                                self.video_only)

    def __json__(self):
        return dict(channel_id=self.channel_id,
                    page_url=self.page_url,
                    provider=self.provider,
                    stream_index=self.stream_index,
                    password=self.password,
                    video_only=self.video_only,
                    **Stream.__json__(self))

    def open(self):
        reader = UHSStreamReader(self)
        reader.open()

        return reader


class UStreamTV(Plugin):
    options = Options({
        "password": "",
        "video_only": False
    })

    @classmethod
    def can_handle_url(cls, url):
        return "ustream.tv" in url

    @classmethod
    def stream_weight(cls, stream):
        match = re.match("mobile_(\w+)", stream)
        if match:
            weight, group = Plugin.stream_weight(match.group(1))
            weight -= 1
            group = "mobile_ustream"
        elif stream == "recorded":
            weight, group = 720, "ustream"
        else:
            weight, group = Plugin.stream_weight(stream)

        return weight, group

    def _get_channel_id(self, url):
        match = re.search("ustream.tv/embed/(\d+)", url)
        if match:
            return int(match.group(1))

        match = re.search("\"cid\":(\d+)", http.get(url).text)
        if match:
            return int(match.group(1))

    def _get_hls_streams(self, wait_for_transcode=False):
        # HLS streams are created on demand, so we may have to wait
        # for a transcode to be started.
        attempts = wait_for_transcode and 10 or 1
        playlist_url = HLS_PLAYLIST_URL.format(self.channel_id)
        streams = {}
        while attempts and not streams:
            try:
                streams = HLSStream.parse_variant_playlist(self.session,
                                                           playlist_url,
                                                           nameprefix="mobile_")
            except IOError:
                # Channel is probably offline
                break

            attempts -= 1
            sleep(3)

        return streams

    def _create_rtmp_stream(self, cdn, stream_name):
        parsed = urlparse(cdn)
        options = dict(rtmp=cdn, app=parsed.path[1:],
                       playpath=stream_name, pageUrl=self.url,
                       swfUrl=SWF_URL, live=True)

        return RTMPStream(self.session, options)

    def _get_module_info(self, app, media_id, password=""):
        self.logger.debug("Waiting for moduleInfo invoke")
        conn = create_ums_connection(app, media_id, self.url, password)

        attempts = 3
        while conn.connected and attempts:
            try:
                result = conn.process_packets(invoked_method="moduleInfo",
                                              timeout=30)
            except (IOError, librtmp.RTMPError) as err:
                raise PluginError("Failed to get stream info: {0}".format(err))

            result = validate_module_info(result)
            if result:
                break
            else:
                attempts -= 1

        conn.close()

        return result

    def _get_streams_from_rtmp(self):
        password = self.options.get("password")
        video_only = self.options.get("video_only")
        module_info = self._get_module_info("channel", self.channel_id,
                                            password)
        if not module_info:
            raise NoStreamsError(self.url)

        providers = module_info.get("stream")
        if providers == "offline":
            raise NoStreamsError(self.url)
        elif not isinstance(providers, list):
            raise PluginError("Invalid stream info: {0}".format(providers))

        streams = {}
        for provider in filter(valid_provider, providers):
            provider_url = provider.get("url")
            provider_name = provider.get("name")
            provider_streams = provider.get("streams")

            for stream_index, stream_info in enumerate(provider_streams):
                stream = None
                stream_height = int(stream_info.get("height", 0))
                stream_name = stream_info.get("description")

                if not stream_name:
                    if stream_height:
                        if not stream_info.get("isTranscoded"):
                            stream_name = "{0}p+".format(stream_height)
                        else:
                            stream_name = "{0}p".format(stream_height)
                    else:
                        stream_name = "live"

                if stream_name in streams:
                    provider_name_clean = provider_name.replace("uhs_", "")
                    stream_name += "_alt_{0}".format(provider_name_clean)

                if provider_name.startswith("uhs_"):
                    stream = UHSStream(self.session, self.channel_id,
                                       self.url, provider_name,
                                       stream_index, password, video_only)
                elif (provider_url.startswith("rtmp") and
                      RTMPStream.is_usable(self.session)):
                        playpath = stream_info.get("streamName")
                        stream = self._create_rtmp_stream(provider_url,
                                                          playpath)

                if stream:
                    streams[stream_name] = stream

        return streams

    def _get_streams_from_amf(self):
        if not RTMPStream.is_usable(self.session):
            raise NoStreamsError(self.url)

        res = http.get(AMF_URL.format(self.channel_id))

        try:
            packet = AMFPacket.deserialize(BytesIO(res.content))
        except (IOError, AMFError) as err:
            raise PluginError("Failed to parse AMF packet: {0}".format(err))

        for message in packet.messages:
            if message.target_uri == "/1/onResult":
                result = message.value
                break
        else:
            raise PluginError("No result found in AMF packet")

        streams = {}
        stream_name = result.get("streamName")
        if stream_name:
            cdn = result.get("cdnUrl") or result.get("fmsUrl")
            if cdn:
                stream = self._create_rtmp_stream(cdn, stream_name)

                if "videoCodec" in result and result["videoCodec"]["height"] > 0:
                    stream_name = "{0}p".format(int(result["videoCodec"]["height"]))
                else:
                    stream_name = "live"

                streams[stream_name] = stream
            else:
                self.logger.warning("Missing cdnUrl and fmsUrl from result")

        stream_versions = result.get("streamVersions")
        if stream_versions:
            for version, info in stream_versions.items():
                stream_version_cdn = info.get("streamVersionCdn", {})

                for name, cdn in filter(valid_cdn, stream_version_cdn.items()):
                    stream = self._create_rtmp_stream(cdn["cdnStreamUrl"],
                                                      cdn["cdnStreamName"])
                    stream_name = "live_alt_{0}".format(name)
                    streams[stream_name] = stream

        return streams

    def _get_live_streams(self):
        self.channel_id = self._get_channel_id(self.url)

        if not self.channel_id:
            raise NoStreamsError(self.url)

        streams = defaultdict(list)

        if not RTMPStream.is_usable(self.session):
            self.logger.warning("rtmpdump is not usable. "
                                "Not all streams may be available.")

        if HAS_LIBRTMP:
            desktop_streams = self._get_streams_from_rtmp
        else:
            self.logger.warning("python-librtmp is not installed. "
                                "Not all streams may be available.")
            desktop_streams = self._get_streams_from_amf

        try:
            for name, stream in desktop_streams().items():
                streams[name].append(stream)
        except PluginError as err:
            self.logger.error("Unable to fetch desktop streams: {0}", err)
        except NoStreamsError:
            pass

        try:
            mobile_streams = self._get_hls_streams(wait_for_transcode=not streams)
            for name, stream in mobile_streams.items():
                streams[name].append(stream)
        except PluginError as err:
            self.logger.error("Unable to fetch mobile streams: {0}", err)
        except NoStreamsError:
            pass

        return streams

    def _get_recorded_streams(self, video_id):
        streams = {}

        if HAS_LIBRTMP:
            module_info = self._get_module_info("recorded", video_id)
            if not module_info:
                raise NoStreamsError(self.url)

            providers = module_info.get("stream")
            if not isinstance(providers, list):
                raise PluginError("Invalid stream info: {0}".format(providers))

            for provider in providers:
                base_url = provider.get("url")
                for stream_info in provider.get("streams"):
                    bitrate = int(stream_info.get("bitrate", 0))
                    stream_name = (bitrate > 0 and "{0}k".format(bitrate) or
                                   "recorded")

                    if stream_name in streams:
                        stream_name += "_alt"

                    url = stream_info.get("streamName")
                    if base_url:
                        url = base_url + url

                    if url.startswith("http"):
                        streams[stream_name] = HTTPStream(self.session, url)
                    elif url.startswith("rtmp"):
                        params = dict(rtmp=url, pageUrl=self.url)
                        streams[stream_name] = RTMPStream(self.session, params)

        else:
            self.logger.warning("The proper API could not be used without "
                                "python-librtmp installed. Stream URL may be "
                                "incorrect.")

            url = RECORDED_URL.format(video_id)
            random_hash = "{0:02x}{1:02x}".format(randint(0, 255),
                                                  randint(0, 255))
            params = dict(hash=random_hash)
            stream = HTTPStream(self.session, url, params=params)
            streams["recorded"] = stream

        return streams

    def _get_streams(self):
        recorded = re.match(RECORDED_URL_PATTERN, self.url)
        if recorded:
            return self._get_recorded_streams(recorded.group("video_id"))
        else:
            return self._get_live_streams()

__plugin__ = UStreamTV
