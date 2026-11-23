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


class SchoolTests(unittest.TestCase):
    def test_default_school_is_kfupm(self):
        self.assertEqual(cd.DEFAULT_SCHOOL, "KFUPM")
        self.assertEqual(cd.BASE_URL, cd.SCHOOLS["KFUPM"]["base_url"])

    def test_iau_preset(self):
        self.assertEqual(cd.SCHOOLS["IAU"]["base_url"], "https://vle.iau.edu.sa/")

    def test_iau_env_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".env")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("IAU_User = 12345\nIAUPassword = s3cret\n")
            with mock.patch.object(cd, "find_env_file", return_value=path):
                self.assertEqual(
                    cd.load_env_credentials("IAU"),
                    {"username": "12345", "password": "s3cret"},
                )

    def test_other_schools_keys_are_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".env")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("User = 12345\nPassword = s3cret\n")
            with mock.patch.object(cd, "find_env_file", return_value=path):
                self.assertEqual(cd.load_env_credentials("IAU"), {})


class LoggedInTests(unittest.TestCase):
    class FakeDriver:
        def __init__(self, url, has_password=False):
            self.current_url = url
            self._has_password = has_password

        def find_elements(self, by, selector):
            return [object()] if self._has_password else []

    def test_accepts_each_school_ultra(self):
        for school, preset in cd.SCHOOLS.items():
            driver = self.FakeDriver(preset["base_url"] + "ultra")
            self.assertTrue(cd._looks_logged_in(driver, preset["base_url"]), school)

    def test_rejects_sso_host(self):
        driver = self.FakeDriver("https://iauauth.iau.edu.sa/adfs/ls/")
        self.assertFalse(
            cd._looks_logged_in(driver, cd.SCHOOLS["IAU"]["base_url"])
        )

    def test_rejects_still_visible_password_form(self):
        driver = self.FakeDriver("https://vle.iau.edu.sa/ultra", has_password=True)
        self.assertFalse(
            cd._looks_logged_in(driver, cd.SCHOOLS["IAU"]["base_url"])
        )


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


class AssessmentHelperTests(unittest.TestCase):
    def test_submission_suffix(self):
        self.assertEqual(
            cd.add_submission_suffix("ME489_HW1.pdf"),
            "ME489_HW1_submission.pdf",
        )
        self.assertEqual(
            cd.add_submission_suffix("ME489_HW1_submission.pdf"),
            "ME489_HW1_submission.pdf",
        )
        self.assertEqual(cd.add_submission_suffix("archive"), "archive_submission")
        self.assertEqual(
            cd.add_submission_suffix("archive.zip"), "archive_submission.zip"
        )

    def test_html_to_text(self):
        self.assertEqual(
            cd.html_to_text("<p>Hello<br>World &amp; more</p>"),
            "Hello\nWorld & more",
        )
        self.assertEqual(cd.html_to_text(""), "")

    def test_format_ts(self):
        self.assertEqual(
            cd.format_ts("2026-09-21T20:59:00.000Z"),
            "2026-09-21 20:59 UTC",
        )
        self.assertEqual(cd.format_ts("—"), "—")
        self.assertEqual(cd.format_ts("not-a-date"), "not-a-date")

    def test_html_from_field_accepts_dicts(self):
        self.assertEqual(
            cd.html_from_field({"rawText": "<p>Raw</p>"}), "<p>Raw</p>"
        )
        self.assertEqual(
            cd.html_from_field({"displayText": "<p>Display</p>"}),
            "<p>Display</p>",
        )

    def test_absolutize_html(self):
        html = '<a href="/x">x</a><img src="/y.png">'
        result = cd.absolutize_html(html, "https://example.test/")
        self.assertIn('href="https://example.test/x"', result)
        self.assertIn('src="https://example.test/y.png"', result)

    def test_extract_links_with_bbfile_metadata(self):
        html = (
            '<p><a href="https://example.test/bbcswebdav/xid-1_1" '
            'data-bbtype="attachment" '
            'data-bbfile="{&quot;fileName&quot;:&quot;ME489_HW1.pdf&quot;,'
            '&quot;mimeType&quot;:&quot;application/pdf&quot;}">'
            "ME489_HW1.pdf</a></p>"
        )
        links = cd.extract_bbcswebdav_links(html)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["name"], "ME489_HW1.pdf")
        self.assertEqual(links[0]["mime"], "application/pdf")
        self.assertFalse(links[0]["is_image"])

    def test_extract_links_skips_inline_images_by_flag(self):
        html = '<img src="https://example.test/bbcswebdav/xid-2_1/convert.png">'
        self.assertEqual(cd.extract_bbcswebdav_links(html), [])
        links = cd.extract_bbcswebdav_links(html, include_images=True)
        self.assertEqual(len(links), 1)
        self.assertTrue(links[0]["is_image"])

    def test_extract_links_unescapes_signed_query(self):
        html = (
            '<a href="https://example.test/bbcswebdav/xid-3_1?a=1&amp;b=2">'
            "file.pdf</a>"
        )
        links = cd.extract_bbcswebdav_links(html)
        self.assertEqual(links[0]["url"], "https://example.test/bbcswebdav/xid-3_1?a=1&b=2")

    def test_assessment_score_and_status(self):
        self.assertEqual(
            cd.CourseDownloader._assessment_score(
                {"id": "_c"}, {"effectiveScore": 55, "pointsPossible": 100}
            ),
            "55 / 100",
        )
        self.assertEqual(
            cd.CourseDownloader._assessment_score(
                {"id": "_c"},
                {
                    "effectiveScore": 55,
                    "pointsPossible": 100,
                    "displayGrade": {"isOverride": True},
                },
            ),
            "55 / 100 (override)",
        )
        self.assertEqual(
            cd.CourseDownloader._assessment_score(None, None), "—"
        )
        self.assertEqual(
            cd.CourseDownloader._assessment_status(
                {"id": "_c"}, {"status": "GRADED"}, None
            ),
            "Graded",
        )
        self.assertEqual(
            cd.CourseDownloader._assessment_status(
                {"id": "_c"}, {"effectiveScore": 9.5}, None
            ),
            "Graded",
        )
        self.assertEqual(
            cd.CourseDownloader._assessment_status(
                {"id": "_c"}, {"status": "NEEDS_GRADING"}, {"id": "_a"}
            ),
            "Submitted",
        )
        self.assertEqual(
            cd.CourseDownloader._assessment_status({"id": "_c"}, None, None),
            "Not submitted",
        )
        self.assertEqual(
            cd.CourseDownloader._assessment_status(None, None, None),
            "No gradebook entry",
        )

    def test_page_not_found(self):
        class TitleDriver:
            title = "Page Not Found"

            def find_elements(self, by, selector):
                return []

        class CssDriver:
            title = "Something"

            def find_elements(self, by, selector):
                return [object()]

        class NormalDriver:
            title = "Course"

            def find_elements(self, by, selector):
                return []

        self.assertTrue(cd._page_not_found(TitleDriver()))
        self.assertTrue(cd._page_not_found(CssDriver()))
        self.assertFalse(cd._page_not_found(NormalDriver()))

    def test_write_assessment_info(self):
        client = mock.Mock()
        client.base = "https://example.test"
        downloader = cd.CourseDownloader(
            client=client, driver=None, status_callback=lambda *_: None
        )
        info = {
            "status": "Graded",
            "score": "55 / 100",
            "submitted": "2026-09-21T04:58:49.353Z",
            "due": "2026-09-21T17:59:00.000Z",
            "attempt": "_647757_1",
            "attempts": "1",
            "receipt": "receipt-1",
            "graded": "2026-10-11T18:18:54.893Z",
            "instructions": "<p>Do the work &amp; submit.</p>",
            "feedback": "<p>Good job</p>",
            "answers": [
                {
                    "question": {
                        "questionType": "numeric",
                        "questionText": {"rawText": "<p>Compute 5 + 5.</p>"},
                        "points": 10,
                        "answerValue": 10,
                        "toleranceValue": 0.1,
                    },
                    "givenAnswer": "10",
                    "correct": True,
                    "isCorrectAnswersVisible": True,
                    "isFeedbackVisible": True,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "ME489_HW1.pdf"), "wb") as handle:
                handle.write(b"pdf")
            downloader._write_assessment_info(tmp, "Homework 1", "Homework 1", info)
            path = os.path.join(tmp, "Homework 1_assessment_info.html")
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as handle:
                page = handle.read()
            self.assertIn("Graded", page)
            self.assertIn("55 / 100", page)
            self.assertIn("Do the work &amp; submit.", page)
            self.assertIn("Good job", page)
            self.assertIn("ME489_HW1.pdf", page)
            self.assertIn("Question 1", page)
            self.assertIn("Compute 5 + 5.", page)
            self.assertIn("Your answer", page)
            self.assertIn("Correct answer", page)
            self.assertIn("10 ± 0.1", page)
            self.assertNotIn("Open submission page", page)


if __name__ == "__main__":
    unittest.main()
