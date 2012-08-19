import sys, os, argparse, subprocess, cmd, socket
import livestreamer
from multiprocessing import Process, Queue

from livestreamer.compat import input, stdout, is_win32
from livestreamer.logger import Logger
from livestreamer.stream import StreamHandler

logger = Logger("manager")

def processArgs(args, usage="", min=None, max=None):
	args = list(filter(lambda x: (len(x)>0 and x != " "), args.split(" ")))
	
	if min is not None:
		if len(args) < min:
			print "stream requires at least one."
			print usage
			return False
	
	if max is not None:
		if len(args) > max:
			print "stream requires a maximum of 3 parameters."
			print usage
			return False

	return True

class ManagerCli(cmd.Cmd):
	def __init__(self, args):
		cmd.Cmd.__init__(self)
		self.args = args
		self.streamPool = []

	def do_kill(self, args):
		usage = "Usage: kill streamid"
		args = processArgs(args)

		if len(args) != 1:
			print "kill requires a parameter."
			print usage
			return False
		try:
			streamId = int(args[0])
		except:
			print "streamid must be a digit."
			print usage
			return False

		try:
			stream = self.streamPool[streamId]
		except:
			print "Not a valit streamid."
			print "Use the list command to list all streams"
			return False

		while True:
			print "This is the stream for", stream['info']
			a = raw_input("Are you sure you want to kill it? (y/n) ").lower()
			if "y" in a:
				self.killStream(stream)
				self.joinStream(stream)
				return False
			elif "n" in a:
				return False

	def killStream(self, stream):
		stream['queue'].put('kill')

	def joinStream(self, stream):
		stream['process'].join()
		self.streamPool.remove(stream)

	def killAllStreams(self):
		for stream in self.streamPool:
			self.killStream(stream)
		for stream in self.streamPool:
			self.joinStream(stream)

	def removeStaleStreams(self):
		for stream in self.streamPool:	
			stream['process'].join(timeout=0.1)
			if not stream['process'].is_alive():
				self.streamPool.remove(stream)

	def exit(self):
		if(self.is_running_streams()):
			while True:
				print "There are streams still running!"
				a = raw_input("Are you sure you want to quit? (y/n) ").lower()
				if "y" in a:
					self.killAllStreams()
					return True
				elif "n" in a:
					return False

		return True

	def do_exit(self, line):
		return self.exit()

	def do_EOF(self, line):
		return self.exit()

	def do_list(self, line):
		self.removeStaleStreams()
		i = 0
		print "ID\tStream"
		for stream in self.streamPool:
			print str(i) + "\t" + stream['info']
			i = i + 1

		if i == 0:
			print "There are no streams running"

	def is_running_streams(self):
		self.removeStaleStreams()
		return len(self.streamPool) > 0

	def nextPort(self, minPort, maxPort):
		for port in range(minPort, maxPort):
			if self.checkPort(port):
				return port

	def checkPort(self, port):
		s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		try:
			s.bind(("", port))
		except:
			return False
		s.close()
		return True

	def do_stream(self, args):
		usage = "Usage: url [stream] [port]"
		args = processArgs(args, usage, 1, 3)
		
		self.args.url = args[0]
		if len(args) > 1:
			self.args.stream = args[1]
			
		self.args.port = None
		if "{PORT}" in self.args.player:
			if len(args) > 2:
				self.args.port = args[2]				
				if not self.checkPort(self.args.port):
					print "The port", str(self.args.port), "is already in use."
					return False
			else:
				self.args.port = self.nextPort(self.args.min_port, self.args.max_port)

			# Put the port into.
			self.args.player = self.args.player.replace("{PORT}", str(self.args.port))

		stream = {'queue': None, 'process': None, 'info': None, 'port': None}


		if self.args.stream:
			stream['info'] = self.args.url + " " + self.args.stream
		else:
			stream['info'] = self.args.url

		stream['queue'] = Queue()
		stream['process'] = Process(target=StreamHandler, args=(self.args, stream['queue']))
		stream['process'].start()

		self.streamPool.append(stream)
		
		# Loop until we get a response as it will be pushing stuff 
		# to the logger and we dont want to clobber any input.
		while stream['queue'].get() is None:
			pass

class Manager():
	def __init__(self, args):
		try:
			args.cmdline = False
			args.quiet_player = True
			logger.set_level(args.loglevel)
			
			interpreter = ManagerCli(args)		
			interpreter.prompt = "livestreamer$ "
			interpreter.cmdloop()
		except KeyboardInterrupt:
			print ""
			interpreter.killAllStreams()
