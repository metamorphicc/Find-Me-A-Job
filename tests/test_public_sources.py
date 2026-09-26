from datetime import UTC, datetime
from email.utils import format_datetime

import pytest
import requests

from job_search_automation.config import SearchConfig
from job_search_automation.public_sources import RemotiveClient, RssClient, SourceError


class Response:
    def __init__(self, *, data=None, content=b"", status=200):
        self.data = data
        self.content = content
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError("source unavailable")

    def json(self):
        return self.data


class Session:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def get(self, *_args, **_kwargs):
        self.calls += 1
        return self.response


def settings():
    return SearchConfig(("Python",), (), (), (), True, True, 7, 20)


def test_remotive_fetches_once_and_preserves_location_restriction():
    session = Session(
        Response(
            data={
                "jobs": [
                    {
                        "id": 123,
                        "url": "https://remotive.com/remote-jobs/dev/python-123",
                        "title": "Python engineer",
                        "company_name": "Example",
                        "publication_date": datetime.now(UTC).isoformat(),
                        "candidate_required_location": "Europe",
                        "job_type": "contract",
                        "salary": "€100/day",
                        "description": "<p>Build software</p>",
                    }
                ]
            }
        )
    )
    items = RemotiveClient(session).search(settings())
    assert session.calls == 1
    assert items[0].kind == "freelance"
    assert items[0].location_scope == "Europe"
    assert items[0].pay_label == "€100/day"


@pytest.mark.parametrize(
    ("source", "url", "market", "kind"),
    [
        ("wwr", "https://weworkremotely.com/remote-jobs/python", "global", "freelance"),
        ("fl", "https://www.fl.ru/projects/123", "ru", "freelance"),
    ],
)
def test_rss_projects_have_stable_ids_and_source_links(source, url, market, kind):
    published = format_datetime(datetime.now(UTC))
    feed = f"""
    <rss><channel><item>
      <title>Example: Python project</title><link>{url}</link>
      <description>Build a service</description><pubDate>{published}</pubDate>
      <region>Europe</region><type>Contract</type>
    </item></channel></rss>
    """.encode()
    session = Session(Response(content=feed))
    first = RssClient(source, "https://example.test/feed", market, session=session).search(settings())
    second = RssClient(source, "https://example.test/feed", market, session=session).search(settings())
    assert first[0].source_id == second[0].source_id
    assert first[0].kind == kind
    assert first[0].url == url


def test_unavailable_rss_is_reported_not_parsed():
    source = RssClient("fl", "https://example.test/feed", "ru", session=Session(Response(status=403)))
    with pytest.raises(SourceError, match="недоступен"):
        source.search(settings())


def test_remotive_category_mode_ignores_text_and_rejects_teaching():
    now = datetime.now(UTC).isoformat()
    session = Session(Response(data={"jobs": [
        {
            "id": 1, "url": "https://remotive.com/remote-jobs/dev/1", "title": "Бармен",
            "category": "Teaching", "publication_date": now,
        },
        {
            "id": 2, "url": "https://remotive.com/remote-jobs/dev/2", "title": "Backend role",
            "category": "Software Development", "publication_date": now,
        },
    ]}))
    focused = SearchConfig((), (), (), (), True, True, 7, 20, categories=("software",))
    items = RemotiveClient(session).search(focused)
    assert [item.title for item in items] == ["Backend role"]
    assert items[0].categories == ("software",)


def test_wwr_category_mode_keeps_only_programming_or_it():
    published = format_datetime(datetime.now(UTC))
    feed = f"""<rss><channel>
    <item><title>Example: Art teacher</title><link>https://weworkremotely.com/remote-jobs/1</link>
      <description>Teacher</description><pubDate>{published}</pubDate>
      <category>Full-Stack Programming</category></item>
    <item><title>Example: Sales Development Representative</title>
      <link>https://weworkremotely.com/remote-jobs/3</link>
      <description>Sales</description><pubDate>{published}</pubDate>
      <category>DevOps and Sysadmin</category></item>
    <item><title>Example: Backend engineer</title><link>https://weworkremotely.com/remote-jobs/2</link>
      <description>Code</description><pubDate>{published}</pubDate>
      <category>Back-End Programming</category></item>
    </channel></rss>""".encode()
    focused = SearchConfig((), (), (), (), True, True, 7, 20, categories=("software",))
    items = RssClient("wwr", "https://example.test/feed", "global", session=Session(Response(content=feed))).search(focused)
    assert [item.title for item in items] == ["Backend engineer"]
    assert items[0].categories == ("software",)
