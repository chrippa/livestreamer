from collections import defaultdict
from .compat import queue, is_py3
from threading import Condition, Lock, RLock
from livestreamer.exceptions import RegistrationFailed, DeliveryFailed, DeliveryTimeout, NotSubscribed, MailboxTimeout, \
    MailboxClosed

from time import time


class MessageQueue(queue.Queue):
    def __init__(self, maxsize=0):
        queue.Queue.__init__(self, maxsize)

        # Want RLock so that we can acquire the  lock externally
        self.mutex = RLock()
        self.not_empty = Condition(self.mutex)
        self.not_full = Condition(self.mutex)
        self.all_tasks_done = Condition(self.mutex)
        self.dummy_cond = Condition(self.mutex)

        self.closed = False

    # TODO: Replace try finally with context manager wrappers
    def qsize(self):
        if self.closed:
            raise MailboxClosed

        try:
            return queue.Queue.qsize(self)
        finally:
            if self.closed:
                raise MailboxClosed

    def empty(self):
        if self.closed:
            raise MailboxClosed

        try:
            return queue.Queue.empty(self)
        finally:
            if self.closed:
                raise MailboxClosed

    def full(self):
        if self.closed:
            raise MailboxClosed

        try:
            return queue.Queue.full(self)
        finally:
            if self.closed:
                raise MailboxClosed

    def task_done(self):
        if self.closed:
            raise MailboxClosed

        try:
            return queue.Queue.task_done(self)
        finally:
            if self.closed:
                raise MailboxClosed

    def put(self, item, block=True, timeout=None):
        with self.mutex:
            if self.closed:
                raise MailboxClosed

            try:
                return queue.Queue.put(self, item, block, timeout)
            finally:
                if self.closed:
                    # Clean up the message we placed on the queue
                    for i, msg in enumerate(self.queue):
                        if msg is item:
                            del self.queue[i]
                            break

                    raise MailboxClosed

    def get(self, block=True, timeout=None, leave_msg=False):
        with self.mutex:
            if self.closed:
                raise MailboxClosed

            # Change behavior of get by changing internally called _get method
            # and also making sure that the not_full condition isn't notified
            # when leaving messages.
            if leave_msg:
                get_backup = self._get
                not_full_backup = self.not_full
                self._get = self._peek
                self.not_full = self.dummy_cond

            try:
                return queue.Queue.get(self, block, timeout)
            finally:
                # Restore any changes made _get method as well as the not_full
                # condition
                if leave_msg:
                    self.not_full = not_full_backup
                    self._get = get_backup

                if self.closed:
                    raise MailboxClosed

    def _peek(self):
        return self.queue[0]

    def join(self):
        with self.mutex:
            if self.closed:
                raise MailboxClosed

            try:
                queue.Queue.join(self)
            finally:
                if self.closed:
                    raise MailboxClosed

    def filter_get(self, filter_fn, *args, **kwargs):
        """Remove and return a matching item from the queue.

        If optional, keyword only args, 'block' is true and 'timeout' is None
        (the default), block if necessary until a matching item is available.
        If 'timeout' is a non-negative number, it blocks at most 'timeout'
        seconds and raises the Empty exception if no matching item was
        available within that time. Otherwise ('block' is false), return an
        item if one is immediately available and passes the filter function,
        else raise the Empty exception ('timeout' is ignored in that case).

        If invoked with the optional keyword only argument, leave_msg, set to
        True, then return matching messages without removing them from the
        queue.
        """
        block = kwargs.pop("block", True)
        timeout = kwargs.pop("timeout", None)
        leave_msg = kwargs.pop("leave_msg", False)

        def find_match():
            """Check for matching messages in queue return (match, pos) tuple"""
            match = False
            pos = -1
            for pos, msg in enumerate(self.queue):
                match = filter_fn(msg, *args, **kwargs)
                if match:
                    break

            return match, pos

        def fetch(pos, leave_msg=False):
            """Return item at position and remove it, unless leave_msg is True."""
            item = self.queue[pos]
            if not leave_msg:
                del self.queue[pos]
            return item

        with self.not_empty:
            if self.closed:
                raise MailboxClosed

            try:
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
                    endtime = time() + timeout
                    while True:
                        # Check for matching message in queue
                        match, pos = find_match()

                        # Break immediately if we found a match so we don't block
                        # indefinitely waiting on a new message to arrive
                        if match:
                            break

                        # Raise queue.Empty exception if we have timed out
                        remaining = endtime - time()
                        if remaining <= 0.0:
                            raise queue.Empty

                        # Wait for a new message to be placed on the queue
                        self.not_empty.wait(remaining)

                # Return item at position where match occurred
                item = fetch(pos, leave_msg=leave_msg)
                if not leave_msg:
                    self.not_full.notify()
                return item
            finally:
                if self.closed:
                    raise MailboxClosed

    def close(self):
        with self.mutex:
            if not self.closed:
                self.closed = True

                self.maxsize = 0  # Make sure we don't block on full queues
                self._qsize = lambda: 1  # Make sure we don't block on empty queues

                self.not_empty.notify_all()
                self.not_full.notify_all()
                self.all_tasks_done.notify_all()


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
        self.closed = False

    def _check_msg_queue_exists(self, msg_handle):
        with self._dict_lock:
            if self._msg_queues.get(msg_handle) is None:
                self._msg_queues[msg_handle] = MessageQueue()

    def deliver(self, msg_handle, msg_data, source=None):
        self._check_msg_queue_exists(msg_handle)

        # Add message to delivery queue
        msg = HandleableMsg(msg_handle, msg_data, source)
        self._msg_queues[msg_handle].put(msg)

        # return message in case we need to wait for it to be handled
        return msg

    def get(self, msg_handle, block=False, leave_msg=False, source=None, timeout=None):
        self._check_msg_queue_exists(msg_handle)

        msg_queue = self._msg_queues[msg_handle]
        try:
            if source is None:
                msg = msg_queue.get(block, timeout, leave_msg=leave_msg)
            else:
                msg = msg_queue.filter_get(source_filter, source,
                                           block=block,
                                           timeout=timeout,
                                           leave_msg=leave_msg)

            return msg

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
        # The get method will decide whether to leave the message on the queue
        msg = self.get(msg_handle, block=True, leave_msg=leave_msg, source=source, timeout=timeout)

        # If the message was empty we have timed out and we should raise an exception
        if not msg:
            raise MailboxTimeout("Timed out waiting for message '{0}'"
                                 .format(msg_handle))

        if not leave_msg:
            msg.set_handled()

    def close(self):
        if not self.closed:
            self.closed = True

            # Close all message queues
            for msg_handle in self._msg_queues:
                msg_queue = self._msg_queues[msg_handle]
                with msg_queue.mutex:

                    # Mark all messages in the queue as handled
                    for msg in msg_queue.queue:
                        msg.set_handled()

                    # Close the message queue waking all threads waiting on it
                    msg_queue.close()

            # De-register the mailbox with the message broker
            self.msg_broker.deregister(self)

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

    def deregister(self, mailbox):
        with self._lock:
            for msg_handle in self.subscribers:
                sub_mailboxes = self.subscribers[msg_handle]
                if mailbox in sub_mailboxes:
                    sub_mailboxes.remove(mailbox)

            del self.mailboxes[mailbox.handle]

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

if is_py3:
    Mailbox.send.__doc__ = MessageBroker.send.__doc__
    Mailbox.subscribe.__doc__ = MessageBroker.subscribe.__doc__
    Mailbox.unsubscribe.__doc__ = MessageBroker.unsubscribe.__doc__
else:
    Mailbox.send.__func__.__doc__ = MessageBroker.send.__func__.__doc__
    Mailbox.subscribe.__func__.__doc__ = MessageBroker.subscribe.__func__.__doc__
    Mailbox.unsubscribe.__func__.__doc__ = MessageBroker.unsubscribe.__func__.__doc__

