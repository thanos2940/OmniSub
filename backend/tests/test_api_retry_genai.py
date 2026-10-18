"""
Transient errors of the google-genai SDK are retried
====================================================
``rate_limited_call`` retried 503/504 only as ``google.api_core`` exceptions, but the google-genai
SDK raises its own ``errors.ServerError`` — so a "model is experiencing high demand" 503 failed the
call at once. Network-free: the API call is a stub.
"""

import pytest
from google.genai import errors

from utils.api_call_wrapper import rate_limited_call


class _Limiter:
    async def acquire(self):
        return None

    def report_rate_limit(self, retry_after):
        pass


def _server_error(code=503):
    return errors.ServerError(code, {"error": {"code": code, "message": "This model is currently experiencing "
                                                                         "high demand.", "status": "UNAVAILABLE"}})


@pytest.mark.asyncio
async def test_a_503_from_the_genai_sdk_is_retried():
    calls = []

    async def call():
        calls.append(1)
        if len(calls) < 3:
            raise _server_error()
        return "ok"

    assert await rate_limited_call(call, rate_limiter=_Limiter(), base_delay=0.01) == "ok"
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_a_lasting_503_still_fails_after_the_retries():
    async def call():
        raise _server_error(504)

    with pytest.raises(errors.ServerError):
        await rate_limited_call(call, rate_limiter=_Limiter(), max_retries=2, base_delay=0.01)


@pytest.mark.asyncio
async def test_a_client_error_is_not_retried():
    calls = []

    async def call():
        calls.append(1)
        raise errors.ClientError(400, {"error": {"code": 400, "message": "bad request", "status": "INVALID_ARGUMENT"}})

    with pytest.raises(errors.ClientError):
        await rate_limited_call(call, rate_limiter=_Limiter(), base_delay=0.01)
    assert len(calls) == 1
