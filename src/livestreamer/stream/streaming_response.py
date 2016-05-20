from contextlib import closing
from functools import partial
from requests.exceptions import RequestException
from ..exceptions import StreamError
from ..buffers import RingBuffer
from .http import HTTPStream


class StreamingResponse:
    def __init__(self, session, executor, uri, first_byte="0", last_byte="",
                 group_id=0, request_params=None, chunk_size=8192,
                 download_timeout=None, read_timeout=None, retries=None):
        self.closed = False
        self.session = session
        self.executor = executor  # Not owned by the streaming response object
        self.uri = uri
        self.first_byte = first_byte
        self.last_byte = last_byte
        self.request_params = request_params or {}
        self.chunk_size = chunk_size
        self.download_timeout = download_timeout
        self.read_timeout = read_timeout
        self.retries = retries
        self.logger = session.logger.new_module("stream.streaming_resp")
        self.future = None
        self.exception = None
        self.buffered_data = 0
        self.consumed_data = 0
        self.segment_size = None  # Initially unknown
        self.segment_buffer = RingBuffer()
        self.group_id = group_id
        self.filename = uri.split("/")[-1].split("?")[0]

        if download_timeout is None:
            self.download_timeout = self.session.options.get("stream-segment-timeout")
        if read_timeout is None:
            self.read_timeout = self.session.options.get("http-stream-timeout")
        if retries is None:
            self.retries = self.session.options.get("stream-segment-attempts")

    @property
    def consumed(self):
        return self.consumed_data == self.segment_size

    @property
    def shutdown_event(self):
        return self.executor.running_group != self.group_id or \
               self.closed

    def start(self):
        self.future = self.executor.submit(self._stream_fetch,
                                           self.retries,
                                           work_group_id=self.group_id)
        return self

    def _stream_fetch(self, retries):
        if self.shutdown_event:
            self.close()
            return

        try:
            self.logger.debug("Started download of {0}, bytes {1}-{2}, group "
                              "id {3}",
                              self.filename, self.first_byte, self.last_byte,
                              self.group_id)

            request_params = HTTPStream.add_range_hdr(self.first_byte,
                                                      self.last_byte,
                                                      self.request_params)
            resp = self.session.http.get(self.uri,
                                         timeout=self.download_timeout,
                                         exception=StreamError,
                                         stream=True,
                                         **request_params)

            with closing(resp):
                # Get the segment size
                self.segment_size = int(resp.headers["Content-Length"])
                if self.segment_buffer.buffer_size != self.segment_size:
                    self.segment_buffer.resize(self.segment_size)

                for chunk in resp.iter_content(self.chunk_size):
                    # Poll for events that indicate we should terminate the download
                    if self.shutdown_event:
                        self.close()
                        return

                    self.segment_buffer.write(chunk)
                    self.buffered_data += len(chunk)

            self.logger.debug("Download of {0}, bytes {1}-{2}, group id {3} "
                              "complete",
                              self.filename, self.first_byte, self.last_byte,
                              self.group_id)

        except (StreamError, RequestException, IOError) as err:
            # Log exception
            exception = StreamError("Unable to download {0}, bytes {1}-{2}, "
                                    "group id {3} ({4})"
                                    .format(self.filename, self.first_byte,
                                            self.last_byte, self.group_id,
                                            err))
            exception.err = err
            self.logger.error("{0}", exception)

            # Retry if retry count positive
            if retries and not self.shutdown_event:
                self.logger.error("Retrying {0}, bytes {1}-{2}, group id {3}",
                                  self.filename, self.first_byte,
                                  self.last_byte, self.group_id)
                self._stream_fetch(retries - 1)

            # Raise exception if we couldn't recover
            else:
                self.exception = exception
                self.close()
                raise self.exception

    def read(self, chunk_size):
        if self.shutdown_event and not self.exception:
            return b""

        try:
            result = self.segment_buffer.read(chunk_size,
                                              block=not self.future.done(),
                                              timeout=self.read_timeout)
        except IOError as err:
            self.close()
            raise StreamError("Failed to read data from stream: {0}"
                              .format(err))

        # The streaming response encountered an unrecoverable exception in one
        # of it's download threads. The stream should crash at this point
        if self.exception:
            self.close()
            raise self.exception

        self.consumed_data += len(result)
        if self.consumed:
            self.close()

        return result

    def iter_content(self, chunk_size):
        return iter(partial(self.read, chunk_size), b"")

    def close(self):
        if not self.closed:
            if self.consumed:
                self.logger.debug("Stream of {0}, bytes {1}-{2}, group id {3} "
                                  "consumed",
                                  self.filename, self.first_byte,
                                  self.last_byte, self.group_id)
            else:
                self.logger.debug("Download of {0}, bytes {1}-{2}, group id "
                                  "{3} cancelled",
                                  self.filename, self.first_byte,
                                  self.last_byte, self.group_id)

            self.closed = True
            self.segment_buffer.close()
