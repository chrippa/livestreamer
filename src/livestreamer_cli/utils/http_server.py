import socket
import re

from email.utils import formatdate
from io import BytesIO

try:
    from BaseHTTPServer import BaseHTTPRequestHandler
except ImportError:
    from http.server import BaseHTTPRequestHandler

_range_re = re.compile("""
    bytes=(?P<first_byte>\d+)-(?P<last_byte>\d*)\Z
""", re.VERBOSE)

_content_range_re = re.compile("""
    Content-Range:\s*bytes\s*
    (?P<first_byte>\d+)
    -
    (?P<last_byte>\d+)
    /
    (?P<complete_length>\d+)
""", re.VERBOSE)


class HTTPRequest(BaseHTTPRequestHandler):
    def __init__(self, request_text):
        self.rfile = BytesIO(request_text)
        self.raw_requestline = self.rfile.readline()
        self.error_code = self.error_message = None
        self.parse_request()

    def send_error(self, code, message):
        self.error_code = code
        self.error_message = message


class HTTPServer(object):
    def __init__(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.conn = self.host = self.port = None
        self.bound = False
        self._supports_seek = False
        self._complete_length = None

    def enable_seek(self, complete_length):
        """
        Enable communicating to the client that we support seek events

        :param complete_length: The complete length of the content to stream
        :return:
        """
        self._supports_seek = True
        self._complete_length = complete_length

    @property
    def supports_seek(self):
        return self._supports_seek

    @property
    def complete_length(self):
        return self._complete_length
    @property
    def addresses(self):
        if self.host:
            return [self.host]

        addrs = set()
        try:
            for info in socket.getaddrinfo(socket.gethostname(), self.port,
                                           socket.AF_INET):
                addrs.add(info[4][0])
        except socket.gaierror:
            pass

        addrs.add("127.0.0.1")
        return sorted(addrs)

    @property
    def urls(self):
        for addr in self.addresses:
            yield "http://{0}:{1}/".format(addr, self.port)

    @property
    def url(self):
        return next(self.urls, None)

    def bind(self, host="127.0.0.1", port=0):
        try:
            self.socket.bind((host or "", port))
        except socket.error as err:
            raise OSError(err)

        self.socket.listen(1)
        self.bound = True
        self.host, self.port = self.socket.getsockname()
        if self.host == "0.0.0.0":
            self.host = None

    def content_range_hdr(self, headers):
        range_header = headers.get("Range")
        if range_header:
            match = _range_re.match(range_header)
            if match:
                first_byte_pos = int(match.group("first_byte"))
                if match.group("last_byte"):
                    last_byte_pos = int(match.group("last_byte"))
                else:
                    last_byte_pos = None

                # Handle requests that don't specify an end byte
                if last_byte_pos is None:
                    last_byte_pos = self.complete_length - 1

                # Make sure we don't overrun end of file
                if last_byte_pos >= self.complete_length:
                    last_byte_pos = self.complete_length - 1

                return ("Content-Range: bytes {0}-{1}/{2}\r\n".format(
                        first_byte_pos,
                        last_byte_pos,
                        self.complete_length).encode())
            else:
                raise OSError("Uninterpretable range header: Range: {0}"
                              .format(range_header))
        else:
            return ("Content-Range: bytes 0-{0}/{1}\r\n".format(
                self.complete_length - 1,
                    self.complete_length).encode())

    def content_length_hdr(self, content_range_header):
        match = _content_range_re.match(content_range_header.decode())
        first_byte_pos = int(match.group("first_byte"))
        last_byte_pos = int(match.group("last_byte"))
        content_length = last_byte_pos - first_byte_pos + 1
        return "Content-Length: {0}\r\n".format(content_length).encode()

    def get_seek_pos(self, req):
        """
        :param req: HTTP request
        :return: Position of the first byte to stream or 0 if no seek position
                 in request
        """
        req_range = req.headers.get("Range")
        if req_range is None:
            return 0

        match = _range_re.match(req_range)
        seek_pos = int(match.group("first_byte"))
        return seek_pos

    def open(self, timeout=30):
        self.socket.settimeout(timeout)

        try:
            conn, addr = self.socket.accept()
            conn.settimeout(None)
        except socket.timeout:
            raise OSError("Socket accept timed out")

        try:
            req_data = conn.recv(1024)
        except socket.error:
            raise OSError("Failed to read data from socket")

        req = HTTPRequest(req_data)
        if req.command not in ("GET", "HEAD"):
            conn.send(b"HTTP/1.1 501 Not Implemented\r\n")
            conn.close()
            raise OSError("Invalid request method: {0}".format(req.command))

        # We don't want to send any data on HEAD requests.
        if req.command == "HEAD":
            conn.close()
            raise OSError

        self.conn = conn

        return req

    def write(self, data):
        if not self.conn:
            raise IOError("No connection")

        self.conn.sendall(data)

    def close(self, client_only=False):
        if self.conn:
            self.conn.close()

        if not client_only:
            self.socket.close()

    def send_header(self, req):
        try:
            if self.supports_seek:
                content_range_hdr = self.content_range_hdr(req.headers)
                content_length_hdr = self.content_length_hdr(content_range_hdr)

                self.conn.send(b"HTTP/1.1 206 Partial Content\r\n")
                self.conn.send(b"Accept-Ranges: bytes\r\n")
                self.conn.send(content_range_hdr)
                self.conn.send(content_length_hdr)
                # TODO: Get content duration for accurate seek control
            else:
                self.conn.send(b"HTTP/1.1 200 OK\r\n")

            date_str = formatdate(timeval=None, localtime=False, usegmt=True)
            self.conn.send(b"Server: Livestreamer\r\n")
            self.conn.send(b"Date: " + date_str.encode())
            # TODO: Get content type
            self.conn.send(b"Content-Type: video/unknown\r\n")
            self.conn.send(b"\r\n")
        except socket.error:
            raise OSError("Failed to write data to socket")
