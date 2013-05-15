import os
import unittest

from livestreamer import Livestreamer, PluginError, NoPluginError
from livestreamer.plugins import Plugin
from livestreamer.stream import *


class TestSession(unittest.TestCase):
    PluginPath = os.path.join(os.path.dirname(__file__), "plugins")

    def setUp(self):
        self.session = Livestreamer()
        self.session.load_plugins(self.PluginPath)

    def test_exceptions(self):
        try:
            self.session.resolve_url("invalid url")
            self.assertTrue(False)
        except NoPluginError:
            self.assertTrue(True)

    def test_load_plugins(self):
        plugins = self.session.get_plugins()
        self.assertTrue(plugins["testplugin"])

    def test_builtin_plugins(self):
        plugins = self.session.get_plugins()
        self.assertTrue("justintv" in plugins)

    def test_resolve_url(self):
        plugins = self.session.get_plugins()
        channel = self.session.resolve_url("http://test.se/channel")
        self.assertTrue(isinstance(channel, Plugin))
        self.assertTrue(isinstance(channel, plugins["testplugin"]))

    def test_options(self):
        self.session.set_option("test_option", "option")
        self.assertEqual(self.session.get_option("test_option"), "option")
        self.assertEqual(self.session.get_option("non_existing"), None)

        self.assertEqual(self.session.get_plugin_option("testplugin", "a_option"), "default")
        self.session.set_plugin_option("testplugin", "another_option", "test")
        self.assertEqual(self.session.get_plugin_option("testplugin", "another_option"), "test")
        self.assertEqual(self.session.get_plugin_option("non_existing", "non_existing"), None)
        self.assertEqual(self.session.get_plugin_option("testplugin", "non_existing"), None)

    def test_plugin(self):
        channel = self.session.resolve_url("http://test.se/channel")
        streams = channel.get_streams()

        self.assertTrue("best" in streams)
        self.assertTrue("worst" in streams)
        self.assertTrue(streams["best"] is streams["1080p"])
        self.assertTrue(streams["worst"] is streams["350k"])
        self.assertTrue(isinstance(streams["rtmp"], RTMPStream))
        self.assertTrue(isinstance(streams["http"], HTTPStream))
        self.assertTrue(isinstance(streams["hls"], HLSStream))
        self.assertTrue(isinstance(streams["akamaihd"], AkamaiHDStream))

    def test_plugin_stream_priority(self):
        channel = self.session.resolve_url("http://test.se/channel")
        streams = channel.get_streams(priority=["http", "rtmp"])

        self.assertTrue(isinstance(streams["480p"], HTTPStream))
        self.assertTrue(isinstance(streams["480p_rtmp"], RTMPStream))

        streams = channel.get_streams(priority=["rtmp", "http"])

        self.assertTrue(isinstance(streams["480p"], RTMPStream))
        self.assertTrue(isinstance(streams["480p_http"], HTTPStream))

if __name__ == "__main__":
    unittest.main()
