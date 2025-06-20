# KFUPM Blackboard Course Downloader

---
<h2 align="center">Downloads | التحميل</h2>

<div align="center">
  <a href="https://github.com/bibo242/Blackboard-course-downloader/releases/latest/download/course_downloaderv1.1.exe" title="Download Latest Release (.exe)">
    <img src="https://img.shields.io/badge/DOWNLOAD%20LATEST%20RELEASE%20(.EXE)-brightgreen?style=for-the-badge&logo=windows&logoColor=white" alt="Download Latest Release (.exe)">
  </a>
  <p align="center"><small><em>(Note: Windows might show a "Windows protected your PC" security warning. Click "More info" and then "Run anyway".)</em></small></p>
  <p align="center"><small><em>This happens because the app isn't yet code-signed (a process that verifies the publisher).</em></small></p>
  <p align="center"><small>View all versions on the <a href="https://github.com/bibo242/Blackboard-course-downloader/releases/latest">Releases Page</a>.</small></p>
</div>

---
<br>







The KFUPM Blackboard Course Downloader is a user-friendly desktop application designed to access your KFUPM Blackboard page and download entire course materials. It works by scraping the Blackboard website, saving you the tedious task of manually clicking and downloading every file.

This tool is perfect for backing up course materials, whether you want to revise from them later or archive them for future students to utilize. It preserves the original folder structure from Blackboard, ensuring everything is perfectly organized.

_Note: it will probably work for you if you're from another university that uses Blackboard but you have to change the URL in the code_

 
![image](https://github.com/user-attachments/assets/8bd239d3-d8fd-4224-addf-e9aaf624bc3c)
![Screenshot from 2025-06-18 15-34-27](https://github.com/user-attachments/assets/a8b1a615-a89e-4943-8b7d-3f867cc6eafd)


---

##  Features

- **Easy-to-Use GUI:** A simple graphical interface that requires no command-line knowledge.
- **Bulk Downloading:** Download all materials for one course, multiple courses, or even entire terms at once.
- **Preserves Structure:** Replicates the exact folder hierarchy from Blackboard on your computer.
- **Comprehensive Scraper:** Downloads all file types (PDF, PPT, DOCX, ZIP, etc.) and also saves external web links as `.url` shortcuts.
- **Browser Choice:** Supports both Google Chrome and Mozilla Firefox.
- **Headless Mode:** An option to run the browser invisibly in the background for a cleaner experience.
- **Standalone Application:** No need to install Python or any dependencies if you use the `.exe` file.

---

##  How to Use the Application (`.exe`)

This is the recommended method for most users. No installation is required!

1.  **Download the latest release.**
    - Go to the [**Releases Page**](https://github.com/bibo242/Blackboard-course-downloader/releases).
    - Under the latest version, download the `course_downloader.exe` file from the "Assets" section.

2.  **Run the application.**
    - Double-click the downloaded `course_downloader.exe` file to launch the program.
    - _(Note: Windows might show a "Windows protected your PC" security warning. Click "More info" and then "Run anyway".)_

3.  **Log in and Scan.**
    - Enter your KFUPM username and password.
    - Choose your preferred browser (Chrome or Firefox).
    - Click **"Scan Courses"**. The application will log in and find all your courses, displaying them in the listbox.

4.  **Download.**
    - Select the course(s) you want to download from the list.
    - Choose a download destination folder.
    - Click **"Download Selected Course(s)"** and watch the magic happen!

### System Requirements
- An active internet connection.
- [Google Chrome](https://www.google.com/chrome/) or [Mozilla Firefox](https://www.mozilla.org/firefox/) must be installed on your system.

---

##  How to Run from Source (`.py` file)

This method is for developers who want to run the Python script directly.

1.  **Clone the repository.**
    Open your terminal or Git Bash and run:
    ```bash
    git clone https://github.com/bibo242/Blackboard-course-downloader.git
    ```

2.  **Navigate to the project folder.**
    ```bash
    cd Blackboard-course-downloader
    ```

3.  **(Optional but Recommended) Create and activate a virtual environment.**
    ```bash
    # Create the environment
    python -m venv venv
    # Activate it (on Windows)
    .\venv\Scripts\activate
    ```

4.  **Install the required packages.**
    The `requirements.txt` file contains all the necessary libraries.
    ```bash
    pip install -r requirements.txt
    ```

5.  **Run the script.**
    ```bash
    python course_downloader.py
    ```
    The application GUI will launch, and you can proceed as described in the user guide above.

---

##  Disclaimer

This tool is provided for educational and personal use only. The user is solely responsible for complying with all terms of service of King Fahd University of Petroleum & Minerals (KFUPM) and Blackboard. Your KFUPM credentials are used locally to log into Blackboard and are not stored or transmitted elsewhere.
