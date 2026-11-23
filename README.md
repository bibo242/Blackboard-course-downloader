# Blackboard Ultra Course Downloader

A tool to locally back up entire Blackboard **Ultra** courses with a single
click. It logs into Blackboard through the institution's Single Sign-On, reads
every course through the Blackboard Learn REST API, and saves all materials to
your computer while preserving the original folder structure.

Implemented schools: **KFUPM** (default) and **IAU**. See
[Implemented schools](#implemented-schools) for details and for adding another
one.

> This is the Ultra version; the original project targeted Blackboard Classic.

---

## Features

- **Easy-to-use GUI:** no command-line knowledge required.
- **Bulk downloading:** one course, several courses, or entire terms at once.
- **Preserves structure:** replicates the exact Ultra outline folder hierarchy.
- **All file types:** PDF, PPT, DOCX, ZIP, videos, images, and more.
- **External web links:** saved as `.url` shortcuts.
- **Ultra documents:** saved as `.html` (with embedded files downloaded too).
- **Assignments & tests:** one folder per assessment with an assessment info
  page, instructor instructions/files, your submitted files, and a rendered
  page for online question/answer submissions.
- **Announcements:** saved as `.html` with attachments.
- **Syllabus:** saved when the course exposes one.
- **Browser choice:** Google Chrome or Mozilla Firefox.
- **Headless mode:** run the login browser invisibly in the background.
- **Incremental:** already-downloaded files are skipped.
- **Standalone application:** prebuilt Windows, macOS and Linux binaries need no
  Python installation.

---

## How it works

1. **Login** is performed in a real browser (Selenium) so the institution's
   SAML/ADFS Single Sign-On works exactly as it does manually. Your credentials
   are entered into the SSO page; they are not sent anywhere else.
2. **Course data** is read with the Blackboard Learn REST API
   (`/learn/api/public/v1/...`) using the session cookies from that browser.
   This is much faster and more reliable than scraping Ultra's rendered pages.
3. **Files** are streamed to disk from the attachment endpoints. Assessment
   submissions are fetched from the gradebook (latest attempt), and online
   question/answer submissions are rendered to HTML. `.url` shortcuts are
   written only for external links.

---

## How to use the application

1. Download the asset for your platform from the
   [latest release](https://github.com/bibo242/Blackboard-course-downloader/releases/latest):
   - **Windows:** `course_downloader.exe`
   - **macOS (Apple Silicon):** `course_downloader-macos-arm64.zip` (unzip and open the `.app`)
   - **Linux:** `course_downloader-linux-x86_64` (make it executable first)

   *(Windows may warn that the app is unsigned — click "More info" then "Run anyway".
   macOS may similarly block the unsigned `.app`; right-click it and choose Open.)*
2. Choose your school (**KFUPM** is the default) and enter your username and
   password.
3. Choose your browser (Chrome or Firefox) and whether to run headless.
4. Choose what to download (documents, links, announcements, syllabus).
5. Click **Scan Courses**.
6. Select the course(s) you want, choose a destination folder, and click
   **Download Selected Course(s)**.

### System requirements

- An active internet connection.
- [Google Chrome](https://www.google.com/chrome/) or
  [Mozilla Firefox](https://www.mozilla.org/firefox/) installed.
  (Firefox also requires `geckodriver` to be on your `PATH`.)

---

## Credentials via `.env`

Instead of typing your username and password every time, you can create a
`.env` file next to `course_downloader.py`:

```dotenv
# KFUPM (default school)
User = 202012345
Password = your-password

# IAU
IAU_User = your-username
IAUPassword = your-password
```

Key names are case-insensitive and only the selected school's keys are read.
Each school declares which spellings it accepts; the table below lists the
implemented ones. Spaces around `=` and surrounding quotes are ignored. The app
fills the fields on startup and keeps those credentials out of `config.ini`.

> `.env` is listed in `.gitignore`, so it is never committed. Never share it.

## Implemented schools

| School | Default | Blackboard URL | `.env` username | `.env` password |
|---|---|---|---|---|
| **KFUPM** | Yes | `https://blackboard.kfupm.edu.sa/` | `User` | `Password` |
| **IAU** | No | `https://vle.iau.edu.sa/` | `IAU_User` | `IAUPassword` |

The GUI dropdown and the CLI `--school` option list these entries. KFUPM is
selected on first run; the last choice is remembered in `config.ini`.

The course/content download logic is generic Blackboard Ultra, so adding a
school is normally just one entry in the `SCHOOLS` dict at the top of
`course_downloader.py`:

```python
"Example": {
    "base_url": "https://blackboard.example.edu/",
    "user_keys": ("example_user",),
    "pass_keys": ("examplepassword",),
},
```

The login step is the part that varies: it expects a SAML/ADFS-style form with
username and password fields. Add `login_url` if the Blackboard landing page
shows provider links instead of redirecting to SSO (as IAU does). Schools using
a different SSO or MFA may need extra selectors or a manual, non-headless
login.

To change the first-run default, edit `DEFAULT_SCHOOL` at the top of
`course_downloader.py`.

## Command line

```bash
# List IAU courses and exit
python course_downloader.py --cli --list --school IAU

# Download IAU courses matching a term
python course_downloader.py --cli --school IAU --term "2025" --dest ~/IAU
```

`--school` defaults to `KFUPM`; `--list` prints matching courses without
downloading anything.

---

## How to run from source

```bash
git clone https://github.com/bibo242/Blackboard-course-downloader.git
cd Blackboard-course-downloader

python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
python course_downloader.py
```

### Running the tests

The unit tests are fully offline (no Blackboard account needed):

```bash
python -m unittest discover -s tests -v
```

---

## Output structure

```
<Download To>/
└── <Term>/
    └── <Course Name>/
        ├── <Folder>/
        │   ├── lecture.pdf
        │   └── notes.docx
        ├── Assessments/
        │   └── Homework 1/
        │       ├── Homework 1_assessment_info.html
        │       ├── ME489_HW1.pdf
        │       ├── ME489_HW1_submission.pdf
        │       └── Homework 1_submission.html
        ├── Some Document.html
        ├── External Link.url
        └── Announcements/
            └── 2026-01-15_Welcome.html
```

Assessment folders contain:
- `<assessment>_assessment_info.html` — status, score, submission date, latest
  attempt, receipt, due date, instructions, instructor feedback, and file list.
- Instructor files with their original names.
- Your submitted files with a `_submission` suffix.
- `<assessment>_submission.html` for online question/answer submissions, with
  question images embedded in the page.

Assessment `.url` files are no longer created. `.url` is only used for real
external links.

---

## Building a standalone binary

Standalone binaries for **Windows, macOS and Linux** are built automatically by
the [build workflow](.github/workflows/build.yml) and attached to the
[latest release](https://github.com/bibo242/Blackboard-course-downloader/releases/latest).
Pushing to `main` (or running the workflow manually) refreshes the assets on the
`v2.0.0` release; pushing a `v*` tag publishes them on that release.

Releases:

- `course_downloader.exe` — Windows (x86_64)
- `course_downloader-macos-arm64.zip` — macOS (Apple Silicon)
- `course_downloader-linux-x86_64` — Linux (x86_64)

To build locally instead (PyInstaller does not cross-compile, so build on the
target OS):

```bash
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --onefile --windowed --icon icon.ico \
    --collect-all customtkinter \
    --collect-all selenium \
    --collect-all webdriver_manager \
    --name course_downloader course_downloader.py
```

`--collect-all selenium` (and `webdriver_manager`) is required: Selenium
lazily imports each browser's driver module, which PyInstaller's static
analysis would otherwise leave out of the bundle.

The output lands in `dist/`. On macOS use `--icon icon.icns` instead; with
`--windowed` the result is `dist/course_downloader.app`.

---

## Notes and limitations

- **SSO:** If a school enables multi-factor authentication, headless login
  cannot complete the second factor automatically. In that case uncheck
  **Headless Mode** and finish the prompt in the browser window.
- **Read-only:** the tool never modifies anything on Blackboard.
- **Assignments/tests:** the latest attempt is downloaded. Submitted files are
  saved with a `_submission` suffix; online question/answer submissions also get
  a rendered `<assessment>_submission.html`. Older attempts are not downloaded.
- **Syllabus:** saved on a best-effort basis because Ultra tenants expose it
  differently.

---

## Disclaimer

This tool is provided for educational and personal use only. The user is solely
responsible for complying with all terms of service of their institution and
Blackboard. Your credentials are used locally to log into Blackboard and are
not stored or transmitted elsewhere.

## License

MIT — see [LICENSE](LICENSE).
