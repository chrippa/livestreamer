"""Plugin for media.ccc.de

Media.ccc.de is a distribution platform for multimedia files provided by the
Chaos Computer Club. It provides a wide variety of video and audio material
in native formats.

Some CCC related events like the Chaos Communication Congress are live
streamed on streaming.media.ccc.de.

Supports:
    - http://media.ccc.de (vod)
    - http://streaming.media.ccc.de (livestreaming)

Limitations:
    * streaming.media.ccc.de:
        - only HLS and audio only (opus and mp3) live streams are supported

    * media.ccc.de
        - only mp4 and audio only (opus and mp3) recordings are supported
"""

import re
import requests
import json

from livestreamer.plugin import Plugin, PluginError
from livestreamer.stream import HTTPStream, HLSStream

API_URL_MEDIA           = "https://api.media.ccc.de"
API_URL_STREAMING_MEDIA = "http://streaming.media.ccc.de/streams/v1.json"

# http(s)://media.ccc.de/path/to/talk.html
_url_media_re           = re.compile("(?P<scheme>http|https)"
                                     ":\/\/"
                                     "(?P<server>media\.ccc\.de)"
                                     "\/")
# http://streaming.media.ccc.de/room/
_url_streaming_media_re = re.compile("(?P<scheme>http)"
                                     ":\/\/"
                                     "(?P<server>streaming\.media\.ccc\.de)"
                                     "\/"
                                     "(?P<room>.*)"
                                     "\/.*")

def get_event_id(url):
    """Extract event id from talk html page.

    Raises :exc:`PluginError` on failure.

    :param url: talk URL

    """
    match = re.search(r"{event_id:\s(?P<event_id>\d+),.*}", get_page(url))

    try:
        event_id = int(match.group('event_id'))
    except:
        raise PluginError("Failed to get event id from URL.")

    return event_id

def get_page(url):
    """Fetch page for given URL.

    :param url: URL to fetch

    """
    page = requests.get(url)

    return page.text

def create_json_object(json_string):
    """Loads json string and returned Python object.

    Raises :exc:`PluginError` on failure.

    :param json_string: json as string

    """
    try:
        json_object = json.loads(json_string)
    except:
        raise PluginError("Could not parse json from API.")

    return json_object

def parse_media_json(json_string):
    """Expose available

    :param json_string: json as string

    """
    json_object = create_json_object(json_string)

    recordings = {}
    for recording in json_object['recordings']:
        match       = re.search(r".*\/(?P<format>.*)", recording['mime_type'])
        file_format = match.group('format')

        if recording['mime_type'] == 'vnd.voc/mp4-web' or\
            recording['display_mime_type'] == 'video/webm':
            continue
        elif recording['mime_type'] == 'vnd.voc/h264-hd':
            name = "1080p"
        elif recording['mime_type'] == 'vnd.voc/h264-lq':
            name = "420p"
        elif re.match(r"audio", recording['display_mime_type']):
            name = "audio_%s" % file_format
        else:
            if recording['hd'] == 'True':
                name = "1080p"
            else:
                name = "420p"

        recordings[name] = recording['recording_url']

    return recordings

def parse_streaming_media_json(json_string, room_from_url):
    """Filter all availabe live streams for given json and room name.

    API-Doku: https://github.com/voc/streaming-website#json-api

    :param json_string: json as string
    :param room_from_url:

    """
    json_object = create_json_object(json_string)

    streams = {}
    for group in json_object:
        for room in group['rooms']:
            # only consider to requested room
            match = _url_streaming_media_re.match(room['link'])
            if not match.group('room') == room_from_url:
                continue

            for stream in room['streams']:
                # get stream language
                if stream['isTranslated'] == False:
                    language = 'native'
                else:
                    language = 'translated'

                # get available hls stream urls
                if 'hls' in stream['urls'].keys():
                    stream_url = stream['urls']['hls']['url']
                    name = None
                    # native HLS streams are announced as
                    # ${height}p and (hd|sd)_native_${height}p
                    if language == 'native':
                        name          = "%sp" % stream['videoSize'][-1]
                        long_name     = "hls_%s_%sp" % ("native",\
                                                        stream['videoSize'][-1])
                        streams[name]      = stream_url
                        streams[long_name] = stream_url
                    elif language == 'translated':
                        long_name     = "hls_%s_%sp" % ("translated",\
                                                        stream['videoSize'][-1])
                        streams[long_name] = stream_url

                # get available audio only mpeg urls
                if 'mp3' in stream['urls'].keys():
                    stream_url    = stream['urls']['mp3']['url']
                    name          = "audio_%s_mpeg" % language
                    streams[name] = stream_url

                # get available audio only opus urls
                if 'opus' in stream['urls'].keys():
                    stream_url    = stream['urls']['opus']['url']
                    name          = "audio_%s_opus" % language
                    streams[name] = stream_url

    return streams


class media_ccc_de(Plugin):
    @classmethod
    def can_handle_url(self, url):
        return _url_media_re.search(url) or _url_streaming_media_re.search(url)

    def _get_streams(self):
        streams = {}

        # streaming.media.ccc.de
        match = _url_streaming_media_re.match(self.url)
        if match:
            query_url    = API_URL_STREAMING_MEDIA
            live_streams = parse_streaming_media_json(get_page(query_url),\
                                                      match.group('room'))

            for stream_name in live_streams.keys():
                if re.search(r"m3u8", live_streams[stream_name]):
                    try:
                        streams[stream_name] = HLSStream(self.session,\
                                                live_streams[stream_name])
                    except IOError as err:
                        self.logger.warning("Failed to extract HLS streams: "
                                            "{0}", err)
                else:
                    streams[stream_name] = HTTPStream(self.session,\
                                                live_streams[stream_name])

        # media.ccc.de
        elif _url_media_re.search(self.url):
            event_id   = get_event_id(self.url)
            query_url  = "%s/public/events/%i" % (API_URL_MEDIA, event_id)
            recordings = parse_media_json(get_page(query_url))

            for name in recordings.keys():
                stream_url    = recordings[name]
                streams[name] = HTTPStream(self.session, stream_url)

        if not streams:
            raise PluginError("This plugin does not support your "
                              "selected video.")

        return streams

__plugin__ = media_ccc_de
