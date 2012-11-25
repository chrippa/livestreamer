Livestreamer
============
Livestreamer is a CLI program that launches live streams from various streaming
services in a custom video player and also a Python library that allows you to
interact with the stream data in your own application.

Current release: **1.4** (2012-11-23). See CHANGELOG for release notes.

Currently includes plugins for these sites:

* Dailymotion
* GOMTV.net (live and VOD)
* livestream.com and new.livestream.com
* ongamenet.com
* own3D.tv
* SVTPlay (live and VOD)
* Twitch/Justin.tv
* UStream
* YouTube


Dependencies
------------
Livestreamer and it's plugins currently depends on these software:

* Python version >= 2.6 or >= 3.0 (currently CPython and PyPy is known to work)
* python-setuptools or python-distribute

These will be installed automatically by the setup script if they are missing:
* python-requests (at least version 0.12.1)
* python-sh (*nix) or python-pbs (Windows)
* python-argparse (only needed for Python version 2.6)

For RTMP based plugins:
* librtmp/rtmpdump (git clone after 2011-07-31 is needed for Twitch/JustinTV plugin)


Installing (Linux, OS X etc)
---------------------------
**Release version**

Pip is a tool to install Python packages from a central repository.

    $ sudo pip install livestreamer


**Git version**

Clone or download an archive of the repository then run:

    $ sudo python setup.py install


Installing (Windows - Installer)
-----------------------------
1. Download installer from downloads and run it
2. Once installed, open  %APPDATA%\livestreamer\livestreamerrc in a text editor and make sure everything is correct
3. Use livestreamer from command prompt

*Note!* If you have previously installed manually you may need to remove livestreamer.exe from PYTHONPATH\Scripts.


Installing (Windows - Manual install)
---------------------------------
1. Install Python
2. Install Python setuptools
3. Get rtmpdump and unpack it somewhere (rtmpdump-20110925-git-6230845-win32.zip from the downloads section should work)
4. Add these paths to your Path environment variable (separate with a semicolon):
 * PYTHONPATH\
 * PYTHONPATH\Scripts\
 * RTMPDUMPPATH\ (or specify full path with --rtmpdump option)
 * PLAYERPATH\ (or specify full path with --player option)

5. **Release version** Open a command prompt and run:

        pip install livestreamer

   **Git version** Open a command prompt and change directory to livestreamer source, then run:

        python setup.py install

    This should install any missing Python dependencies automatically if they are missing.


Using
-----
    $ livestreamer --help


Saving arguments AKA config file
--------------------------------
Livestreamer can read arguments from the file ~/.livestreamerrc (POSIX) or %APPDATA%\livestreamer\livestreamerrc (Windows).
A example file:

    player=mplayer -cache 2048
    gomtv-username=username
    gomtv-password=password


Plugin specific usage
---------------------
Most plugins are straight-forward to use, just pass the URL to the stream and it will work.
However, some plugins are using what could be called a "meta URL" to find the stream for you.


**gomtv**
Passing the URL *gomtv.net* will make the plugin figure out what match is currently playing automatically.

**ongamenet**
To use this plugin the URL *ongamenet.com* must be passed. 


Common issues
-------------
**livestreamer errors with "Unable to read from stream" or "Error while executing subprocess" on Twitch/JustinTV streams.**

When building rtmpdump from source it may link with a already existing (probably older) librtmp version instead of using it's
own version. On Debian/Ubuntu it is recommended to use the official packages of *librtmp0* and *rtmpdump* version
*2.4+20111222.git4e06e21* or newer. This version contains the necessary code to play Twitch/JustinTV streams and
avoids any conflicts. It should be available in the testing or unstable repositories if it's not available in stable yet.


**VLC on Windows fails to play with a error message.**

VLC version 2.0.1 and 2.0.2 contains a bug that prevents it from reading data from stdin.
This has been fixed in version 2.0.3.


**Streams are buffering/lagging**

By default most players do not cache the input from stdin, here is a few command arguments you can pass to some common players:

MPlayer

    mplayer --cache <kbytes> (between 1024 and 8192 is recommended)


VLC

    vlc --file-caching <milliseconds> (between 1000 and 10000 is recommended)

These arguments can be used by passing --player to livestreamer.

Using livestreamer as a library
-------------------------------

http://livestreamer.readthedocs.org/

