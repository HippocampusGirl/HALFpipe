# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

from typing import Optional, Union

import logging
from multiprocessing import get_context
from multiprocessing.queues import JoinableQueue
from multiprocessing.process import BaseProcess
from threading import RLock

from .worker import run as run_worker, MessageSchema

ctx = get_context("forkserver")

schema = MessageSchema()

rlock = RLock()


class NullQueue:
    def put(self, _) -> None:
        pass

    def join(self) -> None:
        pass


class context(object):
    _instance = None

    def __init__(self):
        self._queue: Union[NullQueue, JoinableQueue] = NullQueue()
        self._worker: Optional[BaseProcess] = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            with rlock:
                if cls._instance is None:
                    cls._instance = cls()

        return cls._instance

    @classmethod
    def setup_worker(cls):
        instance = cls.instance()
        with rlock:
            if not isinstance(instance._queue, JoinableQueue):
                instance._queue = JoinableQueue(ctx=ctx)
            if instance._worker is None:
                instance._worker = ctx.Process(target=run_worker, args=(instance._queue,))
                instance._worker.start()

    @classmethod
    def teardown_worker(cls):
        with rlock:
            instance = cls._instance
            if instance is None:
                return

            queue = instance._queue
            worker = instance._worker
            if worker is None or not isinstance(queue, JoinableQueue):
                return

            # wait for queue to empty
            queue.join()

            # send message with teardown command
            obj = schema.dump({"type": "teardown"})
            queue.put(obj)

            # wait up to one second
            worker.join(1.0)

            queue.close()

            instance._queue = NullQueue()
            instance._worker = None

    @classmethod
    def queue(cls):
        return cls.instance()._queue

    @classmethod
    def logging_args(cls):
        return dict(
            queue=cls.queue(),
            levelno=logging.getLogger("halfpipe").level
        )

    @classmethod
    def enable_verbose(cls):
        obj = schema.dump({"type": "enable_verbose"})
        queue = cls.queue()
        queue.put(obj)

    @classmethod
    def enable_print(cls):
        obj = schema.dump({"type": "enable_print"})
        queue = cls.queue()
        queue.put(obj)
        queue.join()

    @classmethod
    def disable_print(cls):
        obj = schema.dump({"type": "disable_print"})
        queue = cls.queue()
        queue.put(obj)
        queue.join()

    @classmethod
    def set_workdir(cls, workdir):
        obj = schema.dump({"type": "set_workdir", "workdir": workdir})
        queue = cls.queue()
        queue.put(obj)
