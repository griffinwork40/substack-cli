"""Tests for substack_cli.read — RSS feed parsing."""
import httpx
import respx

from substack_cli.client import SubstackClient
from substack_cli.read import get_feed

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Capital Mischief</title>
    <item>
      <title>Post One</title>
      <link>https://example.com/p/post-one</link>
      <pubDate>Mon, 01 Jul 2025 10:00:00 GMT</pubDate>
      <description>Summary text</description>
    </item>
    <item>
      <title>Post Two</title>
      <link>https://example.com/p/post-two</link>
      <pubDate>Wed, 03 Jul 2025 10:00:00 GMT</pubDate>
      <description>Another summary</description>
    </item>
  </channel>
</rss>"""

EMPTY_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Empty Feed</title>
  </channel>
</rss>"""


@respx.mock
def test_get_feed_raw_true_returns_xml_string_unparsed(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/feed").mock(
        return_value=httpx.Response(200, content=SAMPLE_RSS.encode(), headers={"content-type": "application/xml"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_feed(client, raw=True)
    assert isinstance(result, str)
    assert "<rss" in result


@respx.mock
def test_get_feed_raw_false_parses_items_into_list_of_dicts(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/feed").mock(
        return_value=httpx.Response(200, content=SAMPLE_RSS.encode(), headers={"content-type": "application/xml"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_feed(client, raw=False)
    assert isinstance(result, list)
    assert len(result) == 2


@respx.mock
def test_get_feed_parsed_item_has_title_link_pubdate_keys(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/feed").mock(
        return_value=httpx.Response(200, content=SAMPLE_RSS.encode(), headers={"content-type": "application/xml"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_feed(client, raw=False)
    item = result[0]
    assert "title" in item
    assert "link" in item
    assert "pubDate" in item
    assert item["title"] == "Post One"


@respx.mock
def test_get_feed_handles_empty_channel_gracefully(fake_cookies, fake_publication_url):
    respx.get(f"{fake_publication_url}/feed").mock(
        return_value=httpx.Response(200, content=EMPTY_RSS.encode(), headers={"content-type": "application/xml"})
    )
    client = SubstackClient(cookies=fake_cookies, publication_url=fake_publication_url)
    result = get_feed(client, raw=False)
    assert result == []