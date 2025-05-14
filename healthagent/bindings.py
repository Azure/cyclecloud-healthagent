from ctypes import *
import os
import sys
from time import time
import asyncio
from healthagent.AsyncScheduler import AsyncScheduler,Priority
DCGM_VERSION = os.getenv("DCGM_VERSION")

try:
    if DCGM_VERSION < '4.0.0':
        print("DCGM version is less than 4.0.0, which is not supported.")
        raise ImportError("Unsupported DCGM version")
    bind_path = "/usr/share/datacenter-gpu-manager-4/bindings/python3"

    sys.path.append(bind_path)
    import pydcgm
    from dcgm_structs import dcgmExceptionClass
    import dcgm_structs
    import dcgm_fields
    import dcgm_agent
    import dcgmvalue
    import DcgmFieldGroup
except:
    raise ImportError("Unable to find dcgm python binding, is PYTHONPATH set properly?")

def create_c_callbackv1(func: callable, loop: asyncio.BaseEventLoop):
    @CFUNCTYPE(None, POINTER(dcgm_structs.c_dcgmPolicyCallbackResponse_v1), c_uint64)
    def c_callback(response, userData):
        # copy data into a python struct so that it is the right format and is not lost when "response" var is lost
        callbackResp = dcgm_structs.c_dcgmPolicyCallbackResponse_v1()
        memmove(addressof(callbackResp), response, callbackResp.FieldsSizeof())
        asyncio.run_coroutine_threadsafe(func(callbackResp), loop=loop)
    return c_callback

def create_c_callbackv2(func: callable, loop: asyncio.BaseEventLoop):
    @CFUNCTYPE(None, POINTER(dcgm_structs.c_dcgmPolicyCallbackResponse_v2), c_uint64)
    def c_callback(response, userData):
        # copy data into a python struct so that it is the right format and is not lost when "response" var is lost
        callbackResp = dcgm_structs.c_dcgmPolicyCallbackResponse_v2()
        memmove(addressof(callbackResp), response, callbackResp.FieldsSizeof())
        coro = AsyncScheduler.add_task(time(),Priority.HARDWARE_EVENT_CALLBACK,func, callbackResp)
        asyncio.run_coroutine_threadsafe(coro=coro, loop=loop)
    return c_callback

# Dynamically expose only the relevant callback function based on DCGM version
if DCGM_VERSION >= '4.0.0':
    create_c_callback = create_c_callbackv2
else:
    create_c_callback = create_c_callbackv1

# Expose all necessary imports and the selected callback function
__all__ = [
    "create_c_callback",
    "pydcgm",
    "dcgm_structs",
    "dcgm_fields",
    "dcgm_agent",
    "dcgmvalue",
    "dcgmExceptionClass",
    "DcgmFieldGroup"
]
