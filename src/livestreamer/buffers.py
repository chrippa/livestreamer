from bisect import bisect_left
from collections import deque, namedtuple
from io import BytesIO
from threading import Event, Lock


class Chunk(BytesIO):
    """A single chunk, part of the buffer."""

    def __init__(self, buf):
        self.length = len(buf)
        BytesIO.__init__(self, buf)

    @property
    def empty(self):
        return self.tell() == self.length


class Buffer(object):
    """Simple buffer for use in single-threaded consumer/filler.

    Stores chunks in a deque to avoid inefficient reallocating
    of large buffers.
    """

    def __init__(self):
        self.chunks = deque()
        self.current_chunk = None
        self.closed = False
        self.length = 0

    def _iterate_chunks(self, size):
        bytes_left = size

        while bytes_left:
            try:
                current_chunk = (self.current_chunk or
                                 Chunk(self.chunks.popleft()))
            except IndexError:
                break

            data = current_chunk.read(bytes_left)
            bytes_left -= len(data)

            if current_chunk.empty:
                self.current_chunk = None
            else:
                self.current_chunk = current_chunk

            yield data

    def write(self, data):
        if not self.closed:
            data = bytes(data)  # Copy so that original buffer may be reused
            self.chunks.append(data)
            self.length += len(data)

    def read(self, size=-1):
        if size < 0 or size > self.length:
            size = self.length

        if not size:
            return b""

        data = b"".join(self._iterate_chunks(size))
        self.length -= len(data)

        return data

    def close(self):
        self.closed = True


class RingBuffer(Buffer):
    """Circular buffer for use in multi-threaded consumer/filler."""

    def __init__(self, size=8192*4):
        Buffer.__init__(self)

        self.buffer_size = size
        self.buffer_lock = Lock()

        self.event_free = Event()
        self.event_free.set()
        self.event_used = Event()

    def _check_events(self):
        if self.length > 0:
            self.event_used.set()
        else:
            self.event_used.clear()

        if self.is_full:
            self.event_free.clear()
        else:
            self.event_free.set()

    def _read(self, size=-1):
        with self.buffer_lock:
            data = Buffer.read(self, size)

            self._check_events()

        return data

    def read(self, size=-1, block=True, timeout=None):
        if block and not self.closed:
            self.event_used.wait(timeout)

            # If the event is still not set it's a timeout
            if not self.event_used.is_set() and self.length == 0:
                raise IOError("Read timeout")

        return self._read(size)

    def write(self, data):
        if self.closed:
            return

        data_left = len(data)
        data_total = len(data)

        while data_left > 0:
            self.event_free.wait()

            if self.closed:
                return

            with self.buffer_lock:
                write_len = min(self.free, data_left)
                written = data_total - data_left

                Buffer.write(self, data[written:written+write_len])
                data_left -= write_len

                self._check_events()

    def resize(self, size):
        with self.buffer_lock:
            self.buffer_size = size

            self._check_events()

    def wait_free(self, timeout=None):
        self.event_free.wait(timeout)

    def wait_used(self, timeout=None):
        self.event_used.wait(timeout)

    def close(self):
        Buffer.close(self)

        # Make sure we don't let a .write() and .read() block forever
        self.event_free.set()
        self.event_used.set()

    @property
    def free(self):
        return max(self.buffer_size - self.length, 0)

    @property
    def is_full(self):
        return self.free == 0

class Segment(namedtuple("Segment", "num chunk")):
    __slots__ = ()
    def __new__(cls, num, chunk):
        chunk = Chunk(chunk)
        return super(Segment, cls).__new__(cls, num, chunk)

    def read(self, size=-1):
        return self.chunk.read(size)

    @property
    def empty(self):
        return self.chunk.empty

class SortedDeque(deque):
    __slots__ = ()
    def __init__(self, iterable=[]):
        super(SortedDeque, self).__init__(sorted(iterable))

    # Must use insert instead of append to add objects to keep
    # everything sorted.
    def insert(self, value):
        index = bisect_left(self, value)
        self.rotate(-index)
        self.appendleft(value)
        self.rotate(index)

class SortingRingBuffer(RingBuffer):
    def __init__(self, size=8192*4):
        RingBuffer.__init__(self, size)
        # Replace self.chunks deque() with SortedDeque()
        self.chunks = SortedDeque()
        self.segments = 0
        self.counter = 0

    @property
    def _pending_segments(self):
        # Wait for at least 3 segments to have a decent order.
        if self.segments < 3:
            return True
        segment_list = [seg.num for seg in self.chunks]
        if len(segment_list) < 1:
            return True
        missing = set(
            range(
                segment_list[0],
                segment_list[len(segment_list)-1]
            )[1:]
        ) - set(segment_list)
        # In case we 100% lost a segment, just popleft until everything
        # gets aligned again.
        if len(missing) != 0:
            if self.segments > 10 and self.counter > 2:
                self.chunks.popleft()
                self.counter = 0
                # wtb a logger around here!.
                #print >>sys.stderr, "pending!", self.segments, missing
            self.counter += 1
            return True
        else:
            self.counter = 0
            return False

    def _check_events(self):
        # XXX The length check may be redundant...
        if not self._pending_segments and self.length > 0:
            self.event_used.set()
        else:
            self.event_used.clear()

        if self.is_full:
            self.event_free.clear()
        else:
            self.event_free.set()

    # Our own _iterate_chunks, it's the same minus Chunk(deque.popleft()).
    def _iterate_segments(self, size):
        bytes_left = size

        while bytes_left:
            try:
                current_chunk = (self.current_chunk or self.chunks.popleft())
            except IndexError:
                break

            data = current_chunk.read(bytes_left)
            bytes_left -= len(data)

            if current_chunk.empty:
                self.current_chunk = None
                self.segments -= 1
            else:
                self.current_chunk = current_chunk

            yield data

    # Must call _iterate_segments instead of _iterate_chunks.
    def _read(self, size=-1):
        if size < 0 or size > self.length:
            size = self.length

        if not size:
            return b""

        data = b"".join(self._iterate_segments(size))
        self.length -= len(data)

        return data

    # Main override of read()
    def read(self, size=-1, block=True, timeout=None):
        if block and not self.closed:
            self.event_used.wait(timeout)

            # If the event is still not set it's a timeout
            if not self.event_used.is_set() and self.length == 0:
                raise IOError("Read timeout")

        with self.buffer_lock:
            data = self._read(size)
            self._check_events()

        return data

    # Write now accepts a new argument, "sequence number" which is used
    # to create the Sequence() object that gets inserted into the 
    # SortedDeque().
    def write(self, data, seqnum=0):
        if self.closed:
            return

        data_left = len(data)
        data_total = len(data)

        while data_left > 0:
            self.event_free.wait()

            if self.closed:
                return

            with self.buffer_lock:
                write_len = min(self.free, data_left)
                written = data_total - data_left
                # Instead of Buffer.write(), directly use SortedDeque.insert()
                if not self.closed:
                    # Copy so that original buffer may be reused
                    data = bytes(data[written:written+write_len])  
                    # Couple together the data buffer and sequence number
                    self.chunks.insert(Segment(seqnum, data))
                    self.length += len(data)
                    self.segments += 1
                data_left -= write_len

                self._check_events()

__all__ = ["Buffer", "RingBuffer", "SortingRingBuffer"]
