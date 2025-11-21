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
# Define the sections to scrape within each course
TARGET_COURSE_SECTIONS = ["Course Content", "Course Syllabus", "Assignments", "Assessments / Tests"]

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
            # Attempt to use geckodriver from PATH first
            try:
                service = FirefoxService() # Assumes geckodriver is in PATH or managed
                driver = webdriver.Firefox(service=service, options=options)
            except Exception: 
                status_callback("  - Geckodriver via Service failed, trying direct webdriver.Firefox(). Ensure geckodriver is in PATH.")
                driver = webdriver.Firefox(options=options) # Fallback
            status_callback("Firefox driver initialized successfully.")
            return driver
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Firefox. Is it installed and geckodriver in PATH? Error: {e}")

    elif browser_choice == "chrome":
        status_callback("Initializing Chrome driver...")
        options = ChromeOptions()
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu") 
        options.add_argument("--no-sandbox") 
        options.add_argument("--disable-dev-shm-usage") 
        try:
            status_callback("  - Checking/installing chromedriver via webdriver_manager...")
            service = ChromeService(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            status_callback("Chrome driver initialized successfully.")
            return driver
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Chrome. Is it installed? webdriver_manager might have issues. Error: {e}")
            
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
    wait.until(EC.presence_of_element_located((By.ID, "module:_4_1"))) # Courses module
    return driver.get_cookies()


# --- Reverted to user's original get_all_terms_and_courses logic ---
# Minimal changes: added sanitization for term_name in the dict for consistency.
def get_all_terms_and_courses(driver, status_callback):
    status_callback("Scanning for all available terms and courses...")
    all_courses = []
    try:
        wait = WebDriverWait(driver, 20)
        # Using the XPath from your original code
        term_headers = wait.until(EC.presence_of_all_elements_located((By.XPATH, "//h3[contains(@class, 'termHeading-coursefakeclass')]")))
        
        if not term_headers:
            status_callback("No term headers found with class 'termHeading-coursefakeclass'. Page structure might have changed or no courses available.")
            return []

        for term_header in term_headers:
            term_name_raw = term_header.text.strip()
            if not term_name_raw: 
                status_callback("Found a term header with no text, skipping.")
                continue
            
            # Sanitize term name for use in paths later
            term_name_clean = re.sub(r'[\\/*?:"<>|]', "_", term_name_raw)
            status_callback(f"Found term: {term_name_clean} (Raw: {term_name_raw})")
            
            try:
                # Using the XPath for course container from your original code
                course_container = term_header.find_element(By.XPATH, "./following-sibling::div[1]")
            except NoSuchElementException:
                status_callback(f"  - Could not find course container (div sibling) for term: {term_name_clean}. Skipping this term's courses.")
                continue # Skip to the next term_header if its course container div is not found

            if not course_container.is_displayed():
                try:
                    # Using the click logic from your original code
                    header_link = term_header.find_element(By.TAG_NAME, "a") # Assumes H3 has an A for expansion
                    driver.execute_script("arguments[0].click();", header_link)
                    wait.until(EC.visibility_of(course_container))
                    status_callback(f"  - Expanded term: {term_name_clean}")
                except Exception as e_click:
                    status_callback(f"  - Could not expand term {term_name_clean}: {e_click}. Courses might be hidden.")
                    # Continue processing, maybe courses are already visible or another issue

            # Using the CSS selector for course elements from your original code
            course_elements = course_container.find_elements(By.CSS_SELECTOR, "ul.courseListing li > a:not(.courseDataBlock a)")

            
            courses_in_term = []
            for el in course_elements:
                el_text = el.text.strip()
                el_url = el.get_attribute('href')
                if el_text and el_url:
                    courses_in_term.append({
                        "name": re.sub(r'[\\/*?:"<>|]', "_", el_text), # Sanitize course name
                        "url": el_url, 
                        "term": term_name_clean # Use cleaned term name
                    })
            
            if courses_in_term:
                all_courses.extend(courses_in_term)
                status_callback(f"  - Found {len(courses_in_term)} courses in term '{term_name_clean}'.")
            else:
                status_callback(f"  - No course links found within the container for term '{term_name_clean}'.")
            
    except TimeoutException:
        status_callback("Timed out waiting for term headers. The page might not have loaded correctly or no terms are visible.")
    except Exception as e:
        status_callback(f"An error occurred while scanning for terms and courses: {e}")
        import traceback
        status_callback(traceback.format_exc())
    
    if not all_courses:
        status_callback("Scan finished. No courses were found across any terms based on the expected structure.")
    return all_courses


def scrape_page_for_content(driver, content_map, status_callback, current_relative_path=""):
    wait = WebDriverWait(driver, 10)
    try:
        content_list_container = wait.until(EC.presence_of_element_located((By.ID, "content_listContainer")))
        # Find all top-level list items on the current page
        content_list_items = content_list_container.find_elements(By.CSS_SELECTOR, "li.liItem[id^='contentListItem:']")
    except TimeoutException:
        status_callback(f"  - No 'content_listContainer' or 'liItem' found on current page ({driver.current_url}). Might be empty or structured differently in '{current_relative_path}'.")
        return

    folders_to_visit_recursively = [] # Stores info about BB Folders to scan after processing current page items

    for item_idx, li_element in enumerate(content_list_items):
        item_title_str = f"Untitled Item {item_idx+1}"
        try:
            # Prefer title from H3 inside div.item or div.itemHead
            title_h3_element = li_element.find_element(By.CSS_SELECTOR, "div.item > h3, div.item > div.itemHead > h3")
            item_title_str = title_h3_element.text.strip()
        except NoSuchElementException:
            try: # Fallback: try any link text within the item if h3 not found or empty
                any_link_in_item = li_element.find_element(By.CSS_SELECTOR, "a")
                if any_link_in_item.text.strip():
                    item_title_str = any_link_in_item.text.strip()
            except NoSuchElementException:
                status_callback(f"    - Could not determine title for an item in '{current_relative_path}'. Using default name.")

        # Sanitize title for use as a folder or file name component
        clean_item_title_as_path_segment = re.sub(r'[\\/*?:"<>|]', "_", item_title_str) if item_title_str else f"untitled_item_{item_idx}"

        # --- Stage 1: Check if the li_element represents a Blackboard Folder ---
        # These folders navigate to another listContent.jsp page.
        try:
            # Look for a folder link specifically within the item's main title area (e.g., inside H3's <a>)
            folder_link_tag = li_element.find_element(By.XPATH, ".//div[contains(@class,'item')]//h3//a[contains(@href, '/listContent.jsp?')]")
            folder_url_value = folder_link_tag.get_attribute("href")
            if folder_url_value:
                folders_to_visit_recursively.append({
                    "name": clean_item_title_as_path_segment, # This will be the subfolder name for recursion
                    "url": folder_url_value
                })
                status_callback(f"    Identified BB Folder: '{item_title_str}'. Will be scanned recursively into subfolder '{clean_item_title_as_path_segment}'.")
                continue # This li_element is a folder; move to the next li_element in content_list_items
        except NoSuchElementException:
            # This li_element is not a standard Blackboard Folder based on its main H3 link.
            pass

        # --- Stage 2: Check if the li_element has an "Attached Files" section ---
        # (e.g., an Assignment item with multiple attached PDFs like your "Assignment 3" example)
        # These attachments should go into a subfolder named after the li_element's title.
        attachments_were_processed_for_this_item = False
        try:
            # Standard Blackboard structure for attachments (based on your HTML example)
            attachments_ul_container = li_element.find_element(By.XPATH, ".//div[contains(@class, 'details')]//ul[contains(@class, 'attachments')]")
            attachment_links_in_ul = attachments_ul_container.find_elements(By.XPATH, ".//a[@href]")

            if attachment_links_in_ul:
                attachments_were_processed_for_this_item = True
                # Create a subfolder path using the item's title for its attachments
                path_for_these_item_attachments = os.path.join(current_relative_path, clean_item_title_as_path_segment)
                status_callback(f"    Item '{item_title_str}' has an 'Attachments' section. Files will be saved in subfolder: '{path_for_these_item_attachments}'")

                for attachment_link_tag in attachment_links_in_ul:
                    attachment_url = attachment_link_tag.get_attribute("href")
                    if not attachment_url or "javascript:void(0)" in attachment_url or attachment_url.strip() == "#":
                        continue

                    # Use the attachment's own link text as its name
                    attachment_name_raw = attachment_link_tag.text.strip()
                    # Sanitize attachment filename (though process_content_list does more thorough cleaning later)
                    attachment_filename_candidate = attachment_name_raw if attachment_name_raw else os.path.basename(attachment_url.split('?')[0])
                    clean_attachment_filename = re.sub(r'[\\/*?:"<>|]', "_", attachment_filename_candidate)

                    if "/bbcswebdav/" in attachment_url:
                        content_map.append({
                            "type": "File", "url": attachment_url,
                            "name": clean_attachment_filename, "path": path_for_these_item_attachments
                        })
                        status_callback(f"      Found Attached File: '{clean_attachment_filename}' for item '{item_title_str}'.")
                    elif attachment_url.startswith("http") and BASE_URL.split('/')[2] not in attachment_url: # External web link
                        content_map.append({
                            "type": "WebLink", "url": attachment_url,
                            "name": clean_attachment_filename, "path": path_for_these_item_attachments
                        })
                        status_callback(f"      Found Attached WebLink: '{clean_attachment_filename}' for item '{item_title_str}'.")

                # If an item has an "Attachments" section, assume its main link (e.g., to uploadAssignment page) is not a downloadable file itself.
                continue # Move to the next li_element in content_list_items
        except NoSuchElementException:
            # No "div.details ul.attachments" structure found, or no links within it.
            pass # Proceed to check for general/direct links if this item wasn't an attachment container

        # --- Stage 3: If not a BB Folder and no "Attached Files" section processed, handle as a general content item ---
        # (e.g., a direct link to a single PDF, a Web Link item, embedded media not in an attachments section)
        # These items are placed directly in the current_relative_path (i.e., not in a new subfolder named after themselves).
        try:
            # Find all relevant links/media sources directly under the li_element's scope.
            # Exclude javascript links, empty hrefs, and already identified folder links.
            general_content_elements = li_element.find_elements(By.XPATH,
                ".//a[@href[ (contains(.,'/bbcswebdav/')) or " +
                "(starts-with(.,'http') and not(starts-with(.,'javascript:')) and .!='#') ] and not(contains(@href, '/listContent.jsp?')) ] | " +
                ".//video[@src[starts-with(.,'http') or contains(.,'/bbcswebdav/')]] | " +
                ".//img[@src[starts-with(.,'http') or contains(.,'/bbcswebdav/')]]"
            )

            if not general_content_elements and not attachments_were_processed_for_this_item: # and not a folder (already continued)
                 status_callback(f"    - Item '{item_title_str}' is not a folder, has no 'Attachments' section, and no direct file/web/media links found by general XPath. Skipping this item's direct content.")

            for content_element_tag in general_content_elements:
                url_value = content_element_tag.get_attribute("href") or content_element_tag.get_attribute("src")
                if not url_value: continue

                name_candidate = clean_item_title_as_path_segment # Default to item's title
                if content_element_tag.tag_name == 'a':
                    link_text_raw = content_element_tag.text.strip()
                    # Use specific link text if it's not generic and better than the item title
                    if link_text_raw and link_text_raw.lower() not in ["view", "open", "download", "link", "attachment", item_title_str.lower()]:
                        name_candidate = re.sub(r'[\\/*?:"<>|]', "_", link_text_raw) # Clean link text
                
                # If multiple general links under one li_element default to item title, try to use basename for uniqueness
                if general_content_elements.index(content_element_tag) > 0 and name_candidate == clean_item_title_as_path_segment:
                     basename_from_url = os.path.basename(url_value.split('?')[0])
                     if basename_from_url : name_candidate = re.sub(r'[\\/*?:"<>|]', "_", basename_from_url)


                if "/bbcswebdav/" in url_value or content_element_tag.tag_name in ['video', 'img']:
                    content_map.append({
                        "type": "File", "url": url_value,
                        "name": name_candidate, "path": current_relative_path # Saved directly in current_relative_path
                    })
                    status_callback(f"      Found General File/Media: '{name_candidate}' (from item '{item_title_str}') in '{current_relative_path or 'section root'}'")
                elif url_value.startswith("http"): # External web link
                    content_map.append({
                        "type": "WebLink", "url": url_value,
                        "name": name_candidate, "path": current_relative_path # Saved directly in current_relative_path
                    })
                    status_callback(f"      Found General WebLink: '{name_candidate}' (from item '{item_title_str}') in '{current_relative_path or 'section root'}'")
        except NoSuchElementException:
            pass # No general content elements found for this item.
        # End of processing one li_element from content_list_items
    
    # --- After iterating all li_elements on the current page, recursively visit collected BB Folders ---
    for folder_to_scan_info in folders_to_visit_recursively:
        folder_name_as_path_segment = folder_to_scan_info['name'] # This is the cleaned title of the folder item
        folder_target_url = folder_to_scan_info['url']
        
        # The new relative path for content inside this folder will be current_relative_path joined with folder_name_as_path_segment
        new_recursive_path_for_folder_content = os.path.join(current_relative_path, folder_name_as_path_segment)
        
        status_callback(f"    > Navigating into Sub-Folder: '{folder_name_as_path_segment}' (URL: {folder_target_url})")
        status_callback(f"      Content from this folder will be saved under relative path: '{new_recursive_path_for_folder_content}'")
        
        try:
            driver.get(folder_target_url)
            # Recursive call to scrape the content of this sub-folder
            scrape_page_for_content(driver, content_map, status_callback, new_recursive_path_for_folder_content)
            
            status_callback(f"    < Navigating back from sub-folder: '{folder_name_as_path_segment}'")
            driver.back() # Go back to the page that listed this folder
            # Wait for the parent page's content list to be present again before proceeding with other folders on this level
            WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.ID, "content_listContainer")))
            time.sleep(0.5) # Small pause for stability and to ensure page state is updated
        except Exception as e_folder_navigation:
            status_callback(f"      ! ERROR during navigation or scraping of folder '{folder_name_as_path_segment}': {e_folder_navigation}")
            status_callback(f"      ! Current URL: {driver.current_url}. Attempting to recover by navigating back if possible.")
            try:
                # If the error occurred before driver.back(), or if driver.back() itself failed,
                # and we are still on the folder's page, try to go back.
                if driver.current_url == folder_target_url or driver.current_url.startswith(folder_target_url.split('?')[0]):
                    driver.back()
                    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "content_listContainer")))
                status_callback(f"      ! Recovery: Navigated back to {driver.current_url}")
            except Exception as e_recovery:
                status_callback(f"      ! Recovery attempt (driver.back) also failed for folder '{folder_name_as_path_segment}': {e_recovery}. May miss subsequent items on this level.")
            # Continue with the next folder on the current level if any.


def process_content_list(session, base_course_dir, content_list, progress_callback, status_callback):
    if not content_list:
        status_callback("      - No new downloadable files or links found in this section/folder.")
        return
        
    unique_content_by_url = {}
    for item in content_list:
        if item['url'] not in unique_content_by_url:
            unique_content_by_url[item['url']] = item
    unique_content = list(unique_content_by_url.values())

    status_callback(f"\n      [Processing {len(unique_content)} unique items for download/linking from this section/folder]")
    
    for i, item_info in enumerate(unique_content):
        if progress_callback: 
            progress_callback((i + 1) / len(unique_content) * 100)
        
        item_type = item_info.get('type', 'Unknown')
        original_name = item_info.get('name', 'untitled')
        relative_path_within_section = item_info.get('path', '') 
        url = item_info.get('url')

        if not url:
            status_callback(f"      ({i+1}) Skipping item with no URL: {original_name}")
            continue

        final_folder_path = os.path.join(base_course_dir, relative_path_within_section)
        os.makedirs(final_folder_path, exist_ok=True)
        
        # --- MODIFICATION FOR FILENAME AND EXTENSION ---
        base_name_candidate = original_name
        ext_candidate = ""

        # If it's a file, try to split extension more traditionally
        if item_type == "File":
            potential_base, potential_ext = os.path.splitext(original_name)
            # Check if the potential_ext is a known or common-looking extension
            if potential_ext and len(potential_ext) > 1 and len(potential_ext) <= 5 and potential_ext[1:].isalnum():
                base_name_candidate = potential_base
                ext_candidate = potential_ext
            # else, keep original_name as base_name_candidate, ext_candidate remains ""
        # For WebLinks, or files where splitext gave an unusual "extension", 
        # treat the whole original_name as the base for cleaning.
        # The .url extension will be added specifically for WebLinks later.

        # Clean the base name candidate (this will be used for both File base and WebLink base)
        # Allow dots within the name initially, they will be handled during final filename construction.
        clean_base_name = re.sub(r'[^\w\s\-\.]', '_', base_name_candidate).strip()
        clean_base_name = re.sub(r'\s+', ' ', clean_base_name) # Consolidate multiple spaces
        if not clean_base_name: 
            clean_base_name = "untitled_item" 
        # --- END OF MODIFICATION ---


        if item_type == "File":
            status_callback(f"        ({i+1}/{len(unique_content)}) Downloading File: {os.path.join(relative_path_within_section, original_name)}")
            try:
                with session.get(url, stream=True, timeout=300, allow_redirects=True) as r: 
                    r.raise_for_status() 
                    server_fname_raw = ""
                    if "content-disposition" in r.headers:
                        fname_match = re.findall(r'filename\*?=(?:UTF-\d{1,2}\'\')?([^";\n]+)', r.headers['content-disposition'], re.IGNORECASE)
                        if fname_match: server_fname_raw = requests.utils.unquote(fname_match[0].strip('"\' '))
                    if not server_fname_raw: server_fname_raw = os.path.basename(url.split('?')[0])

                    # Get extension from server filename if possible, or from original 'ext_candidate'
                    _, ext_from_server = os.path.splitext(server_fname_raw)
                    
                    # Prioritize: 1. ext_candidate (if item_type was File and splitext was good)
                    #             2. ext_from_server
                    #             3. MIME type map
                    final_ext = ext_candidate or ext_from_server or MIME_TYPE_MAP.get(r.headers.get('content-type', '').split(';')[0].lower(), "")
                    
                    if final_ext and not final_ext.startswith('.'): 
                        final_ext = '.' + final_ext
                    
                    # Now, clean_base_name should not have the extension part if final_ext is determined
                    # If clean_base_name ends with what we think is the final_ext, remove it to avoid duplication.
                    temp_clean_base = clean_base_name
                    if final_ext and temp_clean_base.lower().endswith(final_ext.lower()):
                        temp_clean_base = temp_clean_base[:-len(final_ext)]
                    
                    # Final sanitization for filesystem (remove any remaining problematic chars from base)
                    # and ensure no dots are left in this base part that could be misinterpreted as extension sep.
                    final_base_for_file = re.sub(r'[\\/*?:"<>|.]', "_", temp_clean_base) # Replace dots in base with underscore
                    final_base_for_file = re.sub(r'_+', '_', final_base_for_file).strip('_') # Consolidate underscores

                    final_filename_to_save = final_base_for_file + final_ext
                    final_filename_to_save = final_filename_to_save[:200] # Limit overall length
                    
                    if not final_base_for_file: # if base became empty after stripping underscores
                        final_filename_to_save = "downloaded_file" + final_ext


                    final_filepath = os.path.join(final_folder_path, final_filename_to_save)
                    
                    if os.path.exists(final_filepath):
                        try:
                            content_length = int(r.headers.get('content-length', 0))
                            if content_length > 0 and os.path.getsize(final_filepath) == content_length:
                                status_callback(f"          - SKIPPED (already exists with same size): {final_filename_to_save}")
                                continue
                        except Exception: pass

                    with open(final_filepath, 'wb') as f: shutil.copyfileobj(r.raw, f)
                    status_callback(f"          - SAVED: {final_filename_to_save}")

            except requests.exceptions.RequestException as e_req: status_callback(f"          - FAILED (Request Error): {original_name} - {e_req}")
            except IOError as e_io: status_callback(f"          - FAILED (File IO Error): {original_name} - {e_io}")
            except Exception as e: status_callback(f"          - FAILED (General Error): {original_name} - {e}")
        
        elif item_type == "WebLink":
            status_callback(f"        ({i+1}/{len(unique_content)}) Creating Link: {os.path.join(relative_path_within_section, original_name)}")
            
            # For WebLinks, 'clean_base_name' (derived from original_name) is what we want.
            # Remove any characters that are invalid for filenames, including dots that aren't part of the final .url extension.
            base_for_weblink = re.sub(r'[\\/*?:"<>|.]', "_", clean_base_name) # Replace dots with underscore
            base_for_weblink = re.sub(r'_+', '_', base_for_weblink).strip('_') # Consolidate underscores
            
            if not base_for_weblink: base_for_weblink = "weblink_shortcut"

            clean_link_filename = base_for_weblink[:195] + ".url" 
            final_filepath = os.path.join(final_folder_path, clean_link_filename)
            try:
                with open(final_filepath, 'w', encoding='utf-8') as f: f.write(f"[InternetShortcut]\nURL={url}\n")
                status_callback(f"          - LINK CREATED: {clean_link_filename}")
            except Exception as e: status_callback(f"          - FAILED creating link: {clean_link_filename} - {e}")
        else:
            status_callback(f"        ({i+1}/{len(unique_content)}) Skipping item of type '{item_type}': {original_name}")


# --- GUI Application Class (largely unchanged from your previous version with my UI tweaks) ---
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Blackboard Course Downloader")
        self.root.geometry("750x700") 
        self.all_course_data = []

        main_frame = ttk.Frame(root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- UI Widgets setup ---
        row_idx = 0 # Keeps track of the current grid row

        # Username
        ttk.Label(main_frame, text="Username:").grid(row=row_idx, column=0, sticky="w", pady=2)
        self.username_entry = ttk.Entry(main_frame, width=40)
        self.username_entry.grid(row=row_idx, column=1, columnspan=2, sticky="ew", pady=2) # columnspan 2 to align with browse button
        row_idx += 1

        # Password
        ttk.Label(main_frame, text="Password:").grid(row=row_idx, column=0, sticky="w", pady=2)
        self.password_entry = ttk.Entry(main_frame, width=40, show="*")
        self.password_entry.grid(row=row_idx, column=1, columnspan=2, sticky="ew", pady=2)
        row_idx += 1

        # Download To
        ttk.Label(main_frame, text="Download To:").grid(row=row_idx, column=0, sticky="w", pady=2)
        self.path_var = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Desktop", "KFUPM_Blackboard_Downloads"))
        self.path_entry = ttk.Entry(main_frame, textvariable=self.path_var, width=40)
        self.path_entry.grid(row=row_idx, column=1, sticky="ew", pady=2)
        self.browse_button = ttk.Button(main_frame, text="Browse...", command=self.browse_directory)
        self.browse_button.grid(row=row_idx, column=2, padx=(5,0), pady=2, sticky="ew") # padx right 0
        row_idx += 1

        # Browser Selection Frame
        browser_frame = ttk.LabelFrame(main_frame, text="Browser for Automation (must be installed)")
        browser_frame.grid(row=row_idx, column=0, columnspan=3, sticky="ew", pady=(10,5), padx=2)
        self.browser_var = tk.StringVar(value="firefox") 
        self.firefox_rb = ttk.Radiobutton(browser_frame, text="Use Firefox", variable=self.browser_var, value="firefox")
        self.firefox_rb.pack(side="left", padx=10, pady=5)
        self.chrome_rb = ttk.Radiobutton(browser_frame, text="Use Chrome", variable=self.browser_var, value="chrome")
        self.chrome_rb.pack(side="left", padx=10, pady=5)
        row_idx += 1
        
        # Headless Mode Checkbox
        self.headless_var = tk.BooleanVar(value=True)
        self.headless_check = ttk.Checkbutton(main_frame, text="Run in Headless Mode (no browser window visible - recommended)", variable=self.headless_var)
        self.headless_check.grid(row=row_idx, column=0, columnspan=3, sticky="w", pady=5)
        row_idx += 1

        # Scan Courses Button
        self.scan_button = ttk.Button(main_frame, text="1. Scan Courses", command=self.start_scan_thread)
        self.scan_button.grid(row=row_idx, column=0, columnspan=3, pady=10, sticky="ew")
        row_idx += 1

        # Course Selection Label
        ttk.Label(main_frame, text="Select Course(s) to Download (Ctrl/Shift to select multiple):").grid(row=row_idx, column=0, columnspan=3, sticky="w", pady=(10,2))
        row_idx += 1
        
        # Course Listbox and Scrollbar
        listbox_frame = ttk.Frame(main_frame) # Frame to hold listbox and scrollbar
        listbox_frame.grid(row=row_idx, column=0, columnspan=3, sticky="nsew")
        listbox_frame.columnconfigure(0, weight=1)
        listbox_frame.rowconfigure(0, weight=1)

        self.course_listbox = tk.Listbox(listbox_frame, selectmode=tk.EXTENDED, height=10) 
        self.course_listbox.grid(row=0, column=0, sticky="nsew")
        
        scrollbar = ttk.Scrollbar(listbox_frame, orient="vertical", command=self.course_listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.course_listbox.config(yscrollcommand=scrollbar.set)
        row_idx += 1

        # Download Button
        self.download_button = ttk.Button(main_frame, text="2. Download Selected Course(s)", command=self.start_download_thread, state="disabled")
        self.download_button.grid(row=row_idx, column=0, columnspan=3, pady=10, sticky="ew")
        row_idx += 1

        # Status & Logs Frame
        status_frame = ttk.LabelFrame(main_frame, text="Status & Logs")
        status_frame.grid(row=row_idx, column=0, columnspan=3, sticky="nsew", pady=(10,0))
        status_frame.columnconfigure(0, weight=1) # Text widget expands
        status_frame.rowconfigure(0, weight=1) # Text widget expands

        self.status_text = tk.Text(status_frame, height=10, state="disabled", wrap="word", relief=tk.SUNKEN, borderwidth=1)
        self.status_text.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        status_scrollbar = ttk.Scrollbar(status_frame, orient="vertical", command=self.status_text.yview)
        status_scrollbar.grid(row=0, column=1, sticky="ns", padx=5, pady=(5,0)) # Adjusted pady
        self.status_text.config(yscrollcommand=status_scrollbar.set)
        row_idx +=1 

        # Progress Bar
        self.progress_bar = ttk.Progressbar(main_frame, orient="horizontal", mode="determinate")
        self.progress_bar.grid(row=row_idx, column=0, columnspan=3, sticky="ew", pady=(5,10))
        row_idx += 1


        # --- Column and Row Configurations for main_frame ---
        main_frame.columnconfigure(1, weight=1) # Allow entry fields (column 1) to expand horizontally

        # Identify which rows should expand vertically.
        # It's usually listboxes and text areas.
        # The row index for course_listbox_frame is `row_idx - 4` (if current row_idx is correct after all placements)
        # The row index for status_frame is `row_idx - 2`
        
        # Let's count from the top for clarity:
        # 0: Username
        # 1: Password
        # 2: Download To
        # 3: Browser Frame
        # 4: Headless Check
        # 5: Scan Button
        # 6: Course Selection Label
        # 7: Listbox Frame (THIS IS THE ONE THAT NEEDS TO EXPAND)
        # 8: Download Button
        # 9: Status Frame (THIS IS THE OTHER ONE THAT NEEDS TO EXPAND)
        # 10: Progress Bar

        main_frame.rowconfigure(7, weight=1)  # Row containing the course_listbox_frame
        main_frame.rowconfigure(9, weight=1)  # Row containing the status_frame
        
        # Load saved settings (credentials, path, etc.)
        self.load_credentials()
        self.username_entry.bind("<KeyRelease>", lambda e: self.save_credentials_throttled())
        self.password_entry.bind("<KeyRelease>", lambda e: self.save_credentials_throttled())
        self.path_entry.bind("<KeyRelease>", lambda e: self.save_credentials_throttled())
        self._save_timer = None

    def save_credentials_throttled(self):
        if self._save_timer: self.root.after_cancel(self._save_timer)
        self._save_timer = self.root.after(1000, self.save_credentials) 

    def save_credentials(self):
        try:
            config_dir = os.path.join(os.path.expanduser("~"), ".kfupm_bb_downloader")
            os.makedirs(config_dir, exist_ok=True)
            config_file = os.path.join(config_dir, "config.ini")
            with open(config_file, "w") as f:
                f.write(f"username={self.username_entry.get()}\n")
                # Storing password in plain text - UNSAFE, for local convenience only.
                # Consider using 'keyring' for more secure storage.
                f.write(f"password={self.password_entry.get()}\n") 
                f.write(f"download_path={self.path_var.get()}\n")
                f.write(f"browser_choice={self.browser_var.get()}\n")
                f.write(f"headless_mode={self.headless_var.get()}\n")
        except Exception as e:
            self.update_status(f"Warning: Could not save settings: {e}")

    def load_credentials(self):
        try:
            config_file = os.path.join(os.path.expanduser("~"), ".kfupm_bb_downloader", "config.ini")
            if os.path.exists(config_file):
                with open(config_file, "r") as f:
                    for line in f:
                        name, value = line.strip().split("=", 1)
                        if name == "username": self.username_entry.insert(0, value)
                        elif name == "password": self.password_entry.insert(0, value)
                        elif name == "download_path": self.path_var.set(value)
                        elif name == "browser_choice": self.browser_var.set(value)
                        elif name == "headless_mode": self.headless_var.set(value.lower() == 'true')
        except Exception as e:
            self.update_status(f"Warning: Could not load saved settings: {e}")

    def browse_directory(self):
        directory = filedialog.askdirectory(initialdir=self.path_var.get())
        if directory: 
            self.path_var.set(directory)
            self.save_credentials() 

    def update_status(self, message):
        if self.root and self.status_text: 
            self.root.after(0, self._update_status_thread_safe, message)
    
    def _update_status_thread_safe(self, message):
        try:
            self.status_text.config(state="normal")
            self.status_text.insert(tk.END, message + "\n")
            self.status_text.see(tk.END)
            self.status_text.config(state="disabled")
        except tk.TclError: pass # Handle if widget is destroyed

    def update_progress(self, value):
        if self.root and self.progress_bar:
             self.root.after(0, self._update_progress_thread_safe, value)

    def _update_progress_thread_safe(self, value):
        try:
            self.progress_bar['value'] = value
        except tk.TclError: pass

    def set_ui_state(self, enabled):
        state = "normal" if enabled else "disabled"
        widgets_to_toggle = [
            self.username_entry, self.password_entry, self.path_entry,
            self.browse_button, self.scan_button, self.headless_check,
            self.firefox_rb, self.chrome_rb
        ]
        for widget in widgets_to_toggle:
            if widget: widget.config(state=state)
        
        # Download button depends on courses being scanned
        if enabled and self.all_course_data:
            self.download_button.config(state="normal")
        else:
            self.download_button.config(state="disabled")

    def start_scan_thread(self):
        self.set_ui_state(False)
        self.course_listbox.delete(0, tk.END)
        self.all_course_data = [] # Clear previous scan results
        # Clear status text on new scan
        self.status_text.config(state="normal"); self.status_text.delete(1.0, tk.END); self.status_text.config(state="disabled")
        self.update_status("Scan initiated...")
        threading.Thread(target=self.scan_courses_task, daemon=True).start()

    def scan_courses_task(self):
        username = self.username_entry.get(); password = self.password_entry.get()
        if not username or not password:
            messagebox.showerror("Input Error", "Username and Password are required.")
            self.root.after(0, self.set_ui_state, True); return
        
        self.save_credentials() 
        driver = None
        try:
            browser_choice = self.browser_var.get()
            driver = setup_driver(browser_choice, self.update_status, self.headless_var.get())
            
            self.update_status("Logging in to Blackboard...")
            login(driver, username, password) # Assuming login confirms success by not raising error
            self.update_status("Login successful. Fetching course list...")
            
            # This is the call to the reverted function
            self.all_course_data = get_all_terms_and_courses(driver, self.update_status) 
            
            if self.all_course_data:
                self.update_status(f"Scan complete. Found {len(self.all_course_data)} courses across terms.")
                # Sort by term (desc) then course name (asc)
                self.all_course_data.sort(key=lambda x: (x.get('term', 'Unknown Term'), x.get('name', '')), reverse=False) # Term ascending might be more natural
                self.all_course_data.sort(key=lambda x: x.get('term', 'Unknown Term'), reverse=True) # Then reverse by term for newest first

                def update_listbox_ui():
                    self.course_listbox.delete(0, tk.END) # Clear again before populating
                    current_term_header = None # Use None to handle first term correctly
                    for course in self.all_course_data:
                        term = course.get('term', 'Unknown Term')
                        if term != current_term_header:
                            current_term_header = term
                            self.course_listbox.insert(tk.END, f"--- {current_term_header} ---")
                        self.course_listbox.insert(tk.END, f"  {course['name']}")
                    self.download_button.config(state="normal") # Enable download if courses found
                self.root.after(0, update_listbox_ui)
            else:
                self.update_status("Scan complete: No terms or courses found. Please check your Blackboard or the selectors in the script if the page structure has changed.")
                # Ensure download button is disabled if no courses
                self.root.after(0, lambda: self.download_button.config(state="disabled"))

        except RuntimeError as e: 
            self.update_status(f"Driver Error: {e}")
            messagebox.showerror("Driver Setup Error", str(e))
        except Exception as e:
            self.update_status(f"An error occurred during scan: {e}")
            import traceback; self.update_status(traceback.format_exc())
            messagebox.showerror("Scan Error", f"An unexpected error occurred during scan: {e}")
        finally:
            if driver: 
                try: driver.quit()
                except Exception as e_quit: self.update_status(f"Note: Error quitting driver: {e_quit}")
            self.root.after(0, self.set_ui_state, True)
            
    def start_download_thread(self):
        selected_indices = self.course_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("No Selection", "Please select at least one course to download.")
            return

        self.set_ui_state(False)
        self.update_status("Download initiated...")
        # Pass selected indices to the thread to avoid issues with GUI updates from worker thread
        threading.Thread(target=self.download_courses_task, args=(list(selected_indices),), daemon=True).start()

    def download_courses_task(self, selected_indices_tuple):
        username = self.username_entry.get(); password = self.password_entry.get()
        
        courses_to_download_names = []
        # ... (rest of course name gathering is fine) ...
        for i in selected_indices_tuple:
            try:
                item_text = self.course_listbox.get(i).strip() 
                if not item_text.startswith("---"): 
                    courses_to_download_names.append(item_text)
            except tk.TclError: 
                self.update_status("Error: Course listbox changed during selection processing.")
                self.root.after(0, self.set_ui_state, True); return
        
        if not courses_to_download_names:
             messagebox.showerror("Error", "No valid courses selected from list.")
             self.root.after(0, self.set_ui_state, True); return

        courses_to_process = [c for c in self.all_course_data if c['name'] in courses_to_download_names]

        self.update_status(f"Starting download for {len(courses_to_process)} selected course(s)...")
        driver = None
        try:
            browser_choice = self.browser_var.get()
            driver = setup_driver(browser_choice, self.update_status, self.headless_var.get())
            self.update_status("Logging in for download session...")
            login_cookies = login(driver, username, password)
            self.update_status("Login successful for download.")
            
            session = requests.Session()
            for cookie in login_cookies: 
                session.cookies.set(cookie['name'], cookie['value'], domain=cookie.get('domain'), path=cookie.get('path'))

            total_courses = len(courses_to_process)
            for course_idx, course in enumerate(courses_to_process):
                self.root.after(0, self.update_progress, 0) 
                
                term_name_cleaned = course.get('term', 'Unknown_Term') 
                course_name_cleaned = course['name'] 
                
                base_course_download_dir = os.path.join(self.path_var.get(), term_name_cleaned, course_name_cleaned)
                os.makedirs(base_course_download_dir, exist_ok=True)
                
                self.update_status(f"\n--- ({course_idx+1}/{total_courses}) Processing course: {course['name']} (Term: {term_name_cleaned}) ---")
                course_main_url = course['url']
                
                # --- OPTIMIZATION START ---
                self.update_status(f"  Navigating to course home: {course_main_url}")
                driver.get(course_main_url)
                course_page_wait = WebDriverWait(driver, 15) # Slightly shorter wait for main page elements
                try:
                    course_page_wait.until(EC.presence_of_element_located((By.ID, "courseMenuPalette_contents")))
                    self.update_status("    Course home page loaded. Identifying available sections...")
                except TimeoutException:
                    self.update_status(f"    Timeout waiting for course menu on main page for course '{course_name_cleaned}'. Skipping this course's sections.")
                    continue # To next course if course home doesn't load its menu

                available_sections_to_scrape = []
                for section_name_candidate in TARGET_COURSE_SECTIONS:
                    try:
                        # Use a very short timeout for checking existence of each link
                        # driver.find_element is immediate, WebDriverWait allows a small grace period
                        temp_wait = WebDriverWait(driver, 2) # Short wait: 2 seconds to find link
                        section_link_xpath = f"//ul[@id='courseMenuPalette_contents']//a[.//span[normalize-space(.)=\"{section_name_candidate}\"]]"
                        link_element = temp_wait.until(EC.presence_of_element_located((By.XPATH, section_link_xpath)))
                        link_url = link_element.get_attribute('href')
                        
                        if link_url and ("listContent.jsp" in link_url or "launchLink.jsp" in link_url):
                             available_sections_to_scrape.append({"name": section_name_candidate, "url": link_url})
                             self.update_status(f"    + Section '{section_name_candidate}' is available (URL: {link_url})")
                        else:
                            self.update_status(f"    - Section '{section_name_candidate}' found, but URL is not a content page type ({link_url}). Skipping.")
                    except TimeoutException:
                        self.update_status(f"    - Section '{section_name_candidate}' link not found quickly. Skipping this section.")
                    except NoSuchElementException: # Should be caught by TimeoutException with WebDriverWait
                        self.update_status(f"    - Section '{section_name_candidate}' link (NoSuchElement). Skipping this section.")
                # --- OPTIMIZATION END ---


                if not available_sections_to_scrape:
                    self.update_status(f"    No relevant sections found or accessible for course '{course_name_cleaned}'.")
                    continue # To the next course

                for section_info in available_sections_to_scrape:
                    section_name_to_find = section_info["name"]
                    section_target_url = section_info["url"]
                    content_map_for_section = [] 
                    
                    self.update_status(f"  Processing available section: '{section_name_to_find}'")
                    try:
                        self.update_status(f"    Navigating to section '{section_name_to_find}' via URL: {section_target_url}")
                        driver.get(section_target_url)

                        try:
                            # Wait for the content area of the section page to load
                            # This wait is specific to the section page, so 10-15s is reasonable
                            WebDriverWait(driver, 10).until( 
                                EC.presence_of_element_located((By.ID, "content_listContainer"))
                            )
                            self.update_status(f"      Section '{section_name_to_find}' content area loaded.")
                        except TimeoutException:
                            self.update_status(f"      Timeout: Section '{section_name_to_find}' loaded, but 'content_listContainer' not found. Scraping might be limited or fail.")
                        
                        self.update_status(f"      Scanning '{section_name_to_find}' for files and folders...")
                        clean_section_folder_name = re.sub(r'[\\/*?:"<>|]', "_", section_name_to_find)
                        
                        scrape_page_for_content(driver, content_map_for_section, self.update_status, current_relative_path=clean_section_folder_name)
                        
                        if content_map_for_section:
                            self.update_status(f"      Found {len(content_map_for_section)} potential items in '{section_name_to_find}'. Processing downloads...")
                            process_content_list(session, base_course_download_dir, content_map_for_section, 
                                                 lambda p_val: self.root.after(0, self.update_progress, p_val),
                                                 self.update_status)
                        else:
                            self.update_status(f"      No downloadable items or sub-folders found directly in '{section_name_to_find}'.")

                    # Removed Timeout/NoSuchElement here as we pre-filtered available_sections_to_scrape
                    # These exceptions would now relate to issues on the section page itself (e.g., content_listContainer not appearing)
                    except Exception as e_section_processing:
                        self.update_status(f"    - An unexpected error occurred while processing section '{section_name_to_find}': {type(e_section_processing).__name__} - {e_section_processing}")
                    finally:
                        # Optional: Navigate back to course main page if worried about state for next section,
                        # but if sections are independent, this might not be needed and saves a page load.
                        # For now, let's assume direct navigation to next section's URL is fine.
                        # If issues arise, add:
                        # if section_info != available_sections_to_scrape[-1]: # If not the last section
                        #     self.update_status(f"    Returning to course home before next section...")
                        #     driver.get(course_main_url)
                        #     WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "courseMenuPalette_contents")))
                        pass


                self.update_status(f"--- Finished processing course: {course['name']} ---")

            # ... (rest of the try...except...finally for the entire courses loop) ...
            self.update_status("\nAll selected courses and their specified sections processed!")
            messagebox.showinfo("Download Complete", "All selected courses have been processed. Check the status window for details.")
        except RuntimeError as e: 
            self.update_status(f"Driver Error during download: {e}")
            messagebox.showerror("Driver Setup Error", str(e))
        except Exception as e:
            self.update_status(f"A critical error occurred during download: {e}")
            import traceback; self.update_status(traceback.format_exc())
            messagebox.showerror("Download Error", f"A critical error occurred: {e}. Check status for details.")
        finally:
            if driver: 
                try: driver.quit()
                except Exception as e_quit: self.update_status(f"Note: Error quitting driver post-download: {e_quit}")
            self.root.after(0, self.set_ui_state, True)
            self.root.after(0, self.update_progress, 0)

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
