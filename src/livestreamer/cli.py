import sys, os, argparse
import livestreamer
from .manager import Manager
from .utils import port

from livestreamer.compat import input, stdout, is_win32
from livestreamer.logger import Logger
from livestreamer.stream import StreamThread

exampleusage = """
example usage:

$ livestreamer twitch.tv/onemoregametv
Found streams: 240p, 360p, 480p, 720p, best, iphonehigh, iphonelow, live
$ livestreamer twitch.tv/onemoregametv 720p

Stream now playbacks in player (default is VLC).

"""

logger = Logger("cli")
msg_output = sys.stdout
parser = livestreamer.utils.ArgumentParser(description="CLI program that launches streams from various streaming services in a custom video player",
										   fromfile_prefix_chars="@",
										   formatter_class=argparse.RawDescriptionHelpFormatter,
										   epilog=exampleusage, add_help=False)

parser.add_argument("url", help="URL to stream", nargs="?")
parser.add_argument("stream", help="Stream quality to play, use 'best' for highest quality available", nargs="?")

parser.add_argument("-h", "--help", action="store_true", help="Show this help message and exit")
parser.add_argument("-u", "--plugins", action="store_true", help="Print all currently installed plugins")
parser.add_argument("-l", "--loglevel", metavar="level", help="Set log level, valid levels: none, error, warning, info, debug", default="info")
parser.add_argument("-m", "--manager", action="store_true", help="Start the stream manager")
parser.add_argument("--min-port", metavar="port", help="Minimum port in the range for stream manager to start streams. Must grater than 50000. (default: 50000)", default=50000, type=port)
parser.add_argument("--max-port", metavar="port", help="Maximum port in the range for stream manager to start streams. Must less than 65000. (default: 65000)", default=65000, type=port)

playeropt = parser.add_argument_group("player options")
playeropt.add_argument("-p", "--player", metavar="player", help="Command-line for player, default is 'vlc'", default="vlc")
playeropt.add_argument("-q", "--quiet-player", action="store_true", help="Hide all player console output")

outputopt = parser.add_argument_group("file output options")
outputopt.add_argument("-o", "--output", metavar="filename", help="Write stream to file instead of playing it")
outputopt.add_argument("-f", "--force", action="store_true", help="Always write to file even if it already exists")
outputopt.add_argument("-O", "--stdout", action="store_true", help="Write stream to stdout instead of playing it")

pluginopt = parser.add_argument_group("plugin options")
pluginopt.add_argument("-c", "--cmdline", action="store_true", help="Print command-line used internally to play stream, this may not be available on all streams")
pluginopt.add_argument("-e", "--errorlog", action="store_true", help="Log possible errors from internal command-line to a temporary file, use when debugging")
pluginopt.add_argument("-r", "--rtmpdump", metavar="path", help="Specify location of rtmpdump")
pluginopt.add_argument("-j", "--jtv-cookie", metavar="cookie", help="Specify JustinTV cookie to allow access to subscription channels")
pluginopt.add_argument("-U", "--username", metavar="username", help="Authentication username used for GomTV plugin.")
pluginopt.add_argument("-P", "--password", metavar="password", help="Authentication password used for GomTV plugin.")

RCFILE = os.path.expanduser("~/.livestreamerrc")

def exit(msg):
	sys.exit(("error: {0}").format(msg))

def msg(msg):
	msg_output.write(msg + "\n")

def set_msg_output(output):
	msg_output = output
	logger.set_output(output)

def print_plugins():
	pluginlist = list(livestreamer.get_plugins().keys())
	msg(("Installed plugins: {0}").format(", ".join(pluginlist)))

def main():
	arglist = sys.argv[1:]

	if os.path.exists(RCFILE):
		arglist.insert(0, "@" + RCFILE)

	args = parser.parse_args(arglist)

	if args.stdout or args.output == "-":
		set_msg_output(sys.stderr)

	livestreamer.options.set("errorlog", args.errorlog)
	livestreamer.options.set("rtmpdump", args.rtmpdump)
	livestreamer.options.set("jtvcookie", args.jtv_cookie)
	livestreamer.options.set("username", args.username)
	livestreamer.options.set("password", args.password)

	logger.set_level(args.loglevel)
	args.logger = logger
	args.port = None
	
	if args.manager:
		Manager(args)
	elif args.url:
		StreamThread(args)
	elif args.plugins:
		print_plugins()
	else:
		parser.print_help()
