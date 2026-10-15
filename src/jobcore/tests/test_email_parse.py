"""Tests for src.jobcore.email_parse."""

from __future__ import annotations

import base64

from src.jobcore.email_parse import extract_text


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


class TestExtractText:
    def test_plain_text_only(self) -> None:
        message = {
            "payload": {
                "mimeType": "text/plain",
                "body": {"data": _b64url("We have a Backend Team Lead role, remote OK.")},
            }
        }
        assert extract_text(message) == "We have a Backend Team Lead role, remote OK."

    def test_multipart_alternative_prefers_plain(self) -> None:
        message = {
            "payload": {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _b64url("Plain version.")}},
                    {
                        "mimeType": "text/html",
                        "body": {"data": _b64url("<p>HTML <b>version</b>.</p>")},
                    },
                ],
            }
        }
        assert extract_text(message) == "Plain version."

    def test_html_only_falls_back_and_strips_tags(self) -> None:
        message = {
            "payload": {
                "mimeType": "text/html",
                "body": {"data": _b64url("<p>Hello <b>World</b></p><script>evil()</script>")},
            }
        }
        result = extract_text(message)
        assert "Hello" in result
        assert "World" in result
        assert "evil" not in result
        assert "<" not in result

    def test_nested_multipart_mixed(self) -> None:
        message = {
            "payload": {
                "mimeType": "multipart/mixed",
                "parts": [
                    {
                        "mimeType": "multipart/alternative",
                        "parts": [
                            {
                                "mimeType": "text/plain",
                                "body": {"data": _b64url("Nested plain text.")},
                            },
                        ],
                    },
                    {
                        "mimeType": "application/pdf",
                        "body": {"data": _b64url("not real pdf bytes")},
                    },
                ],
            }
        }
        assert extract_text(message) == "Nested plain text."

    def test_hebrew_text_round_trips_cleanly(self) -> None:
        hebrew = "משרת Backend Team Lead, היברידי, נתניה"
        message = {
            "payload": {
                "mimeType": "text/plain",
                "body": {"data": _b64url(hebrew)},
            }
        }
        assert extract_text(message) == hebrew

    def test_no_body_data_returns_empty(self) -> None:
        message = {"payload": {"mimeType": "text/plain", "body": {}}}
        assert extract_text(message) == ""

    def test_missing_payload_returns_empty(self) -> None:
        assert extract_text({}) == ""

    def test_unpadded_base64url_decodes(self) -> None:
        # base64url without '=' padding is a real, common Gmail API shape.
        raw = base64.urlsafe_b64encode(b"short").decode("ascii").rstrip("=")
        message = {"payload": {"mimeType": "text/plain", "body": {"data": raw}}}
        assert extract_text(message) == "short"
