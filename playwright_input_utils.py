"""Input synchronization helpers for high-latency Playwright sessions."""
from __future__ import annotations

import random
from typing import Any


async def type_text_slowly(
    page: Any,
    selector: str,
    text_value: Any,
    *,
    min_delay_ms: int = 60,
    max_delay_ms: int = 140,
) -> None:
    """Focus a field and type text one character at a time.

    This avoids bulk ``fill()`` writes in slow VM/browser environments where
    front-end input handlers may miss fast state updates.
    """
    if min_delay_ms > max_delay_ms:
        raise ValueError("min_delay_ms must be <= max_delay_ms")

    await page.focus(selector)
    for char in str(text_value):
        await page.keyboard.type(char, delay=random.randint(min_delay_ms, max_delay_ms))




class PlaywrightInputUtils:
    """Compatibility wrapper used by Outlook helper flows."""

    @staticmethod
    async def type_humanlike(
        page: Any,
        selector: str,
        text_value: Any,
        *,
        min_delay_ms: int = 60,
        max_delay_ms: int = 140,
    ) -> None:
        await type_text_slowly(
            page,
            selector,
            text_value,
            min_delay_ms=min_delay_ms,
            max_delay_ms=max_delay_ms,
        )

    @staticmethod
    async def click_humanlike(page: Any, selector: str, *, timeout: int = 5000) -> None:
        locator = page.locator(selector).first
        await locator.wait_for(state="visible", timeout=timeout)
        await locator.click()
