# This file is part of Pebble.

# Pebble is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License
# as published by the Free Software Foundation,
# either version 3 of the License, or (at your option) any later version.

# Pebble is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.

# You should have received a copy of the GNU Lesser General Public License
# along with Pebble.  If not, see <http://www.gnu.org/licenses/>.

import os
import sys

from itertools import count
from functools import update_wrapper
from types import FunctionType, MethodType
from traceback import print_exc, format_exc
try:  # Python 2
    from Queue import Empty
    from cPickle import PicklingError
except:  # Python 3
    from queue import Empty
    from pickle import PicklingError

from .concurrent import concurrent as process_worker
from ..thread import concurrent as thread_worker
from ..pebble import Task, TimeoutError, TaskCancelled
from .generic import SimpleQueue, dump_function


_task_counter = count()


def task(*args, **kwargs):
    """Turns a *function* into a Process and runs its logic within.

    A decorated *function* will return a *Task* object once is called.

    If *callback* is a callable, it will be called once the task has ended
    with the task identifier and the *function* return values.

    """
    def wrapper(function):
        return TaskDecoratorWrapper(function, timeout, callback)

    # @task
    if len(args) > 0 and len(kwargs) == 0:
        if not isinstance(args[0], (FunctionType, MethodType)):
            raise ValueError("Decorated object must be function or method.")

        return TaskDecoratorWrapper(args[0], 0, None)

    # task(target=...) or @task(name=...)
    elif len(kwargs) > 0:
        timeout = kwargs.pop('timeout', 0)
        callback = kwargs.pop('callback', None)
        target = kwargs.pop('target', None)
        args = kwargs.pop('args', [])
        kwargs = kwargs.pop('kwargs', {})

        if target is not None:
            queue = SimpleQueue()

            task = ProcessTask(next(_task_counter),
                               function=target, args=args, kwargs=kwargs,
                               callback=callback, timeout=timeout,
                               queue=queue)

            task_manager(task)

            return task
        else:
            return wrapper
    else:
        raise ValueError("Decorator accepts only keyword arguments.")


@process_worker(daemon=True)
def task_worker(queue, function, args, kwargs):
    """Runs the actual function in separate process."""
    error = None
    results = None

    try:
        results = function(*args, **kwargs)
    except (IOError, OSError):
        sys.exit(1)
    except Exception as err:
        error = err
        error.traceback = format_exc()
    finally:
        try:
            queue.put(error is not None and error or results)
        except (IOError, OSError, EOFError):
            sys.exit(1)
        except PicklingError as err:
            error = err
            error.traceback = format_exc()
            queue.put(error)


@thread_worker(daemon=True)
def task_manager(task):
    """Task's lifecycle manager.

    Starts a new worker, waits for the *Task* to be performed,
    collects results, runs the callback and cleans up the process.

    """
    args = task._args
    queue = task._queue
    function = task._function
    timeout = task.timeout > 0 and task.timeout or None

    if os.name == 'nt':
        function, args = dump_function(function, args)

    process = task_worker(queue, function, task._args, task._kwargs)

    try:
        results = queue.get(timeout)
        task._set(results)
    except Empty:
        task._set(TimeoutError('Task Timeout', timeout))

    process.terminate()
    process.join()

    if task._callback is not None:
        try:
            task._callback(task)
        except Exception:
            print_exc()


class ProcessTask(Task):
    """Extends the *Task* object to support *process* decorator."""
    def __init__(self, task_nr, function=None, args=None, kwargs=None,
                 callback=None, timeout=0, identifier=None, queue=None):
        super(ProcessTask, self).__init__(task_nr, callback=callback,
                                          function=function, args=args,
                                          kwargs=kwargs, timeout=timeout,
                                          identifier=identifier)
        self._queue = queue

    def _cancel(self):
        """Overrides the *Task* cancel method in order to signal it
        to the *process* decorator handler."""
        self._cancelled = True
        self._queue.put(TaskCancelled('Task Cancelled'))


class TaskDecoratorWrapper(object):
    """Used by *task* decorator."""
    def __init__(self, function, timeout, callback):
        self._counter = count()
        self._function = function
        self._ismethod = False
        self.timeout = timeout
        self.callback = callback
        update_wrapper(self, function)

    def __get__(self, instance, owner=None):
        """Turns the decorator into a descriptor
        in order to use it with methods."""
        if instance is None:
            return self
        return MethodType(self, instance)

    def __call__(self, *args, **kwargs):
        queue = SimpleQueue()

        task = ProcessTask(next(self._counter),
                           function=self._function, args=args, kwargs=kwargs,
                           callback=self.callback, timeout=self.timeout,
                           queue=queue)

        task_manager(task)

        return task