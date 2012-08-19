from .compat import urllib
from .plugins import PluginError

from livestreamer.compat import urllib
from livestreamer.plugins import PluginError
import hmac, hashlib, zlib, argparse, socket

SWF_KEY = b"Genuine Adobe Flash Player 001"

class ArgumentParser(argparse.ArgumentParser):
	def convert_arg_line_to_args(self, line):
		if line[0] == "#":
			return

		split = line.find("=")
		if split > 0:
			key = line[:split].strip()
			val = line[split+1:].strip()
			yield "--%s=%s" % (key, val)
		else:
			yield "--%s" % line

def urlopen(url, data=None, timeout=None, opener=None, userAgent=None):
	try:
		if opener is not None:
			fd = opener.open(url, data, timeout)
		else:
			if type(url) is str:
				req = urllib.Request(url)
			else:
				req = url
			if userAgent is not None:
				req.add_header("User-Agent", userAgent)
			fd = urllib.urlopen(req, data, timeout)

	except IOError as err:
		if type(err) is urllib.URLError:
			raise PluginError(err.reason)
		else:
			raise PluginError(err)

	return fd

def urlget(url, data=None, timeout=15, opener=None):
	fd = urlopen(url, data, timeout, opener)

	try:
		data = fd.read()
		fd.close()
	except IOError as err:
		if type(err) is urllib.URLError:
			raise PluginError(err.reason)
		else:
			raise PluginError(err)

	return data

def swfverify(url):
	swf = urlget(url)

	if swf[:3] == b"CWS":
		swf = b"F" + swf[1:8] + zlib.decompress(swf[8:])

	h = hmac.new(SWF_KEY, swf, hashlib.sha256)

	return h.hexdigest(), len(swf)

def verifyjson(json, key):
	if not key in json:
		raise PluginError(("Missing '{0}' key in JSON").format(key))

	return json[key]

def port(string):
	value = int(string)
	if value <= 1024:
		msg = "%r must be grater than 1024" % string
		raise argparse.ArgumentTypeError(msg)
	if value > 65535:
		msg = "%r must be less than 65535" % string
		raise argparse.ArgumentTypeError(msg)
	return value

def next_port(args):
	for port in range(args.min_port, args.max_port):
		if check_port(port):
			return port

def check_port(port):
	s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	try:
		s.bind(("", port))
	except:
		return False
	s.close()
	return True

__all__ = ["ArgumentParser", "urlopen", "urlget", "swfverify", "verifyjson", "port", "next_port", "check_port"]
