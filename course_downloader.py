"""
KFUPM Blackboard Ultra Course Downloader
========================================

A desktop tool that logs into KFUPM Blackboard Ultra (SAML SSO) and downloads entire
courses to your computer, preserving the original folder structure.

This version targets **Blackboard Ultra** (the previous version targeted
Blackboard Classic).  Login is done through the browser with Selenium so the
KFUPM Single Sign-On (WSO2 / SAML) flow keeps working; all course data is then
read through the public Blackboard Learn REST API, which is far faster and more
reliable than scraping the rendered Ultra pages.

Read-only: the tool never changes anything on Blackboard.
"""

import os
import re
import sys
import time
import json
import shutil
import argparse
import threading
import traceback
from html import escape as html_escape, unescape as html_unescape
from urllib.parse import urlparse, unquote

import requests

import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# --- Selenium imports (browser automation for the SSO login) ---
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    WebDriverException,
)

# Firefox specific
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.firefox.options import Options as FirefoxOptions

# Chrome specific
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from webdriver_manager.chrome import ChromeDriverManager


# =========================================================================== #
# Constants
# =========================================================================== #

BASE_URL = "https://blackboard.kfupm.edu.sa/"
ULTRA_HOME = BASE_URL + "ultra/"
API_ROOT = "/learn/api/public/v1"

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".kfupm_bb_downloader")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.ini")

# Extensions used as a last resort when the server gives no filename hint.
MIME_TYPE_MAP = {
    "application/pdf": ".pdf",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "application/x-rar-compressed": ".rar",
    "application/x-7z-compressed": ".7z",
    "application/x-tar": ".tar",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-msvideo": ".avi",
    "video/x-matroska": ".mkv",
    "video/webm": ".webm",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "text/plain": ".txt",
    "application/x-ipynb+json": ".ipynb",
    "application/octet-stream": "",
}

# Ultra content handler ids, grouped by what we do with them.
FOLDER_HANDLERS = {
    "resource/x-bb-folder",
    "resource/x-bb-lesson",
    "resource/x-bb-learning-module",
    "resource/x-bb-module",
    "resource/x-bb-blankpage",
    "resource/x-bb-folder-file",
}
FILE_HANDLERS = {"resource/x-bb-file"}
DOC_HANDLERS = {"resource/x-bb-document", "resource/x-bb-ultra-document"}
LINK_HANDLERS = {"resource/x-bb-externallink", "resource/x-bb-courselink"}
ASSESSMENT_HANDLERS = {
    "resource/x-bb-asmt-test-link",
    "resource/x-bb-asmt-assignment",
    "resource/x-bb-asmt-survey-link",
}


# =========================================================================== #
# Small helpers
# =========================================================================== #

_BAD_PATH_CHARS = re.compile(r'[\\/*?:"<>|\r\n\t]')
_BBCSWEBDav_RE = re.compile(
    r'(?:href|src)\s*=\s*["\']([^"\']*?/bbcswebdav/[^"\']+)["\']', re.IGNORECASE
)


def sanitize_component(name, fallback="untitled", max_len=180):
    """Turn an arbitrary Blackboard title into a safe single path component."""
    name = (name or "").strip()
    name = _BAD_PATH_CHARS.sub("_", name)
    name = re.sub(r"\s+", " ", name)
    name = name.strip(" .")
    if not name:
        name = fallback
    return name[:max_len]


def extract_bbcswebdav_urls(body):
    """Return unique /bbcswebdav/ URLs referenced by an Ultra body (BBML/HTML)."""
    if not body:
        return []
    found = _BBCSWEBDav_RE.findall(body)
    out = []
    for url in found:
        url = html_unescape(url).strip()
        if url and url not in out:
            out.append(url)
    return out


def kind_for_handler(handler, item=None):
    """Classify a content item into folder/file/document/link/assessment/other."""
    handler = (handler or "").strip().lower()
    if handler in FOLDER_HANDLERS:
        return "folder"
    if handler in FILE_HANDLERS:
        return "file"
    if handler in DOC_HANDLERS or "syllabus" in handler:
        return "document"
    if handler in LINK_HANDLERS:
        return "link"
    if handler in ASSESSMENT_HANDLERS:
        return "assessment"
    if item is not None and item.get("hasChildren"):
        return "folder"
    if any(token in handler for token in ("folder", "lesson", "module", "blankpage")):
        return "folder"
    if "file" in handler:
        return "file"
    if "document" in handler:
        return "document"
    if "externallink" in handler or "courselink" in handler:
        return "link"
    if any(token in handler for token in ("asmt", "test", "assign", "survey")):
        return "assessment"
    return "other"


def html_document(title, body):
    """Wrap an Ultra body in a minimal, readable HTML shell."""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        f"<title>{html_escape(title)}</title>\n"
        "<style>body{font-family:system-ui,Segoe UI,Arial,sans-serif;"
        "max-width:900px;margin:2rem auto;padding:0 1rem;line-height:1.55}"
        "img{max-width:100%;height:auto}table{border-collapse:collapse}"
        "td,th{border:1px solid #bbb;padding:4px 8px}</style></head><body>\n"
        f"<h1>{html_escape(title)}</h1>\n{body}\n</body></html>\n"
    )


def write_url_file(path, url):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("[InternetShortcut]\nURL=" + url + "\n")


def find_env_file():
    """Locate a local .env next to the script (or in the working directory)."""
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        os.path.join(os.getcwd(), ".env"),
    ]
    if getattr(sys, "frozen", False):
        # Running as a PyInstaller bundle: look next to the .exe as well.
        candidates.insert(0, os.path.join(os.path.dirname(sys.executable), ".env"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def load_env_credentials():
    """Read username/password from a local .env file, if one exists.

    Accepts common key spellings (case-insensitive) and tolerates spaces around
    the `=` sign, e.g. `User = 123456` / `Password = secret`.
    """
    path = find_env_file()
    if not path:
        return {}
    values = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip().lower()
                value = value.strip().strip('"').strip("'")
                if key in ("user", "username", "bb_username", "kfupm_username"):
                    values.setdefault("username", value)
                elif key in ("password", "pass", "bb_password", "kfupm_password"):
                    values.setdefault("password", value)
    except OSError:
        return {}
    return values


# =========================================================================== #
# Selenium driver + SSO login
# =========================================================================== #

def setup_driver(browser_choice, status_callback, headless=True):
    """Create a Selenium WebDriver for the requested browser."""
    if browser_choice == "firefox":
        status_callback("Initializing Firefox driver...")
        options = FirefoxOptions()
        if headless:
            options.add_argument("-headless")
        geckodriver_path = os.environ.get("GECKODRIVER") or shutil.which("geckodriver")
        if not geckodriver_path:
            for candidate in (
                os.path.expanduser("~/bin/geckodriver"),
                os.path.expanduser("~/.local/bin/geckodriver"),
                "/usr/local/bin/geckodriver",
                "/tmp/opencode/geckodriver",
            ):
                if os.path.isfile(candidate):
                    geckodriver_path = candidate
                    break
        try:
            if geckodriver_path:
                status_callback(f"  - using geckodriver at {geckodriver_path}")
                service = FirefoxService(executable_path=geckodriver_path)
                driver = webdriver.Firefox(service=service, options=options)
            else:
                status_callback("  - geckodriver not found; relying on PATH")
                driver = webdriver.Firefox(options=options)
            driver.set_page_load_timeout(90)
            status_callback("Firefox driver initialized successfully.")
            return driver
        except Exception as exc:
            raise RuntimeError(
                "Failed to initialize Firefox. Is it installed and geckodriver on "
                f"PATH? Error: {exc}"
            )

    if browser_choice == "chrome":
        status_callback("Initializing Chrome driver...")
        options = ChromeOptions()
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1280,900")
        try:
            status_callback("  - Checking/installing chromedriver via webdriver_manager...")
            service = ChromeService(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            driver.set_page_load_timeout(90)
            status_callback("Chrome driver initialized successfully.")
            return driver
        except Exception as exc:
            raise RuntimeError(
                f"Failed to initialize Chrome. Is it installed? Error: {exc}"
            )

    raise ValueError("Invalid browser choice specified.")


def _find_visible(driver, selectors):
    """Return the first displayed+enabled element matching any (By, value)."""
    for by, value in selectors:
        try:
            for element in driver.find_elements(by, value):
                try:
                    if element.is_displayed() and element.is_enabled():
                        return element
                except WebDriverException:
                    continue
        except WebDriverException:
            continue
    return None


def _click_element(driver, element):
    try:
        element.click()
    except (ElementClickInterceptedException, WebDriverException):
        driver.execute_script("arguments[0].click();", element)


def _click_submit(driver):
    selectors = [
        (By.ID, "submitButton"),          # KFUPM ADFS (a <span>, not a button)
        (By.ID, "login-button"),
        (By.CSS_SELECTOR, 'span[role="button"]'),
        (By.CSS_SELECTOR, 'button[type="submit"]'),
        (By.CSS_SELECTOR, 'input[type="submit"]'),
        (By.CSS_SELECTOR, "button.btn-primary"),
        (
            By.XPATH,
            "//*[@role='button' and (contains(., 'Sign in') or contains(., 'Sign In'))]",
        ),
        (
            By.XPATH,
            "//button[contains(translate(., 'LOGIN', 'login'), 'login') "
            "or contains(translate(., 'SIGN IN', 'sign in'), 'sign in') "
            "or contains(translate(., 'CONTINUE', 'continue'), 'continue') "
            "or contains(translate(., 'APPROVE', 'approve'), 'approve') "
            "or contains(translate(., 'ACCEPT', 'accept'), 'accept')]",
        ),
        (By.XPATH, "//input[@type='submit']"),
    ]
    element = _find_visible(driver, selectors)
    if element is not None:
        _click_element(driver, element)
        return True
    return False


def _looks_logged_in(driver):
    """True once the browser is back on the Blackboard Ultra site."""
    try:
        url = driver.current_url or ""
    except WebDriverException:
        return False
    if "blackboard.kfupm.edu.sa" not in url:
        return False
    if "login.kfupm.edu.sa" in url:
        return False
    if not ("/ultra" in url or "/webapps" in url or "/learn" in url):
        return False
    # Still showing a credential form? Then we are not logged in.
    try:
        if driver.find_elements(By.CSS_SELECTOR, 'input[type="password"]'):
            return False
    except WebDriverException:
        pass
    return True


def login(driver, username, password, status_callback, timeout=240):
    """Log into KFUPM Blackboard through the WSO2 SAML SSO flow.

    Returns the list of browser cookies for the Blackboard domain.
    """
    status_callback("Opening Blackboard Ultra and starting SSO login...")
    driver.get(ULTRA_HOME)

    username_selectors = [
        (By.ID, "userNameInput"),          # KFUPM ADFS
        (By.NAME, "UserName"),
        (By.ID, "username"),
        (By.NAME, "username"),
        (By.CSS_SELECTOR, 'input[name="username"]'),
        (By.ID, "userName"),
        (By.NAME, "userName"),
        (By.CSS_SELECTOR, 'input[name="userName"]'),
        (By.CSS_SELECTOR, 'input[name*="user" i]'),
        (By.CSS_SELECTOR, 'input[id*="user" i]'),
        (By.CSS_SELECTOR, 'input[type="email"]'),
        (By.CSS_SELECTOR, 'input[type="text"]'),
    ]
    password_selectors = [
        (By.ID, "passwordInput"),          # KFUPM ADFS
        (By.NAME, "Password"),
        (By.ID, "password"),
        (By.NAME, "password"),
        (By.CSS_SELECTOR, 'input[type="password"]'),
        (By.CSS_SELECTOR, 'input[name*="pass" i]'),
        (By.CSS_SELECTOR, 'input[id*="pass" i]'),
    ]

    deadline = time.time() + timeout
    last_url = ""
    username_entered = False
    clicked_continue = False
    password_attempts = 0

    while time.time() < deadline:
        if _looks_logged_in(driver):
            status_callback("Login successful. Landed on Blackboard Ultra.")
            return driver.get_cookies()

        try:
            current_url = driver.current_url
        except WebDriverException:
            current_url = ""
        if current_url != last_url:
            status_callback(f"  - at: {current_url}")
            last_url = current_url

        user_field = _find_visible(driver, username_selectors)
        pass_field = _find_visible(driver, password_selectors)

        if user_field is not None and not username_entered:
            try:
                if not (user_field.get_attribute("value") or ""):
                    user_field.clear()
                    user_field.send_keys(username)
                username_entered = True
                status_callback("  - entered username")
            except WebDriverException:
                pass

        if pass_field is not None:
            if password_attempts >= 3:
                raise RuntimeError(
                    "Login was rejected three times. Please double-check your "
                    "username and password, then try again."
                )
            try:
                pass_field.clear()
                pass_field.send_keys(password)
                status_callback("  - entered password")
                _click_submit(driver)
                password_attempts += 1
                username_entered = False
                clicked_continue = False
                time.sleep(2)
                continue
            except WebDriverException as exc:
                status_callback(f"  - could not submit login form: {exc}")
        elif username_entered:
            # Two-step login: username first, then a "Next" button.
            if _click_submit(driver):
                status_callback("  - submitted username")
                time.sleep(1)
        elif not clicked_continue and "login.kfupm.edu.sa" in current_url:
            # Consent / "continue" style page with no fields.
            if _click_submit(driver):
                status_callback("  - clicked continue")
                clicked_continue = True
                time.sleep(1)

        time.sleep(0.5)

    raise RuntimeError(
        "Timed out waiting for the Blackboard Ultra login to complete. "
        "Please check your username/password and network connection."
    )


# =========================================================================== #
# Ultra REST client
# =========================================================================== #

class UltraError(Exception):
    pass


class UltraAuthError(UltraError):
    pass


class UltraClient:
    """Thin wrapper around the Blackboard Learn REST API (session based)."""

    def __init__(self, base_url=BASE_URL, cookies=None, status_callback=None,
                 timeout=60, overwrite=False, driver=None):
        self.base = base_url.rstrip("/")
        self.status = status_callback or (lambda *_: None)
        self.timeout = timeout
        self.overwrite = overwrite
        self.driver = driver
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
                    "KFUPM-BB-Ultra-Downloader/2.0"
                ),
                "Accept": "application/json",
            }
        )
        for cookie in cookies or []:
            try:
                self.session.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=cookie.get("domain"),
                    path=cookie.get("path") or "/",
                )
            except (KeyError, TypeError):
                continue

    # ------------------------------------------------------------- requests
    def _url(self, path):
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return self.base + path

    def _browser_get_json(self, url, params=None):
        """Fallback: run the GET inside the logged-in browser via fetch().

        Used only if a plain cookie-authenticated request is rejected, so the
        tool keeps working on tenants that are strict about API sessions.
        """
        if self.driver is None:
            return None
        if params:
            url = requests.Request("GET", url, params=params).prepare().url
        script = (
            "const url = arguments[0];"
            "const cb = arguments[arguments.length - 1];"
            "fetch(url, {credentials: 'include', "
            "headers: {'Accept': 'application/json'}})"
            ".then(r => r.text().then(t => cb({status: r.status, body: t})))"
            ".catch(e => cb({status: 0, body: String(e)}));"
        )
        try:
            result = self.driver.execute_async_script(script, url)
        except WebDriverException as exc:
            self.status(f"  - browser fallback failed: {exc}")
            return None
        if not result or result.get("status") != 200:
            return None
        try:
            return json.loads(result.get("body") or "null")
        except ValueError:
            return None

    def get(self, path, params=None, retries=3, allow_404=False):
        url = self._url(path)
        delay = 1.5
        last_error = None
        for attempt in range(1, retries + 1):
            try:
                response = self.session.get(
                    url, params=params, timeout=self.timeout,
                    headers={"Accept": "application/json"},
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= retries:
                    break
                time.sleep(delay)
                delay *= 2
                continue

            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    raise UltraError(f"GET {url} did not return JSON: {exc}")
            if response.status_code == 404 and allow_404:
                return None
            if response.status_code in (401, 403):
                fallback = self._browser_get_json(url, params)
                if fallback is not None:
                    return fallback
                # 403 can be transient (rate limiting / session warm-up): retry.
                if response.status_code == 403 and attempt < retries:
                    last_error = UltraError("HTTP 403")
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise UltraAuthError(
                    f"Blackboard rejected the request (HTTP {response.status_code}). "
                    "Your session may have expired; try scanning again."
                )
            if response.status_code == 429 or response.status_code >= 500:
                last_error = UltraError(f"HTTP {response.status_code}")
                if attempt >= retries:
                    break
                time.sleep(delay)
                delay *= 2
                continue
            raise UltraError(f"GET {url} failed with HTTP {response.status_code}")

        raise UltraError(f"GET {url} failed: {last_error}")

    def wait_until_authenticated(self, timeout=60):
        """Poll /users/me until the SSO session is fully usable.

        Right after landing on /ultra the session cookies can take a moment to
        propagate, which briefly yields 401/403. Retrying avoids that race.
        """
        deadline = time.time() + timeout
        last_error = None
        while time.time() < deadline:
            try:
                me = self.get(f"{API_ROOT}/users/me")
                if me:
                    return me
            except UltraAuthError as exc:
                last_error = exc
                time.sleep(2)
        if last_error:
            raise last_error
        raise UltraAuthError("Blackboard session did not become ready in time.")

    def get_all(self, path, params=None, max_pages=300):
        """Follow Blackboard paging until every result has been collected."""
        collected = []
        next_ref = path
        next_params = params if params is not None else {"limit": 200}
        visited = set()
        while next_ref and next_ref not in visited and len(visited) < max_pages:
            visited.add(next_ref)
            payload = self.get(next_ref, params=next_params)
            if not payload:
                break
            collected.extend(payload.get("results") or [])
            next_ref = (payload.get("paging") or {}).get("nextPage")
            next_params = None
        return collected

    # ------------------------------------------------------------ downloads
    def download(self, url, dest, expected_size=None):
        """Stream a URL to `dest`. Returns (status, size, info).

        status is one of: 'saved', 'skipped', 'failed'.
        """
        if not url:
            return "failed", 0, "no url"
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)

        if os.path.exists(dest) and not self.overwrite:
            existing = os.path.getsize(dest)
            if expected_size is None or existing == expected_size:
                return "skipped", existing, "already exists"

        tmp = dest + ".part"
        try:
            with self.session.get(
                url, stream=True, timeout=300, allow_redirects=True
            ) as response:
                if response.status_code in (401, 403):
                    return "failed", 0, f"HTTP {response.status_code} (no access)"
                if response.status_code == 404:
                    return "failed", 0, "HTTP 404 (not found)"
                if response.status_code >= 400:
                    return "failed", 0, f"HTTP {response.status_code}"

                total = 0
                with open(tmp, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=131072):
                        if chunk:
                            handle.write(chunk)
                            total += len(chunk)

            if total == 0:
                _quiet_remove(tmp)
                return "failed", 0, "empty response body"
            os.replace(tmp, dest)
            return "saved", total, "ok"
        except requests.RequestException as exc:
            _quiet_remove(tmp)
            return "failed", 0, str(exc)
        except OSError as exc:
            _quiet_remove(tmp)
            return "failed", 0, f"write error: {exc}"


def _quiet_remove(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# =========================================================================== #
# Course / term discovery
# =========================================================================== #

def list_terms_and_courses(client, status_callback):
    """Return every accessible Ultra course, grouped later by term."""
    status_callback("Scanning for all available terms and courses...")
    client.wait_until_authenticated()

    # The internal Ultra endpoint reflects exactly what the web UI shows and
    # excludes cross-listed sections the student cannot actually open.
    memberships = None
    try:
        memberships = client.get_all("/learn/api/v1/users/me/memberships")
    except UltraError:
        memberships = None
    if not memberships:
        memberships = client.get_all(f"{API_ROOT}/users/me/courses")
    status_callback(f"  - found {len(memberships)} course membership(s)")

    courses = []
    seen_ids = set()
    term_cache = {}
    skipped = 0

    for membership in memberships:
        course_id = membership.get("courseId")
        if not course_id or course_id in seen_ids:
            continue
        seen_ids.add(course_id)

        # Prefer the richer internal course detail; fall back to the public one.
        details = None
        for path in (
            f"/learn/api/v1/courses/{course_id}",
            f"{API_ROOT}/courses/{course_id}",
        ):
            try:
                details = client.get(path, allow_404=True)
            except UltraError:
                details = None
            if details:
                break
        if not details or details.get("isOrganization") or details.get("organization"):
            skipped += 1
            continue

        name = (
            details.get("name")
            or details.get("displayName")
            or details.get("courseId")
            or course_id
        )
        code = details.get("courseId") or ""

        term_id = details.get("termId")
        term_name = "Unknown Term"
        term_obj = details.get("term")
        if isinstance(term_obj, dict):
            term_name = (
                term_obj.get("name")
                or term_obj.get("description")
                or term_obj.get("id")
                or term_name
            )
        elif isinstance(term_obj, str) and term_obj:
            term_id = term_id or term_obj
        if term_name == "Unknown Term" and term_id:
            if term_id not in term_cache:
                term = None
                for path in (
                    f"{API_ROOT}/terms/{term_id}",
                    f"/learn/api/v1/terms/{term_id}",
                ):
                    try:
                        term = client.get(path, allow_404=True)
                    except UltraError:
                        term = None
                    if term:
                        break
                term_cache[term_id] = (term or {}).get("name") or "Unknown Term"
            term_name = term_cache[term_id]

        courses.append(
            {
                "name": name,
                "code": code,
                "id": course_id,
                "term": term_name,
                "url": course_id,  # kept for compatibility with the UI
            }
        )

    if skipped:
        status_callback(f"  - skipped {skipped} organization/inaccessible course(s)")
    status_callback(f"  - {len(courses)} course(s) discovered")
    return courses


# =========================================================================== #
# Course downloader (content tree walk + materialisation)
# =========================================================================== #

class CourseDownloader:
    def __init__(self, client, driver, status_callback, options=None):
        self.client = client
        self.driver = driver
        self.status = status_callback
        self.options = {
            "documents": True,
            "links": True,
            "announcements": True,
            "syllabus": True,
        }
        if options:
            self.options.update(options)
        self._used_names = {}
        self._lock = threading.Lock()
        self._syllabus_seen = False
        self._root_failed = False
        self.failed_courses = []
        self.stats = {
            "files": 0,
            "skipped": 0,
            "failed": 0,
            "links": 0,
            "documents": 0,
            "announcements": 0,
        }

    # ------------------------------------------------------------- helpers
    def _alloc(self, directory, name):
        key = os.path.normcase(os.path.abspath(directory))
        with self._lock:
            used = self._used_names.setdefault(key, set())
            candidate = name
            base, ext = os.path.splitext(name)
            index = 1
            while candidate.lower() in used:
                candidate = f"{base} ({index}){ext}"
                index += 1
            used.add(candidate.lower())
            return candidate

    def _write_url(self, directory, title, url):
        filename = self._alloc(
            directory, sanitize_component(title, fallback="link") + ".url"
        )
        path = os.path.join(directory, filename)
        try:
            write_url_file(path, url)
            self.stats["links"] += 1
            self.status(f"        LINK: {filename}")
        except OSError as exc:
            self.status(f"        FAILED link {filename}: {exc}")

    # --------------------------------------------------------------- public
    def run(self, course, base_dir):
        self._syllabus_seen = False
        self._root_failed = False
        course_dir = os.path.join(
            base_dir,
            sanitize_component(course.get("term", "Unknown Term")),
            sanitize_component(course.get("name", course.get("id", "course"))),
        )
        os.makedirs(course_dir, exist_ok=True)
        self.status(f"    Output folder: {course_dir}")

        self._walk(course["id"], None, course_dir, depth=0)

        if self._root_failed:
            self.failed_courses.append(
                f"{course.get('name')} [{course.get('term')}]"
            )

        if self.options.get("announcements", True):
            try:
                self._download_announcements(course["id"], course_dir)
            except UltraError as exc:
                self.status(f"    ! Announcements failed: {exc}")

        if self.options.get("syllabus", True) and not self._syllabus_seen:
            self._try_syllabus(course["id"], course_dir)

        return self.stats

    # ------------------------------------------------------------ tree walk
    def _walk(self, course_id, folder_id, dir_path, depth):
        if folder_id is None:
            path = f"{API_ROOT}/courses/{course_id}/contents"
        else:
            path = f"{API_ROOT}/courses/{course_id}/contents/{folder_id}/children"

        try:
            items = self.client.get_all(path)
        except UltraError as exc:
            self.status(f"{'  ' * depth}! Could not read contents: {exc}")
            if depth == 0:
                self._root_failed = True
            return

        items.sort(key=lambda item: (item.get("position") or 0, item.get("title") or ""))

        for item in items:
            content_id = item.get("id")
            if not content_id:
                continue
            handler = ((item.get("contentHandler") or {}).get("id") or "").strip()
            title = (item.get("title") or content_id).strip()
            kind = kind_for_handler(handler, item)

            if kind == "folder":
                sub_dir = os.path.join(
                    dir_path, self._alloc(dir_path, sanitize_component(title))
                )
                os.makedirs(sub_dir, exist_ok=True)
                self.status(f"{'  ' * depth}+ Folder: {title}")
                self._walk(course_id, content_id, sub_dir, depth + 1)

            elif kind == "file":
                self._handle_file(course_id, item, title, dir_path)

            elif kind == "document":
                if "syllabus" in handler or title.lower().startswith("syllabus"):
                    self._syllabus_seen = True
                if self.options.get("documents", True):
                    self._handle_document(course_id, item, title, dir_path)
                else:
                    self.status(f"{'  ' * depth}- Document skipped: {title}")

            elif kind == "link":
                if self.options.get("links", True):
                    self._handle_link(item, title, dir_path)
                else:
                    self.status(f"{'  ' * depth}- Link skipped: {title}")

            elif kind == "assessment":
                self._handle_assessment(course_id, item, title, dir_path)

            else:
                self._handle_other(course_id, item, title, dir_path)

    # ------------------------------------------------------------- handlers
    def _handle_file(self, course_id, item, title, dir_path):
        self.status(f"        File: {title}")
        downloaded = self._download_attachments(
            course_id, item.get("id"), dir_path, title
        )
        if not downloaded:
            # No attachment resource: fall back to any embedded body files.
            if item.get("body"):
                self._download_embeds(course_id, dir_path, title, item.get("body"))
            else:
                self.status(f"          - no downloadable attachment for '{title}'")

    def _handle_document(self, course_id, item, title, dir_path):
        self.status(f"        Document: {title}")
        body = item.get("body") or ""
        if body:
            link_map = self._download_embeds(course_id, dir_path, title, body)
            for original, local in link_map.items():
                body = body.replace(original, local)
        if not body:
            body = "<p><em>No content body was returned by the Blackboard API for this item.</em></p>"

        filename = self._alloc(
            dir_path,
            sanitize_component(title, fallback=item.get("id", "document")) + ".html",
        )
        path = os.path.join(dir_path, filename)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(html_document(title, body))
            self.stats["documents"] += 1
            self.status(f"          SAVED: {filename}")
        except OSError as exc:
            self.status(f"          FAILED document {filename}: {exc}")

    def _handle_link(self, item, title, dir_path):
        handler = item.get("contentHandler") or {}
        url = (
            handler.get("url")
            or handler.get("href")
            or handler.get("absoluteUrl")
            or item.get("url")
        )
        if not url:
            for link in item.get("links") or []:
                if link.get("href"):
                    url = link["href"]
                    break
        if url:
            self._write_url(dir_path, title, url)
        else:
            self.status(f"        - Link has no URL: {title}")

    def _handle_assessment(self, course_id, item, title, dir_path):
        self.status(f"        Assessment/Assignment: {title}")
        outline_url = f"{BASE_URL}ultra/courses/{course_id}/outline"
        self._write_url(dir_path, title, outline_url)
        self._download_attachments(course_id, item.get("id"), dir_path, title)

    def _handle_other(self, course_id, item, title, dir_path):
        url = None
        for link in item.get("links") or []:
            if link.get("href"):
                url = link["href"]
                break
        if url and url.startswith("/"):
            url = BASE_URL.rstrip("/") + url
        self._write_url(
            dir_path, title, url or f"{BASE_URL}ultra/courses/{course_id}/outline"
        )

    # ---------------------------------------------------------- attachments
    def _download_attachments(self, course_id, content_id, dir_path, title):
        if not content_id:
            return 0
        try:
            payload = self.client.get(
                f"{API_ROOT}/courses/{course_id}/contents/{content_id}/attachments",
                allow_404=True,
            )
        except UltraError as exc:
            self.status(f"          - attachments lookup failed: {exc}")
            return 0
        if not payload:
            return 0

        count = 0
        for attachment in payload.get("results") or []:
            attachment_id = attachment.get("id")
            if not attachment_id:
                continue
            name = (
                attachment.get("fileName")
                or attachment.get("name")
                or f"{title}_{attachment_id}"
            )
            name = sanitize_component(name, fallback=attachment_id)
            filename = self._alloc(dir_path, name)
            dest = os.path.join(dir_path, filename)
            url = (
                f"{self.client.base}{API_ROOT}/courses/{course_id}/contents/"
                f"{content_id}/attachments/{attachment_id}/download"
            )
            status, size, info = self.client.download(url, dest)
            if status == "saved":
                self.stats["files"] += 1
                self.status(f"          SAVED: {filename} ({size} bytes)")
                count += 1
            elif status == "skipped":
                self.stats["skipped"] += 1
                self.status(f"          SKIPPED (exists): {filename}")
                count += 1
            else:
                self.stats["failed"] += 1
                self.status(f"          FAILED: {filename} - {info}")
        return count

    def _download_embeds(self, course_id, dir_path, title, body):
        """Download files referenced inside an Ultra body. Returns url->name map."""
        link_map = {}
        for raw_url in extract_bbcswebdav_urls(body):
            url = raw_url
            if url.startswith("//"):
                url = "https:" + url
            elif url.startswith("/"):
                url = BASE_URL.rstrip("/") + url
            if not url.startswith("http"):
                continue

            parsed = urlparse(url)
            name = unquote(os.path.basename(parsed.path)) or title
            name = sanitize_component(name, fallback="file")
            filename = self._alloc(dir_path, name)
            dest = os.path.join(dir_path, filename)

            status, size, info = self.client.download(url, dest)
            if status == "failed":
                separator = "&" if "?" in url else "?"
                status, size, info = self.client.download(
                    url + separator + "xythos-download=true", dest
                )

            if status == "saved":
                self.stats["files"] += 1
                self.status(f"          SAVED (embedded): {filename} ({size} bytes)")
                link_map[raw_url] = filename
            elif status == "skipped":
                self.stats["skipped"] += 1
                link_map[raw_url] = filename
            else:
                self.stats["failed"] += 1
                self.status(f"          FAILED (embedded): {filename} - {info}")
        return link_map

    # -------------------------------------------------------- announcements
    def _download_announcements(self, course_id, course_dir):
        announcements = self.client.get_all(
            f"{API_ROOT}/courses/{course_id}/announcements"
        )
        if not announcements:
            self.status("    No announcements found.")
            return

        out_dir = os.path.join(course_dir, "Announcements")
        os.makedirs(out_dir, exist_ok=True)
        self.status(f"    Downloading {len(announcements)} announcement(s)...")

        announcements.sort(key=lambda a: (a.get("created") or "", a.get("id") or ""))

        for announcement in announcements:
            title = (announcement.get("title") or "announcement").strip()
            created = (announcement.get("created") or "")[:10]
            body = announcement.get("body") or ""
            if body:
                link_map = self._download_embeds(course_id, out_dir, title, body)
                for original, local in link_map.items():
                    body = body.replace(original, local)

            base_name = sanitize_component(
                f"{created}_{title}".strip("_"), fallback="announcement"
            )
            filename = self._alloc(out_dir, base_name + ".html")
            path = os.path.join(out_dir, filename)
            try:
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(html_document(title, body))
                self.stats["announcements"] += 1
                self.status(f"        ANNOUNCEMENT: {filename}")
            except OSError as exc:
                self.status(f"        FAILED announcement {filename}: {exc}")

    # -------------------------------------------------------------- syllabus
    def _try_syllabus(self, course_id, course_dir):
        """Best-effort: save the rendered syllabus page if the tenant exposes one."""
        if self.driver is None:
            return
        url = f"{BASE_URL}ultra/courses/{course_id}/syllabus"
        try:
            self.driver.get(url)
            time.sleep(3)
            content = None
            for selector in ("main", "#syllabus", ".syllabus", "[data-testid='syllabus']"):
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        content = elements[0].get_attribute("outerHTML")
                        if content and len(content) > 200:
                            break
                except WebDriverException:
                    continue
            if not content or len(content) < 200:
                self.status("    No syllabus page available.")
                return
            out_dir = os.path.join(course_dir, "Syllabus")
            os.makedirs(out_dir, exist_ok=True)
            filename = self._alloc(out_dir, "syllabus.html")
            with open(os.path.join(out_dir, filename), "w", encoding="utf-8") as handle:
                handle.write(html_document("Syllabus", content))
            self.stats["documents"] += 1
            self.status(f"    SYLLABUS SAVED: {filename}")
        except Exception as exc:  # noqa: BLE001 - syllabus is best-effort
            self.status(f"    Could not save syllabus: {exc}")


# =========================================================================== #
# GUI
# =========================================================================== #

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("KFUPM Blackboard Ultra Course Downloader")
        self.geometry("720x1060")
        self.resizable(0, 0)

        self.all_course_data = []
        self.course_checkboxes = []
        self._save_timer = None
        self._env_credentials = load_env_credentials()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        main_frame = ctk.CTkFrame(self)
        main_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        self.main_font = ctk.CTkFont(family="Roboto Medium", size=12)
        self.header_font = ctk.CTkFont(family="Roboto Medium", size=13, weight="bold")
        self.button_font = ctk.CTkFont(family="Roboto Medium", size=14, weight="bold")

        row_idx = 0
        main_frame.grid_columnconfigure(0, weight=0)
        main_frame.grid_columnconfigure(1, weight=1)
        main_frame.grid_columnconfigure(2, weight=0)

        # Username
        ctk.CTkLabel(main_frame, text="Username", font=self.header_font,
                     text_color=("gray10", "gray90")).grid(
            row=row_idx, column=0, sticky="w", pady=(0, 5))
        self.username_entry = ctk.CTkEntry(main_frame, width=200, font=self.main_font)
        self.username_entry.grid(row=row_idx, column=1, columnspan=2, sticky="ew",
                                 pady=(0, 5), padx=(5, 0))
        row_idx += 1

        # Password
        ctk.CTkLabel(main_frame, text="Password", font=self.header_font,
                     text_color=("gray10", "gray90")).grid(
            row=row_idx, column=0, sticky="w", pady=(0, 5))
        self.password_entry = ctk.CTkEntry(main_frame, width=200, show="*",
                                           font=self.main_font)
        self.password_entry.grid(row=row_idx, column=1, columnspan=2, sticky="ew",
                                 pady=(0, 5), padx=(5, 0))
        row_idx += 1

        # Download path
        path_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        path_frame.grid(row=row_idx, column=0, columnspan=3, sticky="ew", pady=(0, 5))
        path_frame.grid_columnconfigure(0, weight=0)
        path_frame.grid_columnconfigure(1, weight=1)
        path_frame.grid_columnconfigure(2, weight=0)

        ctk.CTkLabel(path_frame, text="Download To", font=self.header_font,
                     text_color=("gray10", "gray90")).grid(
            row=0, column=0, sticky="w", padx=(0, 5))
        self.path_var = tk.StringVar(
            value=os.path.join(os.path.expanduser("~"), "Desktop",
                               "KFUPM_Blackboard_Downloads")
        )
        self.path_entry = ctk.CTkEntry(path_frame, textvariable=self.path_var,
                                       font=self.main_font)
        self.path_entry.grid(row=0, column=1, sticky="ew", padx=(5, 5))
        self.browse_button = ctk.CTkButton(path_frame, text="Browse", width=80,
                                           command=self.browse_directory,
                                           font=self.button_font)
        self.browse_button.grid(row=0, column=2, sticky="ew")
        self.browse_button.lift()
        row_idx += 1

        # Browser selection
        browser_frame = ctk.CTkFrame(main_frame)
        browser_frame.grid(row=row_idx, column=0, columnspan=3, sticky="ew",
                           pady=10, padx=2)
        ctk.CTkLabel(browser_frame,
                     text="Browser for login automation (must be installed)",
                     font=self.header_font,
                     text_color=("gray10", "gray90")).pack(
            side="top", anchor="w", pady=5, padx=5)
        rb_frame = ctk.CTkFrame(browser_frame, fg_color="transparent")
        rb_frame.pack(side="top", anchor="w", pady=5, padx=5)
        self.browser_var = tk.StringVar(value="firefox")
        self.firefox_rb = ctk.CTkRadioButton(
            rb_frame, text="Use Firefox", variable=self.browser_var, value="firefox",
            font=self.header_font, text_color=("gray10", "gray90"))
        self.firefox_rb.pack(side="left", padx=(0, 20))
        self.chrome_rb = ctk.CTkRadioButton(
            rb_frame, text="Use Chrome", variable=self.browser_var, value="chrome",
            font=self.header_font, text_color=("gray10", "gray90"))
        self.chrome_rb.pack(side="left")
        row_idx += 1

        # Headless
        self.headless_var = tk.BooleanVar(value=True)
        self.headless_check = ctk.CTkCheckBox(
            main_frame,
            text="Run in Headless Mode (no browser window visible - recommended)",
            variable=self.headless_var, font=self.header_font,
            text_color=("gray10", "gray90"))
        self.headless_check.grid(row=row_idx, column=0, columnspan=3, sticky="w", pady=5)
        row_idx += 1

        # Content options
        options_frame = ctk.CTkFrame(main_frame)
        options_frame.grid(row=row_idx, column=0, columnspan=3, sticky="ew",
                           pady=(0, 5), padx=2)
        ctk.CTkLabel(options_frame, text="What to download", font=self.header_font,
                     text_color=("gray10", "gray90")).pack(
            side="top", anchor="w", pady=5, padx=5)
        options_row = ctk.CTkFrame(options_frame, fg_color="transparent")
        options_row.pack(side="top", anchor="w", pady=5, padx=5)
        self.documents_var = tk.BooleanVar(value=True)
        self.links_var = tk.BooleanVar(value=True)
        self.announcements_var = tk.BooleanVar(value=True)
        self.syllabus_var = tk.BooleanVar(value=True)
        for var, text in (
            (self.documents_var, "Ultra documents (.html)"),
            (self.links_var, "External links (.url)"),
            (self.announcements_var, "Announcements"),
            (self.syllabus_var, "Syllabus"),
        ):
            ctk.CTkCheckBox(options_row, text=text, variable=var,
                            font=self.header_font,
                            text_color=("gray10", "gray90")).pack(
                side="left", padx=(0, 12))
        row_idx += 1

        # Scan button
        self.scan_button = ctk.CTkButton(main_frame, text="1. Scan Courses",
                                         command=self.start_scan_thread,
                                         font=self.button_font, height=40)
        self.scan_button.grid(row=row_idx, column=0, columnspan=3, sticky="ew", pady=10)
        row_idx += 1

        # Course list
        ctk.CTkLabel(main_frame, text="Select Course(s) to Download",
                     font=self.header_font, text_color=("gray10", "gray90")).grid(
            row=row_idx, column=0, columnspan=3, sticky="w", pady=(10, 0))
        row_idx += 1

        self.course_scroll_frame = ctk.CTkScrollableFrame(
            main_frame, label_text="Available Courses", label_font=self.header_font,
            height=200)
        self.course_scroll_frame.grid(row=row_idx, column=0, columnspan=3,
                                      sticky="nsew", pady=5)
        try:
            canvas = self.course_scroll_frame._parent_canvas
            canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
            canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))
            canvas.bind_all("<MouseWheel>",
                            lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))
        except Exception:
            pass
        row_idx += 1

        # Download button
        self.download_button = ctk.CTkButton(
            main_frame, text="2. Download Selected Course(s)",
            command=self.start_download_thread, state="disabled", height=40,
            font=self.button_font)
        self.download_button.grid(row=row_idx, column=0, columnspan=3, pady=15,
                                  sticky="ew")
        row_idx += 1

        # Status + progress
        status_frame = ctk.CTkFrame(main_frame)
        status_frame.grid(row=row_idx, column=0, columnspan=3, sticky="nsew",
                          pady=(10, 0))
        status_frame.columnconfigure(0, weight=1)
        status_frame.rowconfigure(0, weight=1)
        self.status_text = ctk.CTkTextbox(status_frame, height=150, state="disabled",
                                          wrap="word", font=("Consolas", 11))
        self.status_text.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        row_idx += 1

        self.progress_bar = ctk.CTkProgressBar(main_frame, orientation="horizontal",
                                               mode="determinate")
        self.progress_bar.grid(row=row_idx, column=0, columnspan=3, sticky="ew",
                               pady=(5, 10))
        self.progress_bar.set(0)

        main_frame.rowconfigure(8, weight=1)   # course list
        main_frame.rowconfigure(10, weight=1)  # status log

        self.load_credentials()
        if self._env_credentials.get("username"):
            self.update_status("Loaded credentials from .env")
        for widget in (self.username_entry, self.password_entry, self.path_entry):
            widget.bind("<KeyRelease>", lambda e: self.save_credentials_throttled())

    # ------------------------------------------------------------- settings
    def save_credentials_throttled(self):
        if self._save_timer:
            self.after_cancel(self._save_timer)
        self._save_timer = self.after(1000, self.save_credentials)

    def save_credentials(self):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            # If credentials come from .env, keep them out of config.ini.
            has_env_creds = bool(self._env_credentials.get("username"))
            with open(CONFIG_FILE, "w", encoding="utf-8") as handle:
                if not has_env_creds:
                    handle.write(f"username={self.username_entry.get()}\n")
                    # Stored in plain text for local convenience only.
                    handle.write(f"password={self.password_entry.get()}\n")
                handle.write(f"download_path={self.path_var.get()}\n")
                handle.write(f"browser_choice={self.browser_var.get()}\n")
                handle.write(f"headless_mode={self.headless_var.get()}\n")
                handle.write(f"documents={self.documents_var.get()}\n")
                handle.write(f"links={self.links_var.get()}\n")
                handle.write(f"announcements={self.announcements_var.get()}\n")
                handle.write(f"syllabus={self.syllabus_var.get()}\n")
        except OSError as exc:
            self.update_status(f"Warning: Could not save settings: {exc}")

    def load_credentials(self):
        # .env credentials take priority over anything saved in config.ini.
        if self._env_credentials.get("username"):
            self.username_entry.insert(0, self._env_credentials["username"])
        if self._env_credentials.get("password"):
            self.password_entry.insert(0, self._env_credentials["password"])

        try:
            if not os.path.exists(CONFIG_FILE):
                return
            with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
                for line in handle:
                    if "=" not in line:
                        continue
                    name, value = line.strip().split("=", 1)
                    if name == "username":
                        if not self._env_credentials.get("username"):
                            self.username_entry.insert(0, value)
                    elif name == "password":
                        if not self._env_credentials.get("password"):
                            self.password_entry.insert(0, value)
                    elif name == "download_path":
                        self.path_var.set(value)
                    elif name == "browser_choice":
                        self.browser_var.set(value)
                    elif name == "headless_mode":
                        self.headless_var.set(value.lower() == "true")
                    elif name == "documents":
                        self.documents_var.set(value.lower() == "true")
                    elif name == "links":
                        self.links_var.set(value.lower() == "true")
                    elif name == "announcements":
                        self.announcements_var.set(value.lower() == "true")
                    elif name == "syllabus":
                        self.syllabus_var.set(value.lower() == "true")
        except (OSError, ValueError) as exc:
            self.update_status(f"Warning: Could not load saved settings: {exc}")

    def browse_directory(self):
        directory = filedialog.askdirectory(initialdir=self.path_var.get())
        if directory:
            self.path_var.set(directory)
            self.save_credentials()

    # --------------------------------------------------------------- status
    def update_status(self, message):
        if self and self.status_text:
            self.after(0, self._update_status_thread_safe, message)

    def _update_status_thread_safe(self, message):
        try:
            self.status_text.configure(state="normal")
            self.status_text.insert(tk.END, message + "\n")
            self.status_text.see(tk.END)
            self.status_text.configure(state="disabled")
        except tk.TclError:
            pass

    def update_progress(self, value):
        if self and self.progress_bar:
            self.after(0, self._update_progress_thread_safe, value)

    def _update_progress_thread_safe(self, value):
        try:
            self.progress_bar.set(max(0.0, min(1.0, value / 100.0)))
        except tk.TclError:
            pass

    def _show_error(self, title, message):
        self.after(0, lambda: messagebox.showerror(title, message))

    def _show_info(self, title, message):
        self.after(0, lambda: messagebox.showinfo(title, message))

    def set_ui_state(self, enabled):
        state = "normal" if enabled else "disabled"
        widgets = [
            self.username_entry, self.password_entry, self.path_entry,
            self.browse_button, self.scan_button, self.headless_check,
            self.firefox_rb, self.chrome_rb,
        ]
        for widget in widgets:
            if widget is None:
                continue
            try:
                widget.configure(state=state)
            except (tk.TclError, ValueError):
                pass

        if enabled and self.all_course_data:
            self.download_button.configure(state="normal")
        else:
            self.download_button.configure(state="disabled")

    # ----------------------------------------------------------------- scan
    def start_scan_thread(self):
        self.set_ui_state(False)
        for checkbox in self.course_checkboxes:
            checkbox["checkbox"].destroy()
        self.course_checkboxes = []
        self.all_course_data = []
        self.status_text.configure(state="normal")
        self.status_text.delete(1.0, tk.END)
        self.status_text.configure(state="disabled")
        self.update_status("Scan initiated...")
        threading.Thread(target=self.scan_courses_task, daemon=True).start()

    def scan_courses_task(self):
        username = self.username_entry.get().strip()
        password = self.password_entry.get()
        if not username or not password:
            self._show_error("Input Error", "Username and Password are required.")
            self.after(0, self.set_ui_state, True)
            return

        self.save_credentials()
        driver = None
        try:
            driver = setup_driver(
                self.browser_var.get(), self.update_status, self.headless_var.get()
            )
            cookies = login(driver, username, password, self.update_status)
            client = UltraClient(BASE_URL, cookies, self.update_status, driver=driver)
            self.all_course_data = list_terms_and_courses(client, self.update_status)

            if self.all_course_data:
                self.all_course_data.sort(
                    key=lambda item: (item.get("term", ""), item.get("name", ""))
                )
                self.update_status(
                    f"Scan complete. Found {len(self.all_course_data)} course(s)."
                )
                self.after(0, self._populate_course_list)
            else:
                self.update_status("Scan complete: no courses were found.")
                self.after(0, lambda: self.download_button.configure(state="disabled"))

        except RuntimeError as exc:
            self.update_status(f"Driver/Login error: {exc}")
            self._show_error("Login Error", str(exc))
        except UltraAuthError as exc:
            self.update_status(f"Authentication error: {exc}")
            self._show_error("Authentication Error", str(exc))
        except Exception as exc:  # noqa: BLE001
            self.update_status(f"An error occurred during scan: {exc}")
            self.update_status(traceback.format_exc())
            self._show_error("Scan Error", f"An unexpected error occurred: {exc}")
        finally:
            if driver:
                try:
                    driver.quit()
                except WebDriverException as exc:
                    self.update_status(f"Note: error quitting driver: {exc}")
            self.after(0, self.set_ui_state, True)

    def _populate_course_list(self):
        for child in self.course_scroll_frame.winfo_children():
            child.destroy()
        self.course_checkboxes = []

        current_term = None
        term_course_frame = None

        def toggle_term(term_value, state_var):
            new_state = state_var.get()
            for item in self.course_checkboxes:
                if item["course_data"].get("term") == term_value:
                    if new_state:
                        item["checkbox"].select()
                    else:
                        item["checkbox"].deselect()

        for course in self.all_course_data:
            term = course.get("term", "Unknown Term")
            if term != current_term:
                current_term = term
                term_var = tk.BooleanVar(value=False)
                term_checkbox = ctk.CTkCheckBox(
                    self.course_scroll_frame, text=f"--- {current_term} ---",
                    variable=term_var, font=self.header_font,
                    text_color=("gray10", "gray90"),
                    command=lambda t=current_term, v=term_var: toggle_term(t, v))
                term_checkbox.pack(side="top", fill="x", padx=5, pady=(10, 2))
                term_course_frame = ctk.CTkFrame(self.course_scroll_frame,
                                                 fg_color="transparent")
                term_course_frame.pack(fill="x", padx=15, pady=2)

            checkbox = ctk.CTkCheckBox(term_course_frame, text=course["name"],
                                       font=self.header_font,
                                       text_color=("gray10", "gray90"))
            checkbox.pack(fill="x", anchor="w", pady=2)
            self.course_checkboxes.append(
                {"checkbox": checkbox, "course_data": course}
            )

        self.download_button.configure(state="normal")

    # ------------------------------------------------------------- download
    def start_download_thread(self):
        selected = [
            item["course_data"]
            for item in self.course_checkboxes
            if item["checkbox"].get() == 1
        ]
        if not selected:
            messagebox.showwarning("No Selection",
                                   "Please select at least one course to download.")
            return

        self.set_ui_state(False)
        self.update_status("Download initiated...")
        threading.Thread(
            target=self.download_courses_task, args=(selected,), daemon=True
        ).start()

    def download_courses_task(self, courses_to_process):
        username = self.username_entry.get().strip()
        password = self.password_entry.get()
        options = {
            "documents": self.documents_var.get(),
            "links": self.links_var.get(),
            "announcements": self.announcements_var.get(),
            "syllabus": self.syllabus_var.get(),
        }

        driver = None
        try:
            driver = setup_driver(
                self.browser_var.get(), self.update_status, self.headless_var.get()
            )
            cookies = login(driver, username, password, self.update_status)
            client = UltraClient(BASE_URL, cookies, self.update_status, driver=driver)
            client.wait_until_authenticated()
            downloader = CourseDownloader(client, driver, self.update_status, options)

            total = len(courses_to_process)
            self.update_status(
                f"Starting download for {total} selected course(s)..."
            )

            for index, course in enumerate(courses_to_process):
                self.update_status(
                    f"\n--- ({index + 1}/{total}) Course: {course['name']} "
                    f"(Term: {course.get('term', 'Unknown')}) ---"
                )
                self.after(0, self.update_progress, (index / total) * 100)
                try:
                    downloader.run(course, self.path_var.get())
                except UltraAuthError as exc:
                    self.update_status(f"  ! Session error, stopping: {exc}")
                    break
                except Exception as exc:  # noqa: BLE001
                    self.update_status(f"  ! Error processing course: {exc}")
                    self.update_status(traceback.format_exc())
                self.after(0, self.update_progress, ((index + 1) / total) * 100)

            stats = downloader.stats
            self.update_status(
                "\nAll selected courses processed. "
                f"Files: {stats['files']} saved, {stats['skipped']} skipped, "
                f"{stats['failed']} failed. Links: {stats['links']}. "
                f"Documents: {stats['documents']}. "
                f"Announcements: {stats['announcements']}."
            )
            if downloader.failed_courses:
                self.update_status(
                    "Courses whose content Blackboard refused (no access):"
                )
                for name in downloader.failed_courses:
                    self.update_status(f"  - {name}")
            self._show_info(
                "Download Complete",
                "All selected courses have been processed. "
                "Check the status window for details.",
            )
        except RuntimeError as exc:
            self.update_status(f"Driver/Login error: {exc}")
            self._show_error("Login Error", str(exc))
        except Exception as exc:  # noqa: BLE001
            self.update_status(f"A critical error occurred: {exc}")
            self.update_status(traceback.format_exc())
            self._show_error("Download Error", f"A critical error occurred: {exc}")
        finally:
            if driver:
                try:
                    driver.quit()
                except WebDriverException as exc:
                    self.update_status(f"Note: error quitting driver: {exc}")
            self.after(0, self.set_ui_state, True)
            self.after(0, self.update_progress, 0)


# =========================================================================== #
# Headless CLI
# =========================================================================== #

def build_cli_parser():
    parser = argparse.ArgumentParser(
        description="KFUPM Blackboard Ultra Course Downloader"
    )
    parser.add_argument("--cli", action="store_true",
                        help="Run headless in the terminal (no GUI)")
    parser.add_argument("--list", action="store_true",
                        help="List matching courses and exit without downloading")
    parser.add_argument("--term", default=None,
                        help="Only include courses whose term name contains this text")
    parser.add_argument("--dest", default=None,
                        help="Download destination folder")
    parser.add_argument("--browser", choices=["firefox", "chrome"], default="firefox",
                        help="Browser to drive for login (default: firefox)")
    parser.add_argument("--show-browser", action="store_true",
                        help="Do not run the login browser headless")
    parser.add_argument("--no-documents", action="store_true",
                        help="Skip Ultra document pages")
    parser.add_argument("--no-links", action="store_true",
                        help="Skip external link shortcuts")
    parser.add_argument("--no-announcements", action="store_true",
                        help="Skip announcements")
    parser.add_argument("--no-syllabus", action="store_true",
                        help="Skip the syllabus")
    return parser


def run_cli(args):
    def log(message):
        print(message, flush=True)

    credentials = load_env_credentials()
    username = os.environ.get("BB_USERNAME") or credentials.get("username")
    password = os.environ.get("BB_PASSWORD") or credentials.get("password")
    if not username or not password:
        log("ERROR: no credentials found. Add 'User'/'Password' to .env "
            "or set BB_USERNAME/BB_PASSWORD.")
        return 2

    dest = args.dest or os.path.join(
        os.path.expanduser("~"), "KFUPM_Blackboard_Downloads"
    )
    os.makedirs(dest, exist_ok=True)
    log(f"Destination: {dest}")

    driver = None
    try:
        driver = setup_driver(args.browser, log, headless=not args.show_browser)
        cookies = login(driver, username, password, log)
        client = UltraClient(BASE_URL, cookies, log, driver=driver)
        courses = list_terms_and_courses(client, log)

        available_terms = sorted({c.get("term", "") for c in courses})
        log(f"Available terms: {available_terms}")

        if args.term:
            needle = args.term.lower()
            courses = [
                c for c in courses
                if needle in (c.get("term") or "").lower()
                or needle == (c.get("term") or "").lower()
            ]
            log(f"{len(courses)} course(s) match term '{args.term}'")

        if args.list:
            for course in courses:
                print(f"  {course.get('term')} | {course.get('name')} | {course.get('id')}")
            return 0

        if not courses:
            log("No courses to download.")
            return 0

        options = {
            "documents": not args.no_documents,
            "links": not args.no_links,
            "announcements": not args.no_announcements,
            "syllabus": not args.no_syllabus,
        }
        downloader = CourseDownloader(client, driver, log, options)

        for index, course in enumerate(courses, 1):
            log(f"\n--- ({index}/{len(courses)}) {course.get('name')} "
                f"[{course.get('term')}] ---")
            try:
                downloader.run(course, dest)
            except UltraAuthError as exc:
                log(f"  ! Session error, stopping: {exc}")
                break
            except Exception as exc:  # noqa: BLE001
                log(f"  ! Error processing course: {exc}")
                log(traceback.format_exc())

        stats = downloader.stats
        log(f"\nDONE. files={stats['files']} skipped={stats['skipped']} "
            f"failed={stats['failed']} links={stats['links']} "
            f"documents={stats['documents']} announcements={stats['announcements']}")
        if downloader.failed_courses:
            log("Courses whose content Blackboard refused (no access):")
            for name in downloader.failed_courses:
                log(f"  - {name}")
        return 0
    except RuntimeError as exc:
        log(f"Driver/Login error: {exc}")
        return 3
    except UltraAuthError as exc:
        log(f"Authentication error: {exc}")
        return 4
    except Exception as exc:  # noqa: BLE001
        log(f"FATAL: {exc}")
        log(traceback.format_exc())
        return 1
    finally:
        if driver:
            try:
                driver.quit()
            except WebDriverException:
                pass


def main():
    args = build_cli_parser().parse_args()
    if args.cli or args.list:
        sys.exit(run_cli(args))
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
