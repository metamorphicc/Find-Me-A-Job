from job_search_automation.discover import discover_application_links


def test_finds_only_explicit_public_application_link():
    html = """
    <a href="https://example.test/application">Apply now</a>
    <a href="https://example.test/login">Apply after login</a>
    <a href="http://127.0.0.1/apply">Apply locally</a>
    """

    def configure(page):
        page.route("**/*", lambda route: route.fulfill(body=html, content_type="text/html"))

    assert discover_application_links("https://example.test/job", configure_page=configure) == (
        "https://example.test/application",
    )


def test_direct_application_form_is_returned():
    html = """
    <form><input name="Name" required><input name="Email" type="email" required>
    <button type="submit">Send</button></form>
    """

    def configure(page):
        page.route("**/*", lambda route: route.fulfill(body=html, content_type="text/html"))

    assert discover_application_links("https://example.test/apply", configure_page=configure) == (
        "https://example.test/apply",
    )
