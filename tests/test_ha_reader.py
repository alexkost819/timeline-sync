from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from timeline_sync.ha_reader import HAReader

START = datetime(2024, 1, 15, 0, tzinfo=UTC)
END = datetime(2024, 1, 15, 23, tzinfo=UTC)


@pytest.mark.asyncio
async def test_get_state_history_request_does_not_use_minimal_response():
    captured_kwargs: dict = {}

    mock_resp = MagicMock()
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=[[]])

    def mock_get(url, **kwargs):
        captured_kwargs.update(kwargs)
        return mock_resp

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = mock_get

    with patch("aiohttp.ClientSession", return_value=mock_session):
        reader = HAReader("http://ha.local:8123", "token")
        await reader.get_state_history("device_tracker.phone", START, END)

    params = captured_kwargs.get("params", {})
    assert params.get("minimal_response") != "true"
