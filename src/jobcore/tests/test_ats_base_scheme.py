import pytest

from src.jobcore.ats.base import http_get_json
from src.jobcore.net import require_http


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://x"])
def test_non_http_schemes_are_refused(url):
    with pytest.raises(ValueError):
        require_http(url)


def test_http_and_https_pass():
    assert require_http("https://example.com/a") == "https://example.com/a"
    assert require_http("http://localhost:11434/api") == "http://localhost:11434/api"


def test_fetcher_refuses_file_urls_rather_than_reading_them():
    with pytest.raises(ValueError):
        http_get_json("file:///etc/passwd")
