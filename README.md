# KFUPM Blackboard Ultra Course Downloader

A tool to locally back up entire KFUPM Blackboard **Ultra** courses with a single
click. It logs into Blackboard through KFUPM Single Sign-On, reads every course
through the Blackboard Learn REST API, and saves all materials to your computer
while preserving the original folder structure.

> This is the Ultra version. The original project targeted Blackboard Classic,
> which no longer matches KFUPM's current Blackboard.

---

## Features

- **Easy-to-use GUI:** no command-line knowledge required.
- **Bulk downloading:** one course, several courses, or entire terms at once.
- **Preserves structure:** replicates the exact Ultra outline folder hierarchy.
- **All file types:** PDF, PPT, DOCX, ZIP, videos, images, and more.
- **External web links:** saved as `.url` shortcuts.
- **Ultra documents:** saved as `.html` (with embedded files downloaded too).
- **Assignments & tests:** saved as `.url` shortcuts plus any attachments.
- **Announcements:** saved as `.html` with attachments.
- **Syllabus:** saved when the course exposes one.
- **Browser choice:** Google Chrome or Mozilla Firefox.
- **Headless mode:** run the login browser invisibly in the background.
- **Incremental:** already-downloaded files are skipped.
- **Standalone application:** the release `.exe` needs no Python installation.

---

## How it works

1. **Login** is performed in a real browser (Selenium) so KFUPM's SAML Single
   Sign-On (`login.kfupm.edu.sa`, WSO2 Identity Server) works exactly as it does
   manually. Your credentials are entered into the SSO page; they are not sent
   anywhere else.
2. **Course data** is read with the Blackboard Learn REST API
   (`/learn/api/public/v1/...`) using the session cookies from that browser.
   This is much faster and more reliable than scraping Ultra's rendered pages.
3. **Files** are streamed to disk from the attachment endpoints, and `.url`
   shortcuts are written for external links.

---

## How to use the application (`.exe`)

1. Download the latest release and run `course_downloader.exe`.
   *(Windows may warn that the app is unsigned — click "More info" then "Run anyway".)*
2. Enter your KFUPM username and password.
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
User = 202012345
Password = your-password
```

Accepted key names (case-insensitive): `User`, `Username`, `BB_USERNAME`,
`KFUPM_USERNAME` and `Password`, `Pass`, `BB_PASSWORD`, `KFUPM_PASSWORD`.
Spaces around `=` and surrounding quotes are ignored. The app fills the fields
from `.env` on startup, and keeps those credentials out of `config.ini`.

> `.env` is listed in `.gitignore`, so it is never committed. Never share it.

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
        ├── Some Document.html
        ├── External Link.url
        ├── Assignment.url
        └── Announcements/
            └── 2026-01-15_Welcome.html
```

---

## Building a standalone `.exe`

```bash
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --icon icon.ico \
    --collect-all customtkinter \
    --name course_downloader course_downloader.py
```

---

## Notes and limitations

- **SSO:** If KFUPM ever enables multi-factor authentication, headless login
  cannot complete the second factor automatically. In that case uncheck
  **Headless Mode** and finish the prompt in the browser window.
- **Read-only:** the tool never modifies anything on Blackboard.
- **Assignments/tests:** student submissions are not downloaded (Blackboard does
  not expose them for this use case); the tool saves a link to the item and any
  instructor attachments.
- **Syllabus:** saved on a best-effort basis because Ultra tenants expose it
  differently.

---

## Disclaimer

This tool is provided for educational and personal use only. The user is solely
responsible for complying with all terms of service of King Fahd University of
Petroleum & Minerals (KFUPM) and Blackboard. Your KFUPM credentials are used
locally to log into Blackboard and are not stored or transmitted elsewhere.

## License

MIT — see [LICENSE](LICENSE).
