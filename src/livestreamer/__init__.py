"""Livestreamer extracts streams from various services.

The main compontent of Livestreamer is a CLI program that launches
streams the streams in a video player.

An API is also provided that allows direct access to stream data.

Full documentation is available at http://livestreamer.tanuki.se/.

"""


__title__ = "livestreamer"
__version__ = "1.7.1"
__license__ = "Simplified BSD"
__author__ = "Christopher Rosell"
__copyright__ = "Copyright 2011-2013 Christopher Rosell"
__credits__ = ["Christopher Rosell", "Athanasios Oikonomou",
               "Gaspard Jankowiak", "Dominik Dabrowski",
               "Toad King", "Niall McAndrew", "Daniel Wallace",
               "Sam Edwards", "John Peterson", "Kacper"]


from .exceptions import (LivestreamerError, PluginError, NoStreamsError,
                         NoPluginError, StreamError)
from .session import Livestreamer
