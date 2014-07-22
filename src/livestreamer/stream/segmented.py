from threading import Thread, Event

from .stream import StreamIO
from ..buffers import SortingRingBuffer
from ..compat import queue



class SegmentedStreamWorker(Thread):
    """The general worker thread.

    This thread is responsible for queueing up segments in the
    writer thread.
    """

    def __init__(self, reader):
        self.closed = False
        self.reader = reader
        self.writer = reader.writer
        self.stream = reader.stream
        self.session = reader.stream.session
        self.logger = reader.logger

        self._last = 0
        self._total_threads = self.session.get_option("hls-download-threads")
        self._wait = None

        Thread.__init__(self)
        self.daemon = True

    def close(self):
        """Shuts down the thread."""
        if not self.closed:
            self.logger.debug("Closing worker thread")

        self.closed = True
        if self._wait:
            self._wait.set()

    def wait(self, time):
        """Pauses the thread for a specified time.

        Returns False if interrupted by another thread and True if the
        time runs out normally.
        """
        self._wait = Event()
        return not self._wait.wait(time)

    def iter_segments(self):
        """The iterator that generates segments for the worker thread.

        Should be overridden by the inheriting class.
        """
        return
        yield

    def _distribute_segment(self, segment):
        # Simple segment delivery to each thread, 
        # a None segment would mean shutdown.
        if segment is None:
            for thread in self.writer:
                thread.put(segment)
            return
        self.writer[self._last].put(segment)
        if self._last == (self._total_threads - 1):
            self._last = 0
        else:
            self._last += 1

    def run(self):
        for segment in self.iter_segments():
            self._distribute_segment(segment)

        # End of stream, tells the writer to exit
        self._distribute_segment(None)
        self.close()


class SegmentedStreamWriter(Thread):
    """The writer thread.

    This thread is responsible for fetching segments, processing them
    and finally writing the data to the buffer.
    """

    def __init__(self, reader, size=10):
        self.closed = False
        self.queue = queue.Queue(size)
        self.reader = reader
        self.stream = reader.stream
        self.session = reader.stream.session
        self.logger = reader.logger

        Thread.__init__(self)
        self.daemon = True

    def close(self):
        """Shuts down the thread."""
        if not self.closed:
            self.logger.debug("Closing writer thread")

        self.closed = True
        self.reader.buffer.close()

    def put(self, segment):
        """Add a segment to the queue."""
        while not self.closed:
            try:
                self.queue.put(segment, block=True, timeout=1)
                break
            except queue.Full:
                continue

    def write(self, segment):
        """Write the segment to the buffer.

        Should be overridden by the inheriting class.
        """
        pass

    def run(self):
        while not self.closed:
            try:
                segment = self.queue.get(block=True, timeout=0.5)
            except queue.Empty:
                continue

            if segment is not None:
                self.write(segment)
            else:
                break

        self.close()


class SegmentedStreamReader(StreamIO):
    __worker__ = SegmentedStreamWorker
    __writer__ = SegmentedStreamWriter

    def __init__(self, stream, timeout=60):
        StreamIO.__init__(self)

        self.session = stream.session
        self.stream = stream
        self.timeout = timeout
        self.writer = []

    def open(self):
        buffer_size = self.session.get_option("ringbuffer-size")
        downloader_threads = self.session.get_option("hls-download-threads")
        self.buffer = SortingRingBuffer(buffer_size)
        for i in range(downloader_threads):
            self.writer.append(self.__writer__(self))
        self.worker = self.__worker__(self)

        for thread in self.writer:
            thread.start()
        self.worker.start()

    def close(self):
        self.worker.close()
        for thread in self.writer:
            thread.close()

        for thread in [self.worker] + self.writer:
            if thread.is_alive():
                thread.join()

        self.buffer.close()

    def _any_alive(self):
        # return is_alive() for any writer thread that's still running.
        alive = False
        for thread in self.writer:
            if not alive:
                alive = thread.is_alive()
        return alive

    def read(self, size):
        if not self.buffer:
            return b""

        return self.buffer.read(size, block=self._any_alive(),
                                timeout=self.timeout)



