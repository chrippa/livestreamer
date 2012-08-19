import livestreamer
from .compat import input, stdout, is_win32
from .logger import Logger
from .stream import StreamThread
from .utils import next_port, check_port

import sys, os, argparse, subprocess, cmd

logger = Logger("manager")

def processArgs(args, name="", usage="", min=None, max=None):
	args = list(filter(lambda x: (len(x)>0 and x != " "), args.split(" ")))
	
	if min is not None:
		if len(args) < min:
			print name, "requires at least", min, "."
			print usage
			return None
	
	if max is not None:
		if len(args) > max:
			print stream, "requires a maximum of", max, "parameters."
			print usage
			return None

	return args

class ManagerCli(cmd.Cmd):
	def __init__(self, args):
		cmd.Cmd.__init__(self)
		self.args = args
		self.streamPool = dict()
		self.streamIndex = 0

	def get_stream_id(self):
		self.streamIndex = self.streamIndex + 1
		return self.streamIndex

	def do_k(self, args):
		self.do_kill(args)

	def do_kill(self, args):
		parser = argparse.ArgumentParser(description='Kill a running stream')
		parser.add_argument('streamid', metavar='id', help='the stream id')

		try:
			args = parser.parse_args(args.split(" "))
		except SystemExit:
			return False

		try:
			stream = self.streamPool[args.streamid]
		except:
			print "Not a valit streamid."
			print "Use the list command to list all streams"
			return False

		while True:
			print "This is the stream for", stream.get_info()
			a = raw_input("Are you sure you want to kill it? (y/n) ").lower()
			if "y" in a:
				stream.kill_stream()
				stream.join_stream()
				return False
			elif "n" in a:
				return False

	def killAllStreams(self):
		for i, stream in self.streamPool.items():
			stream.kill_stream()
		for i, stream in self.streamPool.items():
			stream.join_stream()

	def remove_stale_streams(self):
		for stream in self.streamPool:	
			stream.process.join(timeout=0.1)
			if not stream.process.is_alive():
				self.streamPool.remove(stream)

	def exit(self):
		if(self.are_running_streams()):
			while True:
				print "There are streams still running!"
				a = raw_input("Are you sure you want to quit? (y/n) ").lower()
				if "y" in a:
					self.killAllStreams()
					return True
				elif "n" in a:
					return False
		print ""
		return True
	
	def do_e(self, line):
		return self.do_exit(line)

	def do_exit(self, line):
		return self.exit()

	def do_EOF(self, line):
		return self.exit()

	def do_l(self, line):
		self.do_list(line)

	def do_list(self, line):
		self.remove_stale_streams()

		if len(self.streamPool) == 0:
			print "There are no streams running"
		else:
			fw = 12
			print "".join([s.ljust(fw) for s in ('ID', 'URL', 'Stream', 'Port')])
			for id, stream in self.streamPool:
				print id, "\t", stream.get_info().join([str(s).ljust(fw) for s in line])

	def are_running_streams(self):
		self.remove_stale_streams()
		return len(self.streamPool) > 0

	def do_s(self, args):
		return self.do_stream(args)

	def do_stream(self, args):
		parser = argparse.ArgumentParser(description='Kill a running stream')
		parser.add_argument("url", help="URL to stream", nargs="?", default=self.args.url)
		parser.add_argument("stream", help="Stream quality to play, use 'best' for highest quality available", nargs="?", default=self.args.stream)

		playeropt = parser.add_argument_group("player options")
		playeropt.add_argument("-p", "--player", metavar="player", help="Command-line for player, default is 'vlc'", default="vlc")
		playeropt.add_argument("-P", "--port", metavar="port", help="The port to use if the player command contains '{PORT}'", default=next_port(self.args))

		outputopt = parser.add_argument_group("file output options")
		outputopt.add_argument("-o", "--output", metavar="filename", help="Write stream to file instead of playing it")
		outputopt.add_argument("-f", "--force", action="store_true", help="Always write to file even if it already exists")

		pluginopt = parser.add_argument_group("plugin options")
		pluginopt.add_argument("-c", "--cmdline", action="store_true", help="Print command-line used internally to play stream, this may not be available on all streams")

		try:
			args = parser.parse_args(args.split(" "))
		except SystemExit:
			return False
	
		if not args.url:
			print "TODO: PUT USAGE HERE"
			return False

		if "{PORT}" in args.player:				
			if not check_port(args.port):
				logger.error("The port", str(args.port), "is already in use.")
				return False

			# Put the port into the player.
			args.player = args.player.replace("{PORT}", str(args.port))

		args.stdout = False
		args.quiet_player = True
		args.logger = self.args.logger
	
		stream = StreamThread(args)
		self.streamPool[self.get_stream_id()] = stream
		stream.kill_stream()

class Manager():
	def __init__(self, args):
		try:
			args.cmdline = False
			args.quiet_player = True

			logger.set_level(args.loglevel)
			args.logger = logger
			
			interpreter = ManagerCli(args)		
			interpreter.prompt = "livestreamer$ "
			interpreter.cmdloop()
		except KeyboardInterrupt:
			print ""
			interpreter.killAllStreams()
