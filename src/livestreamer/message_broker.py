from collections import defaultdict, deque
from .compat import queue
from threading import Condition, Lock
from livestreamer.exceptions import RegistrationFailed, DeliveryFailed, DeliveryTimeout, NotSubscribed, MailboxTimeout

from time import time as _time


def filter_get(self, filter_fn, *args, **kwargs):
    """Remove and return a matching item from the queue.

    If optional args 'block' is true and 'timeout' is None (the default),
    block if necessary until a matching item is available. If 'timeout' is
    a non-negative number, it blocks at most 'timeout' seconds and raises
    the Empty exception if no matching item was available within that time.
    Otherwise ('block' is false), return an item if one is immediately
    available and passes the filter function, else raise the Empty exception
    ('timeout' is ignored in that case).
    """
    block = kwargs.pop("block", True)
    timeout = kwargs.pop("timeout", None)

    def find_match():
        """Check for matching messages in queue return (match, pos) tuple"""
        match = False
        pos = -1
        for pos, msg in enumerate(self.queue):
            match = filter_fn(msg, *args, **kwargs)
            if match:
                break

        return match, pos

    def fetch(pos):
        """Return item at position and remove it"""
        item = self.queue[pos]
        del self.queue[pos]
        return item

    self.not_empty.acquire()
    try:
        match = False
        pos = -1
        if not block:
            if not self._qsize():
                raise queue.Empty
        elif timeout is None:
            # Until match is found
            while True:
                # Check for matching message in queue
                match, pos = find_match()

                # Break immediately if we found a match so we don't block
                # indefinitely waiting on a new message to arrive
                if match:
                    break

                # Wait for a new message to be placed on the queue
                self.not_empty.wait()
        elif timeout < 0:
            raise ValueError("'timeout' must be a non-negative number")
        else:
            endtime = _time() + timeout
            while True:
                # Check for matching message in queue
                match, pos = find_match()

                # Break immediately if we found a match so we don't block
                # indefinitely waiting on a new message to arrive
                if match:
                    break

                # Raise queue.Empty exception if we have timed out
                remaining = endtime - _time()
                if remaining <= 0.0:
                    raise queue.Empty

                # Wait for a new message to be placed on the queue
                self.not_empty.wait(remaining)

        # Return item at position where match occurred
        item = fetch(pos)
        self.not_full.notify()
        return item
    finally:
        self.not_empty.release()

# Add this function as a method on the Queue class
queue.Queue.filter_get = filter_get


# Function used to filter the message queue for a matching source handle
def source_filter(msg, source_handle):
    return msg.source.handle == source_handle


class HandleableMsg(object):
    def __init__(self, msg_handle, msg_data=None, source=None):
        self.msg_handle = msg_handle
        self.data = msg_data
        self.source = source
        self.handled_sig = Condition(Lock())
        self._handled = False

    @property
    def handled(self):
        return self._handled

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.set_handled()

    def __nonzero__(self):
        return self.__bool__()

    def __bool__(self):
        return self.msg_handle is not None

    def wait(self, timeout=None):
        with self.handled_sig:
            handled = self._handled
            if not handled:
                self.handled_sig.wait(timeout)
                handled = self._handled
            return handled

    def set_handled(self):
        with self.handled_sig:
            self._handled = True
            self.handled_sig.notify_all()

    def is_handled(self):
        return self._handled


class Mailbox(object):
    def __init__(self, handle, msg_broker):
        self.handle = handle
        self.msg_broker = msg_broker
        self._msg_queues = {}
        self._dict_lock = Lock()

    def _check_msg_queue_exists(self, msg_handle):
        with self._dict_lock:
            if self._msg_queues.get(msg_handle) is None:
                self._msg_queues[msg_handle] = queue.Queue()

    def deliver(self, msg_handle, msg_data, source=None):
        self._check_msg_queue_exists(msg_handle)

        # Add message to delivery queue
        msg = HandleableMsg(msg_handle, msg_data, source)
        self._msg_queues[msg_handle].put(msg)

        # return message in case we need to wait for it to be handled
        return msg

    def get(self, msg_handle, block=False, source=None, timeout=None):
        self._check_msg_queue_exists(msg_handle)

        msg_queue = self._msg_queues[msg_handle]
        try:
            if source is None:
                return msg_queue.get(block, timeout)
            else:
                return msg_queue.filter_get(source_filter, source,
                                            block=block,
                                            timeout=timeout)
        except queue.Empty:
            if block and timeout is not None:
                raise MailboxTimeout("Timed out waiting for message '{0}'"
                                     .format(msg_handle))
            else:
                # Wrap None in HandleableMsg so we can always use the 'with' statement
                return HandleableMsg(None)

    def wait_on_msg(self, msg_handle, source=None, timeout=None, leave_msg=False):
        """
        Block until the specified message has been received.

        When invoked with the leave_msg argument set to False (the default),
        set the received message as handled and discard it.

        When invoked with the leave_msg argument set to True, restore the
        received message to the queue in its unhandled state.

        Raises MailboxTimeout exception on timeout.
        """
        msg = self.get(msg_handle, block=True, source=source, timeout=timeout)

        # If the message was empty we have timed out and we should raise an exception
        if not msg:
            raise MailboxTimeout("Timed out waiting for message '{0}'"
                                 .format(msg_handle))

        # Discard message or put it back on the queue to be handled later
        if leave_msg:
            self._msg_queues[msg_handle].put(msg)
        else:
            msg.set_handled()

    def send(self, msg_handle, msg_data=None, target=None, block=False, timeout=None):
        self.msg_broker.send(msg_handle, msg_data, self, target, block, timeout)

    def subscribe(self, msg_handle):
        self.msg_broker.subscribe(self, msg_handle)

    def unsubscribe(self, msg_handle):
        self.msg_broker.unsubscribe(self, msg_handle)


class MessageBroker(object):
    def __init__(self):
        self.mailboxes = {}
        self.subscribers = defaultdict(lambda: [])
        self._lock = Lock()

    def register(self, mailbox_handle):
        with self._lock:
            mailbox = Mailbox(mailbox_handle, self)

            if mailbox_handle not in self.mailboxes:
                self.mailboxes[mailbox_handle] = mailbox
            else:
                raise RegistrationFailed("Unable to register mailbox, "
                                         "a mailbox with that name already exists")
    
            return mailbox

    def send_to_target(self, target, msg_handle, msg_data=None, source=None,
                       wait_handled=False, timeout=None):
        target_mailbox = self.mailboxes.get(target)
        if target_mailbox is None:
            raise DeliveryFailed("Unable to deliver '{0}' to target '{1}'. "
                                 "Target mailbox does not exist"
                                 .format(msg_handle, target))

        msg = target_mailbox.deliver(msg_handle, msg_data, source)

        if wait_handled:
            # Wait on handled
            msg.wait(timeout)
            if not msg.handled:
                raise DeliveryTimeout("Timed out waiting for message '{0}' to be handled"
                                      .format(msg_handle))
        else:
            # Handled on receive
            msg.set_handled()

    def send(self, msg_handle, msg_data=None, source=None, target=None,
             wait_handled=False, timeout=None):
        """
        Send a message. If target is not set then the message will broadcast
        to all subscribers. If target is set then a directed message will
        be sent to the target mailbox. Directed messages will be delivered
        do not require a subscription to be received by the target mailbox.
        If the target mailbox is not found then a DeliveryFailed error is
        raised.

        If the wait_handled parameter is set to true (default) this method
        will block until all receivers have signaled that they have handled
        the message.
        """
        # Send directed message if target is set
        if target is not None:
            self.send_to_target(target, msg_handle, msg_data, source,
                                wait_handled, timeout)

        # Broadcast message
        else:
            # Get subscribed mailboxes
            subscribers = self.subscribers.get(msg_handle)
            if subscribers is None or len(subscribers) < 1:
                raise DeliveryFailed("No subscribers found to deliver message '{0}' to"
                                     .format(msg_handle))

            # Send handleable message to all subscribed mailboxes
            msgs = []
            for mailbox in subscribers:
                msg = mailbox.deliver(msg_handle, msg_data, source)
                msgs.append(msg)

            if wait_handled:
                # Wait for messages to be handled
                handled_flags = []
                for msg in msgs:
                    handled_flags.append(msg.wait(timeout))

                # Raise error on timeouts
                not_handled_count = 0
                for handled in handled_flags:
                    if not handled:
                        not_handled_count += 1
                if not_handled_count > 0:
                    raise DeliveryTimeout("Timed out waiting on {0} message(s) to be handled"
                                          .format(not_handled_count))
            else:
                # Handled on receive
                for msg in msgs:
                    msg.set_handled()

    def subscribe(self, mailbox, msg_handle):
        with self._lock:
            self.subscribers[msg_handle].append(mailbox)

    def unsubscribe(self, mailbox, msg_handle):
        with self._lock:
            if (self.subscribers.get(msg_handle) is None or
                    mailbox not in self.subscribers[msg_handle]):
                raise NotSubscribed("Not subscribed to message: {0}"
                                    .format(msg_handle))

            self.subscribers[msg_handle].remove(mailbox)

            if len(self.subscribers.get(msg_handle)) == 0:
                del self.subscribers[msg_handle]


Mailbox.send.__func__.__doc__ = MessageBroker.send.__func__.__doc__
Mailbox.subscribe.__func__.__doc__ = MessageBroker.subscribe.__func__.__doc__
Mailbox.unsubscribe.__func__.__doc__ = MessageBroker.unsubscribe.__func__.__doc__
