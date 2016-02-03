import re
import requests

from livestreamer.compat import str, bytes, urlparse
from livestreamer.exceptions import PluginError, NoStreamsError
from livestreamer.plugin import Plugin
from livestreamer.stream import RTMPStream
from livestreamer.utils import urlget




class N24de(Plugin):
    server = "rtmp://pssimn24livefs.fplive.net/pssimn24/"
    SWFURL = "http://www.n24.de/_swf/HomePlayer.swf"
    PageURL = "http://www.n24.de"
    

    @classmethod
    def can_handle_url(self, url):
        return "n24.de" in url

        
    def is_live_stream(self,url):
        return "pssimn24live" in url
      
    def get_video_source(self,text):
        match = re.search("ideoFlashSource = \"(.+?)\";", text)
        if not match:
            return NoStreamsError(self.url)  
        return match.group(1)	

      
    def _get_streams(self):
        if not RTMPStream.is_usable(self.session):
            raise PluginError("rtmpdump is not usable and required by Filmon plugin")
 
        self.rsession = requests.session()
        res = urlget(self.url, session=self.rsession)

        if not N24de.get_video_source(self,res.text):
            raise NoStreamsError(self.url)

        match = re.search("videoFlashconnectionUrl = \"(.+?)\";", res.text)
        if not match:
            raise NoStreamsError(self.url)  
  
        videoFlashconnectionUrl = match.group(1)
        self.server = videoFlashconnectionUrl

        if not N24de.is_live_stream(self,videoFlashconnectionUrl):
            self.playpath = N24de.get_video_source(self,res.text)
        else:
            self.playpath = "stream1"


        streams = {}
        streams["live"] = RTMPStream(self.session, {
            "rtmp": self.server,
            "playpath": self.playpath,
            "swfUrl": self.SWFURL,
            "live": True,
        })

        return streams

__plugin__ = N24de
