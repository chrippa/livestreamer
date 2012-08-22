import livestreamer
from .compat import input, stdout, is_win32
from .logger import Logger
from .stream import StreamThread
from .utils import next_port, check_port, get_password
from prettytable import PrettyTable

import sys, os, argparse, subprocess, cmd

class ManagerCli(cmd.Cmd):
	def __init__(self, args):
		cmd.Cmd.__init__(self)
		self.args = args
		self.streamPool = dict()
		self.streamIndex = 0

	def get_stream_id(self):
		self.streamIndex = self.streamIndex + 1
		return self.streamIndex

	def killAllStreams(self):
		for id, stream in self.streamPool.items():
			stream.kill_stream()
		for id, stream in self.streamPool.items():
			stream.join_stream()

	def remove_stale_streams(self):
		for id, stream in self.streamPool.items():	
			stream.process.join(timeout=0.1)
			if not stream.process.is_alive():
				del self.streamPool[id]

	def are_running_streams(self):
		self.remove_stale_streams()
		return len(self.streamPool) > 0

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

	def do_k(self, args):
		'Kill a running stream'
		self.do_kill(args)

	def do_kill(self, args):
		'Kill a running stream'
		parser = argparse.ArgumentParser(description='Kill a running stream')
		parser.add_argument('streamid', metavar='id', help='the stream id or "all" to kill all streams')

		try:
			args = parser.parse_args(args.split(" "))
		except SystemExit:
			return False
			
		if args.streamid == "all":
			self.killAllStreams()
			return False

		try:
			stream = self.streamPool[int(args.streamid)]
		except:
			print "Not a valit streamid."
			print "Use the list command to list all streams"
			return False

		table = PrettyTable(["ID", "URL", "Stream", "Port"])
		table.add_row(stream.get_info())
		print table
		while True:
			a = raw_input("Are you sure you want to kill this stream? (y/n) ").lower()
			if "y" in a:
				stream.kill_stream()
				stream.join_stream()
				return False
			elif "n" in a:
				return False
	
	def do_e(self, args):
		'Exit the command line'
		return self.do_exit(args)

	def do_exit(self, args):
		'Exit the command line'
		return self.exit()

	def do_EOF(self, args):
		'Exit the command line'
		return self.exit()

	def do_l(self, args):
		'List streams currently running'
		self.do_list(args)

	def do_list(self, args):
		'List streams currently running'
		self.remove_stale_streams()

		if len(self.streamPool) == 0:
			print "There are no streams running"
			return False
		
		table = PrettyTable(["ID", "URL", "Stream", "Port"])
		
		for id, stream in self.streamPool.items():
			table.add_row(stream.get_info())
			
		print table

	def do_s(self, args):
		'Start a new stream'
		return self.do_stream(args)

	def do_stream(self, args):
		'Start a new stream'
		exampleusage = """example usage:

$ stream twitch.tv/onemoregametv
Found streams: 240p, 360p, 480p, 720p, best, iphonehigh, iphonelow, live
$ stream twitch.tv/onemoregametv 720p

Stream now playbacks in player (default is VLC).
"""
		parser = argparse.ArgumentParser(description='Start a new stream')
		parser.add_argument("url", help="URL to stream", nargs="?", default=self.args.url)
		parser.add_argument("stream", 
			help="Stream quality to play, use 'best' for highest quality available", 
			nargs="?", default=self.args.stream)

		playeropt = parser.add_argument_group("player options")
		playeropt.add_argument("-p", "--player", metavar="player", 
			help="Command-line for player, default is 'vlc'", default="vlc")
		playeropt.add_argument("-P", "--port", metavar="port", 
			help="The port to use if the player command contains '{PORT}'", default=next_port(self.args))

		outputopt = parser.add_argument_group("file output options")
		outputopt.add_argument("-o", "--output", metavar="filename", 
			help="Write stream to file instead of playing it", default=self.args.output)
		outputopt.add_argument("-f", "--force", action="store_true", 
			help="Always write to file even if it already exists", default=self.args.force)

		pluginopt = parser.add_argument_group("plugin options")
		pluginopt.add_argument("-c", "--cmdline", action="store_true", 
			help="Print command-line used internally to play stream, this may not be available on all streams", 
			default=self.args.cmdline)

		try:
			args = parser.parse_args(args.split(" "))
		except SystemExit:
			return False
	
		if not args.url:
			print exampleusage
			return False
	
		if "{PORT}" in args.player:				
			if not check_port(args.port):
				logger.error("The port", str(args.port), "is already in use.")
				return False

			# Put the port into the player.
			args.player = args.player.replace("{PORT}", str(args.port))
		else:
			args.port = False

		args.stdout = False
		args.quiet_player = True
		args.loglevel = self.args.loglevel
		
		args.username = self.args.username
		args.password = self.args.password
		args.jtv_cookie = self.args.jtv_cookie
	
		stream = StreamThread(self.get_stream_id(), args)
		self.streamPool[stream.id] = stream

	def do_username(self, args):
		"Set the username for the GOMTV.net plugin"
		parser = argparse.ArgumentParser(description="Set the username for the GOMTV.net plugin")
		parser.add_argument('username', metavar='username', help='the username for you GOMTV.net account')

		try:
			args = parser.parse_args(args.split(" "))
		except SystemExit:
			return False

		if not args.username:
			print "username requires one argument: username [username]"
		else:
			self.args.username = args.username

	def do_password(self, args):
		"Set the password for the GOMTV.net plugin"
		self.args.password = get_password("Password: ")

class Manager():
	def __init__(self, args):
		try:
			args.cmdline = False
			args.quiet_player = True

			logger = Logger("manager")
			
			interpreter = ManagerCli(args)		
			interpreter.prompt = "livestreamer$ "
			interpreter.cmdloop()
		except KeyboardInterrupt:
			print ""
			interpreter.killAllStreams()
