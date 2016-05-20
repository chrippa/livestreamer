from collections import defaultdict, namedtuple

from concurrent import futures
from concurrent.futures import ThreadPoolExecutor
from ..compat import queue
from ..buffers import RingBuffer
from .streaming_response import StreamingResponse
from .threadpool_manager import ThreadPoolManager

try:
    from Crypto.Cipher import AES
    import struct

    def num_to_iv(n):
        return struct.pack(">8xq", n)

    CAN_DECRYPT = True
except ImportError:
    CAN_DECRYPT = False

from . import hls_playlist
from .http import HTTPStream
from .segmented import (SegmentedStreamReader,
                        SegmentedStreamWriter,
                        SegmentedStreamWorker, SeekCoordinator)
from ..exceptions import StreamError, MailboxClosed

_Sequence = namedtuple("Sequence", "num segment")


# Add a mutable fields to the Sequence tuple so we can add seek meta data
# when appropriate
class Sequence(_Sequence):
    def __init__(self, *args, **kwargs):
        super(Sequence, self).__init__()
        self.segment_offset = None
        self.segment_length = None
        self.group_id = 0
        self.is_last = False


class HLSStreamWriter(SegmentedStreamWriter):
    def __init__(self, reader, *args, **kwargs):
        options = reader.stream.session.options
        kwargs["retries"] = options.get("hls-segment-attempts")
        kwargs["threads"] = options.get("hls-segment-threads")
        kwargs["timeout"] = options.get("hls-segment-timeout")
        SegmentedStreamWriter.__init__(self, reader, *args, **kwargs)

        self.executor = ThreadPoolManager(kwargs["threads"])
        self.byterange_offsets = defaultdict(int)
        self.key_data = None
        self.key_uri = None
        self.active_group = 0
        self.msg_broker = self.stream.msg_broker
        self.mailbox = self.msg_broker.register("writer")
        self.mailbox.subscribe("seek_event")

    def create_decryptor(self, key, sequence):
        if key.method != "AES-128":
            raise StreamError("Unable to decrypt cipher {0}", key.method)

        if not key.uri:
            raise StreamError("Missing URI to decryption key")

        if self.key_uri != key.uri:
            res = self.session.http.get(key.uri, exception=StreamError,
                                        **self.reader.request_params)
            self.key_data = res.content
            self.key_uri = key.uri

        iv = key.iv or num_to_iv(sequence)

        # Pad IV if needed
        iv = b"\x00" * (16 - len(iv)) + iv

        return AES.new(self.key_data, AES.MODE_CBC, iv)

    def create_request_params(self, sequence):
        request_params = dict(self.reader.request_params)
        headers = request_params.pop("headers", {})

        if sequence.segment.byterange:
            bytes_start = self.byterange_offsets[sequence.segment.uri]
            if sequence.segment.byterange.offset is not None:
                bytes_start = sequence.segment.byterange.offset

            bytes_len = max(sequence.segment.byterange.range - 1, 0)
            bytes_end = bytes_start + bytes_len
            headers["Range"] = "bytes={0}-{1}".format(bytes_start, bytes_end)
            self.byterange_offsets[sequence.segment.uri] = bytes_end + 1

        request_params["headers"] = headers

        return request_params

    def fetch(self, sequence, retries=None):
        shutdown_event = self.executor.running_group != sequence.group_id
        if shutdown_event or self.closed:
            return

        return StreamingResponse(self.session, self.executor,
                                 sequence.segment.uri,
                                 group_id=sequence.group_id,
                                 request_params=self.reader.request_params,
                                 retries=retries,
                                 download_timeout=self.timeout,
                                 read_timeout=self.reader.timeout,
                                 ).start()

    def write(self, sequence, res, chunk_size=8192):
        decryptor = None
        if sequence.segment.key and sequence.segment.key.method != "NONE":
            try:
                decryptor = self.create_decryptor(sequence.segment.key,
                                                  sequence.num)
            except StreamError as err:
                self.logger.error("Failed to create decryptor: {0}", err)
                self.close()
                return

        for chunk in res.iter_content(chunk_size):
            if decryptor:
                # If the input data is not a multiple of 16, cut off any garbage
                garbage_len = len(chunk) % 16
                if garbage_len:
                    self.logger.debug("Cutting off {0} bytes of garbage "
                                      "before decrypting", garbage_len)
                    chunk = decryptor.decrypt(chunk[:-(garbage_len)])
                else:
                    chunk = decryptor.decrypt(chunk)
    
            self.reader.buffer.write(chunk)

        if res.consumed:
            self.logger.debug("Streaming of segment {0}, group id {1} to "
                              "buffer complete",
                              sequence.num, sequence.group_id)
        else:
            self.logger.debug("Streaming of segment {0}, group id {1} to "
                              "buffer cancelled",
                              sequence.num, sequence.group_id)

        try:
            # End of stream, if we should idle then do so
            if sequence.is_last:
                should_idle_msg = self.mailbox.get("should_idle", block=True)
                should_idle = should_idle_msg.data
                if should_idle:
                    # Close the buffer so that reads don't block and stream
                    # terminates once the last byte has been read
                    self.reader.buffer.close()
                    self.logger.debug("End of stream reached, waiting for seek or close")

                    # Idle waiting on seek events until shutdown
                    self.mailbox.wait_on_msg("seek_event", leave_msg=True)

            # Handle seek events
            with self.mailbox.get("seek_event") as seek_event:
                if seek_event:
                    # Defer to seek coordinator
                    self.mailbox.send("waiting on restart", target="seek_coordinator")
                    self.logger.debug("Writer thread paused, waiting on seek coordinator")
                    self.mailbox.wait_on_msg("restart")
                    self.logger.debug("Writer thread resumed")

        except MailboxClosed:
            self.close()

    def close(self):
        if not self.closed:
            super(HLSStreamWriter, self).close()
            self.mailbox.close()


class HLSStreamWorker(SegmentedStreamWorker):
    def __init__(self, *args, **kwargs):
        SegmentedStreamWorker.__init__(self, *args, **kwargs)

        self.content_type = None
        self.supports_seek = False
        self.complete_length = None
        self.duration = None
        self.playlist_changed = False
        self.playlist_end = None
        self.playlist_sequence = -1
        self.playlist_sequences = []
        self.playlist_reload_time = 15
        self.live_edge = self.session.options.get("hls-live-edge")
        self.msg_broker = self.stream.msg_broker
        self.mailbox = self.msg_broker.register("worker")
        self.mailbox.subscribe("seek_event")

        self.reload_playlist()

    def reload_playlist(self):
        if self.closed:
            return

        self.reader.buffer.wait_free()
        self.logger.debug("Reloading playlist")
        res = self.session.http.get(self.stream.url,
                                    exception=StreamError,
                                    **self.reader.request_params)

        try:
            playlist = hls_playlist.load(res.text, res.url)
        except ValueError as err:
            raise StreamError(err)

        if playlist.is_master:
            raise StreamError("Attempted to play a variant playlist, use "
                              "'hlsvariant://{0}' instead".format(self.stream.url))

        if playlist.iframes_only:
            raise StreamError("Streams containing I-frames only is not playable")

        media_sequence = playlist.media_sequence or 0
        sequences = [Sequence(media_sequence + i, s)
                     for i, s in enumerate(playlist.segments)]

        if sequences:
            self.process_sequences(playlist, sequences)

    def process_sequences(self, playlist, sequences):
        first_sequence, last_sequence = sequences[0], sequences[-1]

        if first_sequence.segment.key and first_sequence.segment.key.method != "NONE":
            self.logger.debug("Segments in this playlist are encrypted")

            if not CAN_DECRYPT:
                raise StreamError("Need pyCrypto installed to decrypt this stream")

        self.playlist_changed = ([s.num for s in self.playlist_sequences] !=
                                 [s.num for s in sequences])
        self.playlist_reload_time = (playlist.target_duration or
                                     last_sequence.segment.duration)
        if self.playlist_changed:
            self.playlist_sequences = sequences

        if not self.playlist_changed:
            self.playlist_reload_time = max(self.playlist_reload_time / 2, 1)

        if playlist.is_endlist:
            self.playlist_end = last_sequence.num

            playlist_type = playlist.playlist_type
            if self.playlist_changed and (playlist_type == "VOD" or
                                          playlist_type == "EVENT"):
                self.get_seek_meta(playlist, sequences)
                if self.complete_length and self.duration:
                    self.supports_seek = True

        if self.playlist_sequence < 0:
            if self.playlist_end is None:
                edge_index = -(min(len(sequences), max(int(self.live_edge), 1)))
                edge_sequence = sequences[edge_index]
                self.playlist_sequence = edge_sequence.num
            else:
                self.playlist_sequence = first_sequence.num

    def valid_sequence(self, sequence):
        return sequence.num >= self.playlist_sequence

    def get_seq_idx(self, seek_pos):
        for idx, sequence in enumerate(self.playlist_sequences):
            first_byte = sequence.segment_offset
            last_byte = first_byte + sequence.segment_length - 1
            if first_byte <= seek_pos <= last_byte:
                self.stream.player_range_adjust = -(seek_pos - first_byte)
                return idx

    def iter_segments(self):
        group_id = 0
        seq_start_pos = 0
        while not self.closed:
            try:
                # Handle seek events
                with self.mailbox.get("seek_event", block=False) as seek_event:
                    if seek_event:
                        seq_start_pos = self.get_seq_idx(seek_event.data)
                        group_id += 1
                        self.playlist_sequence = self.playlist_sequences[seq_start_pos].num

                        self.logger.debug("Worker received a seek event, "
                                          "new segments start at number {0}, "
                                          "group id {1}"
                                          .format(self.playlist_sequence, group_id))

                        # Defer to seek coordinator
                        self.mailbox.send("waiting on restart", target="seek_coordinator")
                        self.logger.debug("Worker thread paused, waiting on seek coordinator")
                        self.mailbox.wait_on_msg("restart")
                        self.logger.debug("Worker thread resumed")

                skip_reload = False
                for sequence in filter(self.valid_sequence,
                                       self.playlist_sequences[seq_start_pos:]):
                    # Got seek event.
                    # Break out of this inner loop, leave seek event unprocessed
                    if self.mailbox.get("seek_event", block=False, leave_msg=True):
                        skip_reload = True
                        break

                    sequence.group_id = group_id
                    stream_end = self.playlist_end and sequence.num >= self.playlist_end
                    sequence.is_last = stream_end
                    self.logger.debug("Adding segment {0} to queue, group_id {1}",
                                      sequence.num,
                                      sequence.group_id)
                    yield sequence

                    # End of stream
                    if stream_end:
                        if self.supports_seek:
                            # Idle waiting for seek events or shutdown
                            self.mailbox.send("should_idle", True, target="writer")
                            self.mailbox.wait_on_msg("seek_event", leave_msg=True)
                            skip_reload = True
                        else:
                            self.mailbox.send("should_idle", False, target="writer")
                            return

                    self.playlist_sequence = sequence.num + 1

                if not skip_reload and self.wait(self.playlist_reload_time):
                    try:
                        self.reload_playlist()
                    except StreamError as err:
                        self.logger.warning("Failed to reload playlist: {0}", err)

            except MailboxClosed:
                self.close()

    def get_seek_meta(self, playlist, sequences):
        self.logger.debug("Fetching meta data for seek")

        duration = 0
        complete_length = 0
        content_type = None
        try:
            # Parallelize large number of small network requests
            executor = ThreadPoolExecutor(16)
            meta_fetch_queue = queue.Queue()
            for segment in playlist.segments:
                future = executor.submit(self.session.http.head, segment.uri,
                                         acceptable_status=[200, 206],
                                         exception=StreamError)
                meta_fetch_queue.put(future)
            executor.shutdown(wait=False)

            seen_files = []
            segment_offset = 0
            for i, segment in enumerate(playlist.segments):
                file = segment.uri.split("?")[0]
                hls_byterange = segment.byterange

                future = meta_fetch_queue.get()
                res = future.result(timeout=10)

                segment_length = int(res.headers.get("Content-Length"))
                # Don't accumulate content length for hls byte ranges as they are
                # usually one file
                if not hls_byterange or (hls_byterange and file not in seen_files):
                    complete_length += segment_length

                # Meta data for player
                duration += segment.duration
                content_type = res.headers.get("Content-Type")

                # Add meta data to sequences needed to retrieve content on seek
                sequences[i].segment_offset = segment_offset
                sequences[i].segment_length = segment_length

                segment_offset += segment_length
                seen_files.append(file)

            self.logger.debug("Complete content length of {0} bytes retrieved",
                              complete_length)
            self.logger.debug("Content duration of {0} seconds retrieved",
                              duration)
            self.logger.debug("Content type of '{0}' retrieved",
                              content_type)

            self.complete_length = complete_length
            self.duration = duration
            self.content_type = content_type

            self.logger.debug("Fetching of seek meta data complete")

        except (StreamError, futures.TimeoutError, futures.CancelledError,
                ValueError, TypeError) as err:
            self.logger.debug("Unable to get complete VOD metadata for seek: {0}"
                              .format(err))
            self.complete_length = None
            self.duration = None
            self.content_type = None

    def close(self):
        if not self.closed:
            super(HLSStreamWorker, self).close()
            self.mailbox.close()


class HLSStreamReader(SegmentedStreamReader):
    __worker__ = HLSStreamWorker
    __writer__ = HLSStreamWriter

    def __init__(self, stream, *args, **kwargs):
        SegmentedStreamReader.__init__(self, stream, *args, **kwargs)
        self.supports_seek = False
        self.logger = stream.session.logger.new_module("stream.hls")
        self.request_params = dict(stream.args)
        self.timeout = stream.session.options.get("hls-timeout")

        # These params are reserved for internal use
        self.request_params.pop("exception", None)
        self.request_params.pop("stream", None)
        self.request_params.pop("timeout", None)
        self.request_params.pop("url", None)

    def open(self):
        SegmentedStreamReader.open(self)
        self.supports_seek = self.worker.supports_seek
        if self.supports_seek:
            self.seek_coordinator = SeekCoordinator(self)
            self.seek_coordinator.start()

    def close(self):
        try:
            self.seek_coordinator.close()
            if self.seek_coordinator.is_alive():
                self.seek_coordinator.join()
        except AttributeError:
            pass
        SegmentedStreamReader.close(self)


class HLSStream(HTTPStream):
    """Implementation of the Apple HTTP Live Streaming protocol

    *Attributes:*

    - :attr:`url` The URL to the HLS playlist.
    - :attr:`args` A :class:`dict` containing keyword arguments passed
      to :meth:`requests.request`, such as headers and cookies.

    .. versionchanged:: 1.7.0
       Added *args* attribute.

    """

    __shortname__ = "hls"

    def __init__(self, session_, url, **kwargs):
        HTTPStream.__init__(self, session_, url, **kwargs)

    def __repr__(self):
        return "<HLSStream({0!r})>".format(self.url)

    def __json__(self):
        json = HTTPStream.__json__(self)

        # Pretty sure HLS is GET only.
        del json["method"]
        del json["body"]

        return json

    def open(self):
        reader = HLSStreamReader(self)
        reader.open()

        if reader.worker.supports_seek:
            self.supports_seek = True
            self.complete_length = reader.worker.complete_length
            self.duration = reader.worker.duration
            self.content_type = reader.worker.content_type

        return reader

    @classmethod
    def parse_variant_playlist(cls, session_, url, name_key="name",
                               name_prefix="", check_streams=False,
                               **request_params):
        """Attempts to parse a variant playlist and return its streams.

        :param url: The URL of the variant playlist.
        :param name_key: Prefer to use this key as stream name, valid keys are:
                         name, pixels, bitrate.
        :param name_prefix: Add this prefix to the stream names.
        :param check_streams: Only allow streams that are accesible.
        """

        # Backwards compatibility with "namekey" and "nameprefix" params.
        name_key = request_params.pop("namekey", name_key)
        name_prefix = request_params.pop("nameprefix", name_prefix)

        res = session_.http.get(url, exception=IOError, **request_params)

        try:
            parser = hls_playlist.load(res.text, base_uri=res.url)
        except ValueError as err:
            raise IOError("Failed to parse playlist: {0}".format(err))

        streams = {}
        for playlist in filter(lambda p: not p.is_iframe, parser.playlists):
            names = dict(name=None, pixels=None, bitrate=None)

            for media in playlist.media:
                if media.type == "VIDEO" and media.name:
                    names["name"] = media.name

            if playlist.stream_info.resolution:
                width, height = playlist.stream_info.resolution
                names["pixels"] = "{0}p".format(height)

            if playlist.stream_info.bandwidth:
                bw = playlist.stream_info.bandwidth

                if bw >= 1000:
                    names["bitrate"] = "{0}k".format(int(bw / 1000.0))
                else:
                    names["bitrate"] = "{0}k".format(bw / 1000.0)

            stream_name = (names.get(name_key) or names.get("name") or
                           names.get("pixels") or names.get("bitrate"))

            if not stream_name or stream_name in streams:
                continue

            if check_streams:
                try:
                    session_.http.get(playlist.uri, **request_params)
                except Exception:
                    continue

            stream = HLSStream(session_, playlist.uri, **request_params)
            streams[name_prefix + stream_name] = stream

        return streams
