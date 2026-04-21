"""shared/google_gmail.py unit tests。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_list_messages_empty_returns_empty_list():
    """搜尋結果為空時，不應進入 ThreadPoolExecutor(max_workers=0) → ValueError。"""
    mock_service = MagicMock()
    mock_service.users().messages().list().execute.return_value = {}  # 無 messages key

    with patch("shared.google_gmail._get_service", return_value=mock_service):
        from shared.google_gmail import list_messages

        result = list_messages(query="in:sent 報價", max_results=5)

    assert result == []


def test_list_messages_messages_key_empty_list():
    """messages key 存在但為空陣列時，同樣回傳空 list。"""
    mock_service = MagicMock()
    mock_service.users().messages().list().execute.return_value = {"messages": []}

    with patch("shared.google_gmail._get_service", return_value=mock_service):
        from shared.google_gmail import list_messages

        result = list_messages(query="in:sent 報價", max_results=5)

    assert result == []
