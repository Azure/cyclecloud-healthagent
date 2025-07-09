import asyncio
from time import time, sleep, perf_counter
from healthagent.scheduler import Scheduler
import signal
import os
import sys
import functools
import pytest
import functools


@Scheduler.periodic(5)
async def periodic_task(queue: asyncio.Queue, myarg: str):

    now = time()
    try:
        queue.put_nowait((now, myarg))
    except asyncio.QueueFull:
        Scheduler.cancel_task()

@Scheduler.periodic(3)
async def periodic_task2(queue: asyncio.Queue, myarg: str = "Default"):

    now = time()
    try:
        queue.put_nowait((now, myarg))
    except asyncio.QueueFull:
        Scheduler.cancel_task()

class A:
    @Scheduler.periodic(2)
    @classmethod
    def test_periodic_classmethod(self, queue: asyncio.Queue, myarg: str = "Default"):
        now = time()
        try:
            queue.put_nowait((now, myarg))
        except asyncio.QueueFull:
            Scheduler.cancel_task()
    @staticmethod
    @Scheduler.periodic(2)
    def test_periodic_staticmethod(queue: asyncio.Queue, myarg: str = "Default"):
        now = time()
        try:
            queue.put_nowait((now, myarg))
        except asyncio.QueueFull:
            Scheduler.cancel_task()

@Scheduler.pool
def on_demand_task(sleep_t: int = 10):

    # blocking work.
    sleep(sleep_t)
    return sleep_t

def handler(signum, frame):

    signame = signal.Signals(signum).name
    print(f'Signal Received {signame} ({signum}) pid: {os.getpid()}')

    Scheduler.stop()

async def test_periodic():

    Scheduler.start()
    q1 = asyncio.Queue(maxsize=3)
    Scheduler.add_task(periodic_task, q1, "hello_from_periodic")
    # allow our task to run 3 times, after which queue will get full and our task should
    # cancel itself.
    await asyncio.sleep(15)
    # now verify the timestamps in the queue.
    # we should also have 3 values in the queue

    (t_prev, arg) = q1.get_nowait()
    assert(arg == "hello_from_periodic")
    for _ in range(2):
        (t_next, arg) = q1.get_nowait()
        assert 5.0 <= (t_next - t_prev) <= 5.05 # no more than 0.05 away from the expected call interval which is 5 seconds.
        assert(arg == "hello_from_periodic")
        t_prev = t_next
    # no more values should have been added in the queue since our task should have cancelled itself.
    with pytest.raises(asyncio.QueueEmpty):
        (t_next, arg) = q1.get_nowait()
    Scheduler.stop()

async def test_subprocess():
    Scheduler.start()
    task = Scheduler.subprocess("/bin/hostname")
    out = await Scheduler.add_task(task)
    stdout, _ = await out.communicate()
    import socket
    assert socket.gethostname() == stdout.decode().strip()
    Scheduler.stop()


async def test_on_demand():

    # This should not submit anything because scheduler is not initialized
    rc = Scheduler.add_task(on_demand_task)
    assert rc == None
    Scheduler.start()
    q1 = asyncio.Queue(maxsize=6)
    # submit a periodic task that will run every 3 seconds.
    Scheduler.add_task(periodic_task2, q1, "hello_from_periodic")
    # now submit an on-demand job that will block for 20 seconds and run in a process pool
    rc = await Scheduler.add_task(on_demand_task, 20)
    assert(rc == 20)
    # our periodic task should have run 6 times by then and cancelled it self on the 7th.
    assert(q1.qsize() == 6)
    Scheduler.stop()
    # this should not submit anything because scheduler is stopped
    rc = Scheduler.add_task(on_demand_task)
    assert rc == None

async def test_multiple_periodic():
    """
    Tests multiple periodic tasks can run at their defined intervals.
    Tests periodic tasks don't keep running after Scheduler is stopped.
    """

    Scheduler.start()
    q1 = asyncio.Queue(maxsize=15)
    Scheduler.add_task(periodic_task, q1, "hello_1")
    Scheduler.add_task(periodic_task2, q1, "hello_2")
    await asyncio.sleep(20)
    Scheduler.stop()
    # total items in the queue should be 11
    assert q1.qsize() == 11

async def test_class_periodic():
    """
    Tests periodic tasks can be defined as class methods.
    """

    Scheduler.start()
    q1 = asyncio.Queue(maxsize=5)
    Scheduler.add_task(A.test_periodic_classmethod, q1, "hello_1")
    await asyncio.sleep(11)
    Scheduler.stop()
    # total items in the queue should be 5
    assert q1.qsize() == 5

async def test_static_periodic():
    """
    Tests periodic tasks can be defined as static methods.
    """

    Scheduler.start()
    q1 = asyncio.Queue(maxsize=5)
    Scheduler.add_task(A.test_periodic_staticmethod, q1, "hello_1")
    await asyncio.sleep(11)
    Scheduler.stop()
    # total items in the queue should be 5
    assert q1.qsize() == 5

async def test_signal_handling():

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, lambda: handler(signal.SIGINT, None))
    Scheduler.start()
    q1 = asyncio.Queue(maxsize=15)
    Scheduler.add_task(periodic_task, q1, "hello_1")
    Scheduler.add_task(periodic_task2, q1, "hello_2")
    # let it run for 10 seconds, our periodic tasks would have added 6 items by then.
    await asyncio.sleep(10)
    os.kill(os.getpid(), signal.SIGINT)
    # give event loop chance to run the handler
    await asyncio.sleep(0.1)
    # check signal handler set the stop event
    assert Scheduler.stop_event.is_set() == True
    # validate our tasks dropped items in the queue as expected
    assert q1.qsize() == 6
    # validate no more tasks can be submitted
    # This should not submit anything because scheduler is not initialized
    rc = Scheduler.add_task(on_demand_task)
    assert rc == None