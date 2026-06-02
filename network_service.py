"""Network failover hook used by Playwright navigation helpers.

The default implementation is intentionally conservative: it does not change
system network settings by itself. Replace the body of
``reconfigure_network_adapter`` with your environment-specific adapter reset,
gateway failover, or internal network-service API call.
"""
from __future__ import annotations

import asyncio


async def reconfigure_network_adapter(
    *,
    worker_id: int = 0,
    attempt: int = 0,
    reason: str = "",
) -> bool:
    """Reset or reconfigure the network path for one worker.

    Returns True when a real failover action was performed. The built-in stub
    returns False so local development and health checks stay side-effect free.
    """
    _ = (worker_id, attempt, reason)
    await asyncio.sleep(0)
    return False


# Backward-compatible alias for callers that prefer reset_* naming.
reset_network_adapter = reconfigure_network_adapter
