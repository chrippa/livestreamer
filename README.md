Livestreamer
============
Livestreamer is a CLI program that launches streams from various
streaming services in a custom video player.

Currently supported sites are:

* GOMTV.net
* Justin.tv/Twitch.tv
* Own3d.tv
* SVTPlay
* UStream
* YouTube

Note: Justin.tv plugin requires rtmpdump with jtv token support (recent git).

Livestreamer is compatible with Python version >= 2.6 and >= 3.0.


Installing (Linux, OS X etc)
----------
Make sure you have Python and Python setuptools then run:

    $ sudo python setup.py install


Installing (Windows)
--------------------
1. Install Python
2. Install Python setuptools
3. Get rtmpdump and unpack it somewhere (rtmpdump-20110925-git-6230845-win32.zip from http://rtmpdump.mplayerhq.hu/ should work)
4. Add these paths to your Path environment variable:
  * [Python path]\
  * [Python path]\scripts\
  * [rtmpdump path]\ (or specify full path with --rtmpdump option)
  * [VLC/mplayer/other path]\ (or specify full path with --player option)

5. Open a command prompt and change directory to livestreamer source, then run:

    python setup.py install

Note: If you want to use VLC be aware there is currently a bug in version 2.0.1/2.0.2
that prevents stdin reading from working. The bug has been fixed in version 2.0.3.


Using
-----
    $ livestreamer --help


Manager
-------
The manager can be use to manage multiple instances and stream it out again.
This was created to use with XSplit so you can import streams from all the
websites listed.

The Manager and XSplit
----------------------
Set your player argument though the config file or command line to:

	vlc --sout=#rtp{sdp=rtsp://:{PORT}/} --no-sout-rtp-sap --no-sout-standard-sap --ttl=1 --sout-keep

	You can adjust this as you wish only if you know what you are doing.

Now run Livestreamer with -m to start in manager mode.

	livestream -m

To start a stream you can use the following. The port is optional.

	stream url stream [port]

In XSplit add the stream using the ip camera source with the url:

	rtsp://localhost:[port]/\\rtsp_transport:udp

	The port is the port listed in the livestreamer command line.


Saving arguments AKA config file
--------------------------------
Livestreamer can read arguments from the file ~/.livestreamer.conf on Unix based operating systems
and %APPDATA%\livestreamer.conf on windows based operating systems.
A example file:

    player=mplayer
    jtv-cookie=_jtv3_session_id=arandomhash
    username=username
    password=password


Using livestreamer as a library
-------------------------------
Livestreamer is also a library. Short example:

    import livestreamer

    url = "http://twitch.tv/day9tv"
    channel = livestreamer.resolve_url(url)
    streams = channel.get_streams()

    stream = streams["720p"]
    fd = stream.open()

    while True:
        data = fd.read(1024)
        if len(data) == 0:
            break

        # do something with data

    fd.close()

