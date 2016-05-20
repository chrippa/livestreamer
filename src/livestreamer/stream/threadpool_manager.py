import weakref
from ..compat import Counter
from concurrent.futures import ThreadPoolExecutor, _base
from threading import Lock, RLock, Condition
from concurrent.futures.thread import _WorkItem
from collections import defaultdict


class GroupTracker(object):
    def __init__(self):
        self._counter = Counter()
        self.is_shutdown = defaultdict(lambda: Condition(Lock()))

    def increment(self, group_id):
        with self.is_shutdown[group_id]:
            self._counter[group_id] += 1

    def decrement(self, group_id):
        with self.is_shutdown[group_id]:
            new_val = self._counter[group_id] - 1

            if new_val > 0:
                self._counter[group_id] = new_val
            elif new_val == 0:
                self.is_shutdown[group_id].notify_all()
                del self._counter[group_id]
                del self.is_shutdown[group_id]
            else:
                raise ValueError("Tried to decrement counter below zero!")

    def wait_shutdown(self, group_id, timeout=None):
        with self.is_shutdown[group_id]:
            is_shutdown = self._counter[group_id]

            if not is_shutdown:
                self.is_shutdown[group_id].wait(timeout)
                is_shutdown = self._counter[group_id]
            return is_shutdown


class _GroupWorkItem(_WorkItem):
    def __init__(self, work_group_id, executor_ref, future, fn, args, kwargs):
        _WorkItem.__init__(self, future, fn, args, kwargs)
        self.work_group_id = work_group_id
        self.executor_ref = executor_ref

    def run(self):
        try:
            _WorkItem.run(self)
        finally:
            executor = self.executor_ref()
            if executor is not None:
                executor.group_tracker.decrement(self.work_group_id)
            del executor


class ThreadPoolManager(ThreadPoolExecutor):
    def __init__(self, max_workers=None):
        ThreadPoolExecutor.__init__(self, max_workers)
        self._shutdown_lock = RLock()
        self.group_tracker = GroupTracker()
        self._running_group = 0
        self._prev_group = -1

    @property
    def running_group(self):
        return self._running_group

    @property
    def prev_group(self):
        return self._prev_group

    def submit(self, fn, *args, **kwargs):
        """
        Submit work item to be executed on a thread pool.
        Optionally assign a the work item to a work group with the reserved
        work_group_id keyword argument.

        :param fn: Function to execute
        :param args: Positional arguments to pass to the function
        :param kwargs: Keyword arguments to pass to the function \r\n
                       NOTE: The work_group_id keyword argument is reserved for
                       internal use and will not be passed on to the function
        :return: A future object
        """
        with self._shutdown_lock:
            work_group_id = kwargs.pop("work_group_id", 0)
            if work_group_id is None:
                work_group_id = 0

            if self._shutdown or work_group_id != self._running_group:
                raise RuntimeError('cannot schedule new futures after shutdown')

            # Increment count of tracked work items belonging to this group
            self.group_tracker.increment(work_group_id)

            f = _base.Future()
            w = _GroupWorkItem(work_group_id, weakref.ref(self),
                               f, fn, args, kwargs)

            self._work_queue.put(w)
            self._adjust_thread_count()
            return f

    def set_running_group(self, work_group_id, wait_shutdown=False):
        with self._shutdown_lock:
            self._prev_group = self._running_group
            self._running_group = work_group_id

        if wait_shutdown:
            return self.group_tracker.wait_shutdown(self._prev_group)
        else:
            return True

    def shutdown(self, wait=True):
        with self._shutdown_lock:
            # Trigger shutdown msg on all groups
            self.set_running_group(self._prev_group, wait_shutdown=False)

            self._shutdown = True
            self._work_queue.put(None)
        if wait:
            for t in self._threads:
                t.join()

