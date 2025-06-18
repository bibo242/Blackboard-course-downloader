import os
import time
import getpass
import re
import requests
import shutil
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading

# --- Selenium Imports (for multi-browser support) ---
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException

# Firefox specific
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.firefox.options import Options as FirefoxOptions

# Chrome specific
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from webdriver_manager.chrome import ChromeDriverManager


# --- Constants and Mappings ---
BASE_URL = "https://blackboard.kfupm.edu.sa/"
MIME_TYPE_MAP = {
    'application/pdf': '.pdf', 'application/vnd.ms-powerpoint': '.ppt',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx',
    'application/msword': '.doc', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'application/vnd.ms-excel': '.xls', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
    'application/zip': '.zip', 'application/x-zip-compressed': '.zip', 'application/x-rar-compressed': '.rar',
    'application/x-7z-compressed': '.7z', 'application/x-tar': '.tar', 'video/mp4': '.mp4',
    'video/quicktime': '.mov', 'video/x-msvideo': '.avi', 'video/x-matroska': '.mkv', 'video/webm': '.webm',
    'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif', 'text/plain': '.txt',
    'application/x-ipynb+json': '.ipynb', 'application/octet-stream': ''
}

# --- Backend Web Scraping Logic ---

def setup_driver(browser_choice, status_callback, headless=True):
    """
    Sets up a Selenium WebDriver based on the user's explicit choice.
    """
    if browser_choice == "firefox":
        status_callback("Initializing Firefox driver...")
        options = FirefoxOptions()
        if headless:
            options.add_argument("-headless")
        try:
            service = FirefoxService()
            driver = webdriver.Firefox(service=service, options=options)
            status_callback("Firefox driver initialized successfully.")
            return driver
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Firefox. Is it installed? Error: {e}")

    elif browser_choice == "chrome":
        status_callback("Initializing Chrome driver...")
        options = ChromeOptions()
        if headless:
            options.add_argument("--headless=new")
        try:
            status_callback("  - Checking/installing chromedriver...")
            service = ChromeService(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            status_callback("Chrome driver initialized successfully.")
            return driver
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Chrome. Is it installed? Error: {e}")
            
    else:
        raise ValueError("Invalid browser choice specified.")


def login(driver, username, password):
    driver.get(BASE_URL)
    wait = WebDriverWait(driver, 20)
    user_field = wait.until(EC.presence_of_element_located((By.ID, "user_id")))
    pass_field = driver.find_element(By.ID, "password")
    login_button = driver.find_element(By.ID, "entry-login")
    user_field.send_keys(username)
    pass_field.send_keys(password)
    login_button.click()
    wait.until(EC.presence_of_element_located((By.ID, "module:_4_1")))
    return driver.get_cookies()

def get_all_terms_and_courses(driver, status_callback):
    status_callback("Scanning for all available terms and courses...")
    all_courses = []
    try:
        wait = WebDriverWait(driver, 20)
        term_headers = wait.until(EC.presence_of_all_elements_located((By.XPATH, "//h3[contains(@class, 'termHeading-coursefakeclass')]")))
        
        for term_header in term_headers:
            term_name = term_header.text.strip()
            if not term_name: continue
            status_callback(f"Found term: {term_name}")
            
            course_container = term_header.find_element(By.XPATH, "./following-sibling::div[1]")
            if not course_container.is_displayed():
                header_link = term_header.find_element(By.TAG_NAME, "a")
                driver.execute_script("arguments[0].click();", header_link)
                wait.until(EC.visibility_of(course_container))

            course_elements = course_container.find_elements(By.CSS_SELECTOR, "ul.courseListing li a")
            courses = [
                {"name": re.sub(r'[\\/*?:"<>|]', "", el.text.strip()), "url": el.get_attribute('href'), "term": term_name}
                for el in course_elements if el.text.strip()
            ]
            all_courses.extend(courses)
            status_callback(f"  - Found {len(courses)} courses.")
            
    except Exception as e:
        status_callback(f"Error scanning courses: {e}")
    return all_courses

def navigate_to_course_content(driver, status_callback):
    try:
        wait = WebDriverWait(driver, 15)
        content_link_xpath = "//a/span[contains(text(), 'Course Content')]"
        content_link = wait.until(EC.element_to_be_clickable((By.XPATH, content_link_xpath)))
        if "listContent.jsp" not in driver.current_url:
             content_link.click()
             status_callback("  - Navigated to Course Content area.")
    except Exception:
        status_callback("  - 'Course Content' link not found, scraping current page.")

def scrape_page_for_content(driver, content_map, status_callback, current_relative_path=""):
    """Recursively scrapes a page for all discoverable content."""
    wait = WebDriverWait(driver, 10)
    try:
        wait.until(EC.presence_of_element_located((By.ID, "content_listContainer")))
        content_list_items = driver.find_elements(By.CSS_SELECTOR, "ul#content_listContainer > li.liItem")
    except TimeoutException:
        return

    folders_to_visit = []
    
    for item in content_list_items:
        try:
            links = item.find_elements(By.XPATH, ".//a[@href] | .//video[@src] | .//img[@src]")
            item_title = item.find_element(By.CSS_SELECTOR, "div.item > h3").text.strip()
            
            for link in links:
                url = link.get_attribute("href") or link.get_attribute("src")
                if not url or "javascript:void(0)" in url: continue
                name = link.text.strip() or item_title
                if "/bbcswebdav/" in url:
                    content_map.append({"type": "File", "url": url, "name": name, "path": current_relative_path})
                elif url.startswith("http") and "blackboard.kfupm.edu.sa" not in url:
                    content_map.append({"type": "WebLink", "url": url, "name": name, "path": current_relative_path})
        except NoSuchElementException:
            pass
        
        try:
            folder_link = item.find_element(By.CSS_SELECTOR, "div.item > h3 > a[href*='/listContent.jsp?']")
            folders_to_visit.append({"name": folder_link.text.strip(), "url": folder_link.get_attribute("href")})
        except NoSuchElementException:
            continue

    for folder in folders_to_visit:
        clean_name = re.sub(r'[\\/*?:"<>|]', "", folder['name'])
        new_relative_path = os.path.join(current_relative_path, clean_name)
        status_callback(f"  > Scanning Folder: {folder['name']}")
        driver.get(folder['url'])
        scrape_page_for_content(driver, content_map, status_callback, new_relative_path)
        driver.back()
        time.sleep(1)

def process_content_list(session, course_dir, content_list, progress_callback, status_callback):
    if not content_list:
        status_callback("  - No downloadable files or links found for this course.")
        return
    unique_content = [dict(t) for t in {tuple(d.items()) for d in content_list}]
    status_callback(f"\n  [Processing {len(unique_content)} unique items]")
    
    for i, item_info in enumerate(unique_content):
        progress_callback((i + 1) / len(unique_content) * 100)
        item_type, original_name, relative_path, url = item_info['type'], item_info['name'], item_info['path'], item_info['url']
        final_folder_path = os.path.join(course_dir, relative_path)
        os.makedirs(final_folder_path, exist_ok=True)
        
        if item_type == "File":
            status_callback(f"    ({i+1}) Downloading File: {os.path.join(relative_path, original_name)}")
            try:
                with session.get(url, stream=True, timeout=180) as r:
                    r.raise_for_status()
                    server_fname = ""
                    if "content-disposition" in r.headers:
                        fname_match = re.findall("filename=\"?(.+?)\"?", r.headers['content-disposition'])
                        if fname_match: server_fname = fname_match[0]
                    if not server_fname: server_fname = os.path.basename(url.split('?')[0])
                    base_name_from_link, ext_from_link = os.path.splitext(original_name)
                    _, ext_from_server = os.path.splitext(server_fname)
                    final_name = re.sub(r'[\\/*?:"<>|]', "", base_name_from_link if base_name_from_link else original_name)
                    final_ext = ext_from_link or ext_from_server or MIME_TYPE_MAP.get(r.headers.get('content-type', '').split(';')[0], "")
                    final_filename = final_name + final_ext
                    final_filepath = os.path.join(final_folder_path, final_filename)
                    with open(final_filepath, 'wb') as f: shutil.copyfileobj(r.raw, f)
            except Exception as e:
                status_callback(f"      - FAILED: {e}")
        
        elif item_type == "WebLink":
            status_callback(f"    ({i+1}) Creating Link: {os.path.join(relative_path, original_name)}")
            clean_filename = re.sub(r'[\\/*?:"<>|]', "", original_name) + ".url"
            final_filepath = os.path.join(final_folder_path, clean_filename)
            with open(final_filepath, 'w', encoding='utf-8') as f: f.write(f"[InternetShortcut]\nURL={url}\n")


# --- GUI Application Class ---
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Blackboard Course Downloader")
        self.root.geometry("700x650") # Increased height slightly for new widget
        self.all_course_data = []

        main_frame = ttk.Frame(root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- UI Widgets setup ---
        row_idx = 0
        ttk.Label(main_frame, text="Username:").grid(row=row_idx, column=0, sticky="w", pady=2); row_idx += 1
        self.username_entry = ttk.Entry(main_frame, width=40)
        self.username_entry.grid(row=row_idx-1, column=1, sticky="ew")

        ttk.Label(main_frame, text="Password:").grid(row=row_idx, column=0, sticky="w", pady=2); row_idx += 1
        self.password_entry = ttk.Entry(main_frame, width=40, show="*")
        self.password_entry.grid(row=row_idx-1, column=1, sticky="ew")

        ttk.Label(main_frame, text="Download To:").grid(row=row_idx, column=0, sticky="w", pady=2); row_idx += 1
        self.path_var = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Desktop", "KFUPM_Downloads"))
        self.path_entry = ttk.Entry(main_frame, textvariable=self.path_var, width=40)
        self.path_entry.grid(row=row_idx-1, column=1, sticky="ew")
        self.browse_button = ttk.Button(main_frame, text="Browse...", command=self.browse_directory)
        self.browse_button.grid(row=row_idx-1, column=2, padx=5)

        # --- NEW: Browser Selection ---
        browser_frame = ttk.LabelFrame(main_frame, text="Browser Selection (choose whatever you have installed)")
        browser_frame.grid(row=row_idx, column=0, columnspan=3, sticky="ew", pady=5, padx=5); row_idx += 1
        self.browser_var = tk.StringVar(value="firefox")
        ttk.Radiobutton(browser_frame, text="Use Firefox", variable=self.browser_var, value="firefox").pack(side="left", padx=10, pady=5)
        ttk.Radiobutton(browser_frame, text="Use Chrome", variable=self.browser_var, value="chrome").pack(side="left", padx=10, pady=5)
        
        self.scan_button = ttk.Button(main_frame, text="Scan Courses", command=self.start_scan_thread)
        self.scan_button.grid(row=row_idx, column=1, pady=10); row_idx += 1

        ttk.Label(main_frame, text="Select Course(s) to Download (use Ctrl/Shift to select multiple):").grid(row=row_idx, column=0, columnspan=3, sticky="w", pady=5); row_idx += 1
        self.course_listbox = tk.Listbox(main_frame, selectmode=tk.MULTIPLE, height=8)
        self.course_listbox.grid(row=row_idx, column=0, columnspan=3, sticky="nsew")
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=self.course_listbox.yview)
        scrollbar.grid(row=row_idx, column=3, sticky="ns")
        self.course_listbox.config(yscrollcommand=scrollbar.set); row_idx += 1

        self.headless_var = tk.BooleanVar(value=True)
        self.headless_check = ttk.Checkbutton(main_frame, text="Run in Headless Mode (no browser window)", variable=self.headless_var)
        self.headless_check.grid(row=row_idx, column=0, columnspan=2, sticky="w", pady=5); row_idx += 1
        
        self.download_button = ttk.Button(main_frame, text="Download Selected Course(s)", command=self.start_download_thread, state="disabled")
        self.download_button.grid(row=row_idx, column=1, pady=10); row_idx += 1

        ttk.Label(main_frame, text="Status:").grid(row=row_idx, column=0, sticky="w", pady=5); row_idx += 1
        self.status_text = tk.Text(main_frame, height=8, state="disabled", wrap="word")
        self.status_text.grid(row=row_idx, column=0, columnspan=3, sticky="nsew"); row_idx += 1
        
        self.progress_bar = ttk.Progressbar(main_frame, orient="horizontal", mode="determinate")
        self.progress_bar.grid(row=row_idx, column=0, columnspan=3, sticky="ew", pady=10)

        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(row_idx-3, weight=1) # course listbox
        main_frame.rowconfigure(row_idx-1, weight=1) # status text

    def browse_directory(self):
        directory = filedialog.askdirectory()
        if directory: self.path_var.set(directory)

    def update_status(self, message):
        self.status_text.config(state="normal"); self.status_text.insert(tk.END, message + "\n"); self.status_text.see(tk.END); self.status_text.config(state="disabled"); self.root.update_idletasks()
        
    def update_progress(self, value):
        self.progress_bar['value'] = value; self.root.update_idletasks()

    def start_scan_thread(self):
        self.scan_button.config(state="disabled"); self.download_button.config(state="disabled")
        self.course_listbox.delete(0, tk.END)
        self.status_text.config(state="normal"); self.status_text.delete(1.0, tk.END); self.status_text.config(state="disabled")
        threading.Thread(target=self.scan_courses_task, daemon=True).start()

    def scan_courses_task(self):
        username = self.username_entry.get(); password = self.password_entry.get()
        if not username or not password:
            messagebox.showerror("Error", "Username and Password are required.")
            self.scan_button.config(state="normal"); return
            
        driver = None
        try:
            browser_choice = self.browser_var.get()
            driver = setup_driver(browser_choice, self.update_status, self.headless_var.get())
            
            self.update_status("Logging in..."); login(driver, username, password)
            self.all_course_data = get_all_terms_and_courses(driver, self.update_status)
            self.update_status("Scan complete.")
            
            if self.all_course_data:
                self.all_course_data.sort(key=lambda x: x['term'], reverse=True)
                current_term = ""
                for course in self.all_course_data:
                    if course["term"] != current_term:
                        current_term = course["term"]
                        self.course_listbox.insert(tk.END, f"--- {current_term} ---")
                    self.course_listbox.insert(tk.END, f"  {course['name']}")
                self.download_button.config(state="normal")
            else:
                self.update_status("No terms or courses found.")
        except Exception as e:
            self.update_status(f"An error occurred during scan: {e}")
            import traceback; self.update_status(traceback.format_exc())
        finally:
            if driver: driver.quit()
            self.scan_button.config(state="normal")
            
    def start_download_thread(self):
        self.download_button.config(state="disabled"); self.scan_button.config(state="disabled")
        threading.Thread(target=self.download_courses_task, daemon=True).start()

    def download_courses_task(self):
        username = self.username_entry.get(); password = self.password_entry.get()
        selected_indices = self.course_listbox.curselection()
        if not selected_indices:
            messagebox.showerror("Error", "Please select at least one course.")
            self.download_button.config(state="normal"); self.scan_button.config(state="normal"); return

        courses_to_download = []
        for i in selected_indices:
            item_text = self.course_listbox.get(i).strip()
            if item_text.startswith("---"): continue
            for course in self.all_course_data:
                if course['name'] == item_text:
                    courses_to_download.append(course); break
        
        self.update_status(f"Starting download for {len(courses_to_download)} selected course(s)...")
        driver = None
        try:
            browser_choice = self.browser_var.get()
            driver = setup_driver(browser_choice, self.update_status, self.headless_var.get())
            login_cookies = login(driver, username, password)
            
            session = requests.Session()
            for cookie in login_cookies: session.cookies.set(cookie['name'], cookie['value'])

            for course in courses_to_download:
                self.update_progress(0)
                term_dir = os.path.join(self.path_var.get(), course['term'])
                course_dir = os.path.join(term_dir, course['name'])
                os.makedirs(course_dir, exist_ok=True)
                
                self.update_status(f"\n--- Processing course: {course['name']} ---")
                driver.get(course['url'])
                navigate_to_course_content(driver, self.update_status)
                
                self.update_status("  [Phase A: Scanning all folders for file links...]")
                content_map = []
                scrape_page_for_content(driver, content_map, self.update_status)
                
                process_content_list(session, course_dir, content_map, self.update_progress, self.update_status)

            self.update_status("\nAll selected courses downloaded successfully!")
        except Exception as e:
            self.update_status(f"A critical error occurred: {e}")
            import traceback; self.update_status(traceback.format_exc())
        finally:
            if driver: driver.quit()
            self.download_button.config(state="normal"); self.scan_button.config(state="normal")
            self.update_progress(0)

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()