class LivestreamerError(Exception):
    """Any error caused by Livestreamer will be caught
       with this exception."""


class PluginError(LivestreamerError):
    """Plugin related error."""


class NoStreamsError(LivestreamerError):
    def __init__(self, url):
        self.url = url
        err = "No streams found on this URL: {0}".format(url)
        Exception.__init__(self, err)


class NoPluginError(PluginError):
    """No relevant plugin has been loaded."""


class StreamError(LivestreamerError):
    """Stream related error."""


class MessageBrokerError(LivestreamerError):
    """Message broker related related errors"""


class NotSubscribed(MessageBrokerError):
    """Tried to unsubscribe from a message we weren't subscribed to"""


class RegistrationFailed(MessageBrokerError):
    """Failed to register a mailbox with the message broker"""


class MailboxTimeout(MessageBrokerError):
    """Mailbox timed out wait for message"""


class MailboxClosed(MessageBrokerError):
    """Tried to call a method on a closed mailbox"""


class DeliveryFailed(MessageBrokerError):
    """Was unable to deliver a message"""


class DeliveryTimeout(DeliveryFailed):
    """Timeout occurred while trying to deliver a message"""


__all__ = ["LivestreamerError", "PluginError", "NoPluginError",
           "NoStreamsError", "StreamError"]
