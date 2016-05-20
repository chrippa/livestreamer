from concurrent import futures
from threading import Thread, Event

from ..exceptions import MailboxClosed
from .threadpool_manager import ThreadPoolManager
from .stream import StreamIO
from ..buffers import RingBuffer
from ..compat import queue


class SeekCoordinator(Thread):
    def __init__(self, reader):
        self.thread_man = reader.writer.executor
        self.work_queue = reader.writer.futures
        self.video_buffer = reader.buffer
        self.logger = reader.session.logger.new_module("stream.seek_coord")
        self.mailbox = reader.stream.msg_broker.register("seek_coordinator")
        self.mailbox.subscribe("seek_event")
        self.closed = False

        Thread.__init__(self)
        self.daemon = True

    def run(self):
        while not self.closed:
            try:
                with self.mailbox.get("seek_event", block=True):
                    self.logger.debug("Seek coordinator received a seek event")

                    # Starts shutdown of prev work group
                    prev_group_id = self.thread_man.running_group
                    group_id = prev_group_id + 1
                    self.thread_man.set_running_group(group_id, wait_shutdown=False)

                    # Close the video buffer so the writer can't block trying to
                    # write to it
                    self.video_buffer.close()

                    # Wait for the writer to enter a ready state first so it can't
                    # block trying to fetch work from an empty work queue
                    self.logger.debug("Waiting for writer thread")
                    self.mailbox.wait_on_msg("waiting on restart", source="writer")

                    # Reader and writer are paused, it is now safe to re-initialise
                    # the video buffer
                    self.video_buffer.__init__(self.video_buffer.buffer_size)

                    # Flush work queue and poll worker for ready state
                    queue_empty = False
                    worker_ready = False
                    self.logger.debug("Flushing work queue and polling worker thread")
                    while not worker_ready or not queue_empty:
                        if not worker_ready:
                            worker_ready = self.mailbox.get("waiting on restart",
                                                            source="worker")
                            if worker_ready:
                                self.logger.debug("Worker ready, finishing queue flush")
                        try:
                            work_item = self.work_queue.get(block=False)
                            segment = work_item[0]
                            try:
                                self.logger.debug("Dropping segment {0}-{1}, "
                                                  "group id {id} from the work queue"
                                                  .format(*segment.byte_range,
                                                          id=segment.group_id))
                            except AttributeError:
                                pass
                            try:
                                self.logger.debug("Dropping segment {0}, "
                                                  "group id {id} from the work queue"
                                                  .format(segment.num,
                                                          id=segment.group_id))
                            except AttributeError:
                                pass
                            queue_empty = self.work_queue.empty()
                        except queue.Empty:
                            queue_empty = self.work_queue.empty()

                    # Queue has been flush so it is safe to restart threads
                    self.logger.debug("Queue flush complete, restarting worker and writer threads")
                    self.mailbox.send("restart", target="worker")
                    self.mailbox.send("restart", target="writer")
            except MailboxClosed:
                self.close()

    def close(self):
        if not self.closed:
            self.closed = True
            self.mailbox.close()


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

    def run(self):
        for segment in self.iter_segments():
            self.writer.put(segment)

        # End of stream, tells the writer to exit
        self.writer.put(None)
        self.close()


class SegmentedStreamWriter(Thread):
    """The writer thread.

    This thread is responsible for fetching segments, processing them
    and finally writing the data to the buffer.
    """

    def __init__(self, reader, size=20, retries=None, threads=None, timeout=None):
        self.closed = False
        self.reader = reader
        self.stream = reader.stream
        self.session = reader.stream.session
        self.logger = reader.logger

        if not retries:
            retries = self.session.options.get("stream-segment-attempts")

        if not threads:
            threads = self.session.options.get("stream-segment-threads")

        if not timeout:
            timeout = self.session.options.get("stream-segment-timeout")

        self.retries = retries
        self.timeout = timeout
        self.executor = futures.ThreadPoolExecutor(max_workers=threads)
        self.futures = queue.Queue(size)

        Thread.__init__(self)
        self.daemon = True

    def close(self):
        """Shuts down the thread."""
        if not self.closed:
            self.logger.debug("Closing writer thread")

        self.closed = True
        self.reader.buffer.close()
        self.executor.shutdown(wait=True)

    def put(self, segment):
        """Adds a segment to the download pool and write queue."""
        if self.closed:
            return

        # If we are using a thread pool manager, try to find a group id
        kwargs = {}
        if isinstance(self.executor, ThreadPoolManager):
            try:
                kwargs["work_group_id"] = segment.group_id
            except AttributeError:
                pass

        if segment is not None:
            future = self.executor.submit(self.fetch, segment,
                                          retries=self.retries,
                                          **kwargs)
        else:
            future = None

        self.queue(self.futures, (segment, future))

    def queue(self, queue_, value):
        """Puts a value into a queue but aborts if this thread is closed."""
        while not self.closed:
            try:
                queue_.put(value, block=True, timeout=1)
                break
            except queue.Full:
                continue

    def fetch(self, segment):
        """Fetches a segment.

        Should be overridden by the inheriting class.
        """
        pass

    def write(self, segment, result):
        """Writes a segment to the buffer.

        Should be overridden by the inheriting class.
        """
        pass

    def run(self):
        while not self.closed:
            try:
                segment, future = self.futures.get(block=True, timeout=0.5)
            except queue.Empty:
                continue

            # End of stream
            if future is None:
                break

            while not self.closed:
                try:
                    result = future.result(timeout=0.5)
                except futures.TimeoutError:
                    continue
                except futures.CancelledError:
                    break

                if result is not None:
                    self.write(segment, result)

                break

        self.close()


class SegmentedStreamReader(StreamIO):
    __worker__ = SegmentedStreamWorker
    __writer__ = SegmentedStreamWriter

    def __init__(self, stream, timeout=None):
        StreamIO.__init__(self)
        self.session = stream.session
        self.stream = stream

        if not timeout:
            timeout = self.session.options.get("stream-timeout")

        self.timeout = timeout

    def open(self):
        buffer_size = self.session.get_option("ringbuffer-size")
        self.buffer = RingBuffer(buffer_size)
        self.writer = self.__writer__(self)
        self.worker = self.__worker__(self)

        self.writer.start()
        self.worker.start()

    def close(self):
        self.worker.close()
        self.writer.close()

        for thread in (self.worker, self.writer):
            if thread.is_alive():
                thread.join()

        self.buffer.close()

    def read(self, size):
        if not self.buffer:
            return b""

        return self.buffer.read(size, block=self.writer.is_alive(),
                                timeout=self.timeout)
