import sys, os, argparse, subprocess, cmd, socket
import livestreamer
from multiprocessing import Process, Queue

from livestreamer.compat import input, stdout, is_win32
from livestreamer.logger import Logger

logger = Logger("cli")

def processArgs(args):
	return list(filter(lambda x: (len(x)>0 and x != " "), args.split(" ")))

def write_stream(fd, out, progress, queue):
	written = 0

	while True:
		try:
			#This may be causing come lag as it could still be blocking for a short amount of time.
			if queue.get(False, 0) == "kill":
				break
		except:
			pass
		try:
			data = fd.read(8192)
		except IOError:
			logger.error("Error when reading from stream")
			break

		if len(data) == 0:
			break

		try:
			out.write(data)
		except IOError:
			logger.error("Error when writing to output")
			break

		written += len(data)

		if progress:
			sys.stderr.write(("\rWritten {0} bytes").format(written))

	if progress and written > 0:
		sys.stderr.write("\n")

	logger.info("Closing stream")
	fd.close()

	if out != stdout:
		out.close()

def output_stream(stream, args, queue):
	progress = False
	out = None
	player = None

	logger.info("Opening stream {0}", args.stream)

	try:
		fd = stream.open()
	except livestreamer.StreamError as err:
		print ("Could not open stream - {0}").format(err)
		queue.put("failed")
		return False

	logger.debug("Pre-buffering 8192 bytes")
	try:
		prebuffer = fd.read(8192)
	except IOError:
		print "Failed to read data from stream"
		queue.put("failed")
		return False

	logger.debug("Checking output")

	if args.output:
		if args.output == "-":
			out = stdout
		else:
			out = check_output(args.output, args.force)
			progress = True
	elif args.stdout:
		out = stdout
	else:
		cmd = args.player
		if "vlc" in args.player:
			cmd = cmd + " - vlc://quit"

		if args.port is not None:
			cmd = cmd.replace("{PORT}", str(args.port))

		if args.quiet_player:
			pout = open(os.devnull, "w")
			perr = open(os.devnull, "w")
		else:
			pout = sys.stderr
			perr = sys.stdout

		logger.info("Starting player: {0}", args.player)
		if args.port is not None:
			logger.info("Stream port is: {0}", args.port)
		player = subprocess.Popen(cmd, shell=True, stdout=pout, stderr=perr,
								  stdin=subprocess.PIPE)
		out = player.stdin

	if not out:
		print "Failed to open a valid stream output"
		queue.put("failed")
		return False

	if is_win32:
		import msvcrt
		msvcrt.setmode(out.fileno(), os.O_BINARY)

	logger.debug("Writing stream to output")
	out.write(prebuffer)

	writeQueue = Queue()
	process = Process(target=write_stream, args=(fd, out, progress, writeQueue))
	process.start()

	queue.put("started")
	while queue.get() != "kill":
		pass

	writeQueue.put("kill")
	process.join()

	if player:
		try:
			player.kill()
		except:
			pass

def handle_url(args, queue):
	try:
		channel = livestreamer.resolve_url(args.url)
	except livestreamer.NoPluginError:
		print ("No plugin can handle URL: {0}").format(args.url)
		queue.put("failed")
		return False

	logger.info("Found matching plugin {0} for URL {1}", channel.module, args.url)

	try:
		streams = channel.get_streams()
	except livestreamer.StreamError as err:
		print str(err)
		queue.put("failed")
		return False
	except livestreamer.PluginError as err:
		print str(err)
		queue.put("failed")
		return False

	if len(streams) == 0:
		print ("No streams found on this URL: {0}").format(args.url)
		queue.put("failed")
		return False

	keys = list(streams.keys())
	keys.sort()
	validstreams = (", ").join(keys)

	if args.stream:
		if args.stream in streams:
			stream = streams[args.stream]

			output_stream(stream, args, queue)
		else:
			print ("Invalid stream quality: {0}").format(args.stream)
			print ("Valid streams: {0}").format(validstreams)
			queue.put("failed")
			return False
	else:
		print ("Found streams: {0}").format(validstreams)
		queue.put("failed")
		return False

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
		args = processArgs(args)
		
		if len(args) < 1:
			print "stream requires at least one."
			print usage
			return False
		if len(args) > 3:
			print "stream requires a maximum of 3 parameters."
			print usage
		
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

		stream = {'queue': None, 'process': None, 'info': self.args.url + " " + self.args.stream, 'port': self.args.port}
		stream['queue'] = Queue()
		stream['process'] = Process(target=handle_url, args=(self.args, stream['queue']))
		stream['process'].start()

		self.streamPool.append(stream)
		
		while stream['queue'].get() is None:
			pass

class Manager():
	def __init__(self, args):
		interpreter = ManagerCli(args)		
		interpreter.prompt = "livestreamer$ "
		interpreter.cmdloop()
