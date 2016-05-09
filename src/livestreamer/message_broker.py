from collections import defaultdict, deque
from .compat import queue
from threading import Condition, Lock
from livestreamer.exceptions import RegistrationFailed, DeliveryFailed, DeliveryTimeout, NotSubscribed, MailboxTimeout


def msg_filter(queue_, wait, timeout, filter_fn, *args, **kwargs):
    match = False
    saved_msgs = deque()
    msg = None
    while not match:
        # Unblocks on new msgs
        msg = queue_.get(wait, timeout)

        match = filter_fn(msg, *args, **kwargs)
        if match:
            # Restore saved messages to the message queue
            while len(saved_msgs) > 0:
                queue_.put(saved_msgs.popleft())
        else:
            # Save unmatching messages
            saved_msgs.append(msg)

    return msg


def source_filter(msg, source):
    return msg.source.handle == source


class HandleableMsg(object):
    def __init__(self, msg_handle, msg_data=None, source=None):
        self.__nonzero__ = self.__bool__
        self.msg_handle = msg_handle
        self.data = msg_data
        self.source = source
        self.handled_sig = Condition(Lock())
        self._handled = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.set_handled()

    def __bool__(self):
        return self.msg_handle is not None

    def wait(self, timeout=None):
        with self.handled_sig:
            handled = self._handled
            if not handled:
                handled = self.handled_sig.wait(timeout)
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

    def get(self, msg_handle, wait=False, source=None, timeout=None):
        self._check_msg_queue_exists(msg_handle)

        msg_queue = self._msg_queues[msg_handle]
        try:
            if source is None:
                return msg_queue.get(wait, timeout)
            else:
                return msg_filter(msg_queue, wait, timeout, source_filter, source)
        except queue.Empty:
            if wait and timeout is not None:
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
        msg = self.get(msg_handle, wait=True, source=source, timeout=timeout)

        # If the message was empty we have timed out and we should raise an exception
        if not msg:
            raise MailboxTimeout("Timed out waiting for message '{0}'"
                                 .format(msg_handle))

        # Discard message or put it back on the queue to be handled later
        if leave_msg:
            self._msg_queues[msg_handle].put(msg)
        else:
            msg.set_handled()

    def send(self, msg_handle, msg_data=None, target=None, wait=False, timeout=None):
        self.msg_broker.send(msg_handle, msg_data, self, target, wait, timeout)

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

    def send_to_target(self, target, msg_handle, msg_data=None, source=None, wait=False, timeout=None):
        target_mailbox = self.mailboxes.get(target)
        if target_mailbox is None:
            raise DeliveryFailed("Unable to deliver '{0}' to target '{1}'. "
                                 "Target mailbox does not exist"
                                 .format(msg_handle, target))

        msg = target_mailbox.deliver(msg_handle, msg_data, source)

        if wait:
            # Wait on handled
            handled = msg.wait(timeout)
            if not handled:
                raise DeliveryTimeout("Timed out waiting for message '{0}' to be handled"
                                      .format(msg_handle))
        else:
            # Handled on receive
            msg.set_handled()

    def send(self, msg_handle, msg_data=None, source=None, target=None, wait=False, timeout=None):
        """
        Send a message. If target is not set then the message will broadcast
        to all subscribers. If target is set then a directed message will
        be sent to the target mailbox. Directed messages will be delivered
        do not require a subscription to be received by the target mailbox.
        If the target mailbox is not found then a DeliveryFailed error is
        raised.

        If the wait parameter is set to true (default) this method will block
        until all receivers have signaled that they have handled the message.
        """
        # Send directed message if target is set
        if target is not None:
            self.send_to_target(target, msg_handle, msg_data, source, wait, timeout)

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

            if wait:
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
