"""Offline unit tests for the Ultra downloader.

These tests never touch the network: they exercise the pure helpers and the
pagination/attachment logic with fake payloads so the behaviour can be verified
without a Blackboard account.
"""

import os
import tempfile
import unittest
from unittest import mock

import course_downloader as cd


class SanitizeTests(unittest.TestCase):
    def test_removes_illegal_characters(self):
        self.assertEqual(cd.sanitize_component('a/b\\c:d*e?f"g<h>i|j'), "a_b_c_d_e_f_g_h_i_j")

    def test_collapses_whitespace_and_strips_dots(self):
        self.assertEqual(cd.sanitize_component("  hello   world. "), "hello world")

    def test_empty_uses_fallback(self):
        self.assertEqual(cd.sanitize_component("   ", fallback="untitled"), "untitled")

    def test_truncates_to_max_len(self):
        self.assertEqual(len(cd.sanitize_component("x" * 500, max_len=20)), 20)


class ExtractUrlsTests(unittest.TestCase):
    def test_finds_href_and_src(self):
        body = (
            '<a href="/bbcswebdav/pid-1/xid-1_1/slides.pdf">slides</a>'
            '<img src="/bbcswebdav/pid-2/xid-2_1/pic.png">'
        )
        self.assertEqual(
            cd.extract_bbcswebdav_urls(body),
            ["/bbcswebdav/pid-1/xid-1_1/slides.pdf", "/bbcswebdav/pid-2/xid-2_1/pic.png"],
        )

    def test_deduplicates(self):
        body = '<a href="/bbcswebdav/x.pdf">a</a><a href="/bbcswebdav/x.pdf">b</a>'
        self.assertEqual(cd.extract_bbcswebdav_urls(body), ["/bbcswebdav/x.pdf"])

    def test_unescapes_ampersands(self):
        body = '<a href="/bbcswebdav/x.pdf?a=1&amp;b=2">x</a>'
        self.assertEqual(cd.extract_bbcswebdav_urls(body), ["/bbcswebdav/x.pdf?a=1&b=2"])

    def test_empty_body(self):
        self.assertEqual(cd.extract_bbcswebdav_urls(""), [])


class KindTests(unittest.TestCase):
    def test_known_handlers(self):
        cases = {
            "resource/x-bb-folder": "folder",
            "resource/x-bb-lesson": "folder",
            "resource/x-bb-file": "file",
            "resource/x-bb-document": "document",
            "resource/x-bb-ultra-document": "document",
            "resource/x-bb-externallink": "link",
            "resource/x-bb-courselink": "link",
            "resource/x-bb-asmt-test-link": "assessment",
            "resource/x-bb-asmt-assignment": "assessment",
        }
        for handler, expected in cases.items():
            self.assertEqual(cd.kind_for_handler(handler), expected, handler)

    def test_syllabus_is_document(self):
        self.assertEqual(cd.kind_for_handler("resource/x-bb-syllabus"), "document")

    def test_has_children_is_folder(self):
        self.assertEqual(
            cd.kind_for_handler("", {"hasChildren": True}), "folder"
        )

    def test_unknown_is_other(self):
        self.assertEqual(cd.kind_for_handler("resource/x-bb-mystery"), "other")


class HtmlTests(unittest.TestCase):
    def test_escapes_title(self):
        doc = cd.html_document("<script>", "<p>body</p>")
        self.assertIn("&lt;script&gt;", doc)
        self.assertIn("<p>body</p>", doc)


class UrlFileTests(unittest.TestCase):
    def test_writes_internet_shortcut(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "link.url")
            cd.write_url_file(path, "https://example.com")
            with open(path, encoding="utf-8") as handle:
                content = handle.read()
            self.assertEqual(content, "[InternetShortcut]\nURL=https://example.com\n")


class PaginationTests(unittest.TestCase):
    def test_get_all_follows_next_page(self):
        client = cd.UltraClient(cookies=[])
        pages = {
            "page1": {"results": [1, 2], "paging": {"nextPage": "page2"}},
            "page2": {"results": [3], "paging": {}},
        }
        with mock.patch.object(client, "get", side_effect=lambda path, params=None: pages[path]):
            self.assertEqual(client.get_all("page1"), [1, 2, 3])

    def test_get_all_stops_on_pagination_loop(self):
        client = cd.UltraClient(cookies=[])
        payload = {"results": [1], "paging": {"nextPage": "same"}}
        with mock.patch.object(client, "get", return_value=payload):
            self.assertEqual(client.get_all("same"), [1])


class AllocTests(unittest.TestCase):
    def test_deduplicates_names_per_directory(self):
        downloader = cd.CourseDownloader(client=None, driver=None, status_callback=lambda *_: None)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(downloader._alloc(tmp, "file.pdf"), "file.pdf")
            self.assertEqual(downloader._alloc(tmp, "file.pdf"), "file (1).pdf")
            self.assertEqual(downloader._alloc(tmp, "file.pdf"), "file (2).pdf")

    def test_same_name_allowed_in_different_directories(self):
        downloader = cd.CourseDownloader(client=None, driver=None, status_callback=lambda *_: None)
        with tempfile.TemporaryDirectory() as tmp:
            other = os.path.join(tmp, "sub")
            os.makedirs(other)
            self.assertEqual(downloader._alloc(tmp, "file.pdf"), "file.pdf")
            self.assertEqual(downloader._alloc(other, "file.pdf"), "file.pdf")


class DownloadSkipTests(unittest.TestCase):
    def test_skips_existing_file_of_matching_size(self):
        client = cd.UltraClient(cookies=[])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.bin")
            with open(path, "wb") as handle:
                handle.write(b"hello")
            status, size, _ = client.download("http://invalid", path, expected_size=5)
            self.assertEqual((status, size), ("skipped", 5))

    def test_no_url_fails(self):
        client = cd.UltraClient(cookies=[])
        self.assertEqual(client.download("", "/tmp/x")[0], "failed")


class EnvCredentialTests(unittest.TestCase):
    def test_parses_spaces_and_capital_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".env")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("User = 12345\nPassword = s3cret\n")
            with mock.patch.object(cd, "find_env_file", return_value=path):
                self.assertEqual(
                    cd.load_env_credentials(),
                    {"username": "12345", "password": "s3cret"},
                )

    def test_strips_quotes_and_ignores_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".env")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("# comment\nUSERNAME=\"abc\"\nPASSWORD='def'\n")
            with mock.patch.object(cd, "find_env_file", return_value=path):
                self.assertEqual(
                    cd.load_env_credentials(),
                    {"username": "abc", "password": "def"},
                )

    def test_missing_file_returns_empty(self):
        with mock.patch.object(cd, "find_env_file", return_value=None):
            self.assertEqual(cd.load_env_credentials(), {})


class BrowserFallbackTests(unittest.TestCase):
    def test_returns_none_without_driver(self):
        client = cd.UltraClient(cookies=[])
        self.assertIsNone(client._browser_get_json("https://example.com/api"))

    def test_parses_json_returned_by_driver(self):
        client = cd.UltraClient(cookies=[])

        class FakeDriver:
            def execute_async_script(self, script, url):
                return {"status": 200, "body": '{"ok": true}'}

        client.driver = FakeDriver()
        self.assertEqual(client._browser_get_json("https://example.com/api"), {"ok": True})

    def test_non_200_returns_none(self):
        client = cd.UltraClient(cookies=[])

        class FakeDriver:
            def execute_async_script(self, script, url):
                return {"status": 401, "body": "nope"}

        client.driver = FakeDriver()
        self.assertIsNone(client._browser_get_json("https://example.com/api"))


if __name__ == "__main__":
    unittest.main()
