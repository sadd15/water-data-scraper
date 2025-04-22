import os
import os.path
import time
import json
import logging
from datetime import datetime, timedelta

# --- Selenium Imports ---
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
try: from webdriver_manager.chrome import ChromeDriverManager; USE_WEBDRIVER_MANAGER = True
except ImportError: USE_WEBDRIVER_MANAGER = False

# --- Google API Imports ---
try:
    from google.auth.transport.requests import Request; from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow; from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError: exit(1)

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s-%(levelname)s-%(message)s', handlers=[logging.StreamHandler()])

# --- ค่าคงที่และ การตั้งค่า ---
try: script_dir = os.path.dirname(os.path.abspath(__file__))
except NameError: script_dir = os.getcwd()
CONFIG_FILE = 'config.txt'; config_values = {}; SPREADSHEET_ID = None; SHEET_NAME_LATEST = None; SHEET_NAME_LOG = None
try:
    logging.info(f"--- อ่าน Config '{CONFIG_FILE}' ---")
    config_path = os.path.join(script_dir, CONFIG_FILE)
    if not os.path.exists(config_path): raise FileNotFoundError(f"ไม่พบ '{CONFIG_FILE}'...")
    with open(config_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip();
            if line and not line.startswith('#') and '=' in line: key, value = line.split('=', 1); config_values[key.strip()] = value.strip(); logging.info(f"  อ่าน {line_num}: {key.strip()}='{value.strip()}'")
    SPREADSHEET_ID = config_values.get('SPREADSHEET_ID'); SHEET_NAME_LATEST = config_values.get('SHEET_NAME_LATEST'); SHEET_NAME_LOG = config_values.get('SHEET_NAME_LOG')
    if not SPREADSHEET_ID or not SHEET_NAME_LATEST or not SHEET_NAME_LOG: raise ValueError(f"ไม่พบ SPREADSHEET_ID/LATEST/LOG")
    logging.info(f"อ่าน Config สำเร็จ: ID='{SPREADSHEET_ID}', Latest='{SHEET_NAME_LATEST}', Log='{SHEET_NAME_LOG}'")
except Exception as e: logging.error(f"Error อ่าน config: {e}"); exit(1)

TARGET_URL = 'https://hyd-app.rid.go.th/hydro4d.html'; TARGET_ROW_ID = "235"
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']; CREDENTIALS_FILE = 'credentials.json'; TOKEN_FILE = 'token.json'
CHROMEDRIVER_FALLBACK_PATH = os.path.join(script_dir, 'chromedriver.exe'); WAIT_TIME_SECONDS = 60
PAGE_LOAD_TIMEOUT_SECONDS = 180 # <<< เพิ่มเวลารอโหลดหน้าเว็บเป็น 3 นาที

# --- ฟังก์ชันยืนยันตัวตน Google (ถูกต้องแล้ว) ---
def authenticate_google_sheets():
    # ... (โค้ดส่วนนี้เหมือนเดิมเป๊ะ) ...
    logging.info("--- ยืนยันตัวตน Google Sheets API ---"); creds = None
    token_path = os.path.join(script_dir, TOKEN_FILE); credentials_path = os.path.join(script_dir, CREDENTIALS_FILE)
    if not os.path.exists(credentials_path): logging.error(f"ไม่พบ '{CREDENTIALS_FILE}'..."); return None
    if os.path.exists(token_path):
        try: creds = Credentials.from_authorized_user_file(token_path, SCOPES); logging.info(f"โหลด Creds จาก '{TOKEN_FILE}'")
        except Exception as e: logging.warning(f"โหลด token.json ไม่ได้: {e}"); creds = None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try: logging.info("รีเฟรช Token..."); creds.refresh(Request()); logging.info("รีเฟรชสำเร็จ")
            except Exception as e: logging.warning(f"รีเฟรช Token ไม่ได้ ({e})"); creds = None
        if not creds:
             logging.info("เริ่มยืนยันตัวตน Browser...")
             try: flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES); creds = flow.run_local_server(port=0, open_browser=True); logging.info("ยืนยันตัวตน Browser สำเร็จ")
             except Exception as e: logging.error(f"ยืนยันตัวตน Browser ผิดพลาด: {e}"); return None
        if creds:
            try:
                with open(token_path, 'w') as token: token.write(creds.to_json())
                logging.info(f"บันทึก Token ใหม่ลง '{TOKEN_FILE}'")
            except Exception as e: logging.error(f"บันทึก Token ไม่ได้: {e}")
    if not creds: logging.error("ไม่สามารถรับ Credentials"); return None
    try: service = build('sheets', 'v4', credentials=creds); logging.info("เชื่อมต่อ Sheets API Service สำเร็จ!"); return service
    except Exception as e: logging.error(f"สร้าง Service Sheets ผิดพลาด: {e}"); return None


# --- ฟังก์ชันดึงข้อมูลและจัดรูปแบบ (เพิ่ม Timeout และ Options) ---
def scrape_format_like_web():
    logging.info(f"--- เริ่มต้นดึงข้อมูล Selenium (Row ID: {TARGET_ROW_ID}) ---");
    options = webdriver.ChromeOptions()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920x1080')
    options.add_argument("--log-level=3"); options.add_experimental_option('excludeSwitches', ['enable-logging'])
    options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36') # ลองเปลี่ยน User Agent ใหม่ๆ
    # --- เพิ่ม Options สำหรับ Linux/Container ---
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-extensions')
    options.add_argument("--disable-infobars")
    # -----------------------------------------

    driver = None; service = None
    try: # เปิด Browser
        if USE_WEBDRIVER_MANAGER:
            try: service = ChromeService(ChromeDriverManager().install()); driver = webdriver.Chrome(service=service, options=options); logging.info("เปิด Chrome (Headless) สำเร็จ")
            except Exception as e_wdm: logging.warning(f"wdm ล้มเหลว ({e_wdm}), ลอง Path สำรอง..."); raise
        else:
             if os.path.exists(CHROMEDRIVER_FALLBACK_PATH): service = ChromeService(executable_path=CHROMEDRIVER_FALLBACK_PATH); driver = webdriver.Chrome(service=service, options=options); logging.info("เปิด Chrome (Headless) สำรองสำเร็จ")
             else: raise WebDriverException("ไม่พบ ChromeDriver")
    except Exception as e_init: logging.error(f"Error เริ่มต้น Selenium: {e_init}"); return None

    data_for_sheet = []
    try:
        logging.info(f"กำลังเปิด URL: {TARGET_URL} (Page Load Timeout: {PAGE_LOAD_TIMEOUT_SECONDS} วินาที)...")
        # --- ตั้งค่า Page Load Timeout ---
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT_SECONDS)
        # --------------------------------
        driver.get(TARGET_URL) # <<< ลองโหลดหน้าเว็บ
        table_id = "jqGrid"
        logging.info(f"โหลด URL สำเร็จ, กำลังรอตาราง ID='{table_id}' และแถว ID='{TARGET_ROW_ID}'...")

        try: # รอ Container และ แถวเป้าหมาย
            WebDriverWait(driver, WAIT_TIME_SECONDS).until( EC.presence_of_element_located((By.ID, "gbox_" + table_id)) ); logging.info("พบ Container")
            target_row_element = WebDriverWait(driver, WAIT_TIME_SECONDS).until( EC.element_to_be_clickable((By.ID, TARGET_ROW_ID)) ); logging.info(f"พบแถว ID='{TARGET_ROW_ID}'")
        except TimeoutException: logging.error(f"Error: หมดเวลารอ ({WAIT_TIME_SECONDS} วิ) ไม่พบตาราง/แถว ID='{TARGET_ROW_ID}' หลังโหลดหน้าเว็บ"); return None

        # --- กำหนด Headers ---
        today = datetime.now(); date_q_values = []
        thai_month_abbr = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]; thai_day_abbr = ["อา.", "จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส."]
        for i in range(6, -1, -1): target_date = today - timedelta(days=i); day_abbr = thai_day_abbr[int(target_date.strftime("%w"))]; day = target_date.day; month_abbr = thai_month_abbr[target_date.month]; date_q_values.append(f"{day_abbr} {day} {month_abbr}")
        headers = [ "ลำดับ", "สถานี", "ลุ่มน้ำ", "อำเภอ", "จังหวัด", "ระดับตลิ่ง(ม.)"]; headers.extend(date_q_values); headers.extend(["เฉลี่ย", "กราฟ", "ร้อยละความจุ", "แนวโน้ม"])
        headers_bottom = [ "", "", "", "", "", "ความจุลำน้ำ(ลบ.ม./วินาที)"]; headers_bottom.extend([f"ปริมาณน้ำQ{i}" for i in range(7, 0, -1)]); headers_bottom.extend(["เฉลี่ย ปริมาณน้ำ", "", "", ""])
        data_for_sheet.append(headers); data_for_sheet.append(headers_bottom); logging.info(f"สร้าง Headers 2 แถว สำเร็จ")
        # --- ดึงข้อมูลดิบ และ ทำความสะอาด ---
        try:
            raw_cols_elements = [td for td in target_row_element.find_elements(By.TAG_NAME, "td") if td.is_displayed()]
            raw_cols = [' '.join(element.text.split()).replace('\xa0', ' ') for element in raw_cols_elements]
            logging.info(f"ข้อมูลดิบ (หลัง clean): {raw_cols}")
            if len(raw_cols) == 17:
                row_top = []; row_bottom = [] # สร้างแถวข้อมูล
                for i in range(5): row_top.append(raw_cols[i]); row_bottom.append("")
                col6_parts = raw_cols[5].split(); row_top.append(col6_parts[0] if len(col6_parts) > 0 else "N/A"); row_bottom.append(col6_parts[-1] if len(col6_parts) > 1 else "N/A")
                for i in range(6, 13): q_val_parts = raw_cols[i].split(); row_top.append(q_val_parts[0] if len(q_val_parts) > 0 else "N/A"); row_bottom.append(q_val_parts[-1] if len(q_val_parts) > 1 else "N/A")
                avg_val_parts = raw_cols[13].split(); row_top.append(avg_val_parts[0] if len(avg_val_parts) > 0 else "N/A"); row_bottom.append(avg_val_parts[-1] if len(avg_val_parts) > 1 else "N/A")
                row_top.append(raw_cols[14]); row_top.append(raw_cols[15]); row_top.append(raw_cols[16]); row_bottom.extend(["", "", ""])
                data_for_sheet.append(row_top); data_for_sheet.append(row_bottom); logging.info("จัดรูปแบบข้อมูลคล้ายหน้าเว็บสำเร็จ")
            else: logging.warning(f"ดึงข้อมูลดิบได้ {len(raw_cols)} ไม่ครบ 17"); return None
        except Exception as row_err: logging.error(f"เกิดปัญหาดึง/จัดรูปแบบแถว ID='{TARGET_ROW_ID}': {row_err}"); return None

    # --- จัดการ Error ตอนโหลดหน้าเว็บ ---
    except TimeoutException as page_load_timeout:
         logging.error(f"Error: หมดเวลา ({PAGE_LOAD_TIMEOUT_SECONDS} วินาที) ในการโหลด URL: {TARGET_URL}")
         try: driver.save_screenshot(os.path.join(script_dir,f"screenshot_pageload_timeout.png")); logging.info("บันทึก screenshot...")
         except Exception: pass
         data_for_sheet = None # คืนค่า None ถ้าโหลดหน้าเว็บ Timeout
    # --- จัดการ Error อื่นๆ และ ปิด Browser ---
    except Exception as e_scrape: logging.error(f"เกิดข้อผิดพลาดระหว่างดึงข้อมูล: {e_scrape}"); import traceback; traceback.print_exc(); data_for_sheet = None
    finally:
        if driver:
            try: driver.quit(); logging.info("ปิด Chrome Browser (Headless) แล้ว")
            except Exception as e_quit: logging.error(f"เกิดปัญหาปิด Browser: {e_quit}")
    return data_for_sheet if data_for_sheet and len(data_for_sheet) == 4 else None # ตรวจสอบให้ดีขึ้นก่อนคืนค่า


# --- ฟังก์ชันเขียนทับชีตล่าสุด (ใช้ USER_ENTERED) ---
def update_latest_sheet(service, data_to_write):
    # ... (โค้ดส่วนนี้เหมือนเดิมเป๊ะ) ...
    sheet_name = SHEET_NAME_LATEST; logging.info(f"--- เริ่มต้นอัปเดตชีตล่าสุด '{sheet_name}' ---")
    if not service: logging.error("ไม่มี Service"); return False
    if not data_to_write or len(data_to_write) != 4: logging.error("ข้อมูลเขียนชีตล่าสุดไม่ถูกต้อง"); return False
    num_headers = 2; num_data_rows = 2; data_with_timestamp = [row[:] for row in data_to_write]
    try:
        logging.info(f"กำลังล้างชีต '{sheet_name}'..."); clear_range = f"{sheet_name}!A1:Z"
        service.spreadsheets().values().clear(spreadsheetId=SPREADSHEET_ID, range=clear_range).execute(); logging.info("ล้างชีตสำเร็จ")
        run_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S'); timestamp_header = "วันที่ดึงข้อมูล"
        if timestamp_header not in data_with_timestamp[0]: data_with_timestamp[0].append(timestamp_header)
        if len(data_with_timestamp[1]) < len(data_with_timestamp[0]): data_with_timestamp[1].append("")
        while len(data_with_timestamp[2]) < len(data_with_timestamp[0]): data_with_timestamp[2].append("")
        while len(data_with_timestamp[3]) < len(data_with_timestamp[0]) - 1: data_with_timestamp[3].append("")
        data_with_timestamp[3].append(run_timestamp)
        write_range = f"{sheet_name}!A1"; body = {'values': data_with_timestamp}
        logging.info(f"กำลังเขียน {len(data_with_timestamp)} แถว ลงชีต '{sheet_name}' (valueInputOption=USER_ENTERED)...")
        result = service.spreadsheets().values().update(spreadsheetId=SPREADSHEET_ID, range=write_range, valueInputOption='USER_ENTERED', body=body).execute()
        updated_cells = result.get('updatedCells', 'N/A'); logging.info(f"เขียนลงชีต '{sheet_name}' สำเร็จ! เซลล์ที่อัปเดต: {updated_cells}")
        return True
    except HttpError as error: logging.error(f"เกิด HttpError ตอนเขียนชีต '{sheet_name}': {error}"); return False
    except Exception as e: logging.error(f"เกิดข้อผิดพลาดตอนเขียนชีต '{sheet_name}': {e}"); import traceback; traceback.print_exc(); return False


# --- ฟังก์ชันเขียนต่อท้ายชีต Log (ใช้ RAW และ Header Format DD/MM/YYYY ที่ถูกต้อง) ---
def append_data_to_log_sheet(service, full_data):
    # ... (โค้ดส่วนนี้เหมือนเดิมเป๊ะ) ...
    sheet_name = SHEET_NAME_LOG; logging.info(f"--- เริ่มต้นเขียนข้อมูลล่าสุดต่อท้ายชีต Log '{sheet_name}' ---")
    if not service: logging.error("ไม่มี Service object"); return False
    if not full_data or len(full_data) != 4: logging.error("ข้อมูล Input Log ไม่ถูกต้อง"); return False
    try:
        today = datetime.now(); formatted_date = today.strftime('%d/%m/%Y'); timestamp_header = "วันที่ดึงข้อมูล"
        log_header_top = ["ลำดับ", "สถานี", "ลุ่มน้ำ", "อำเภอ", "จังหวัด","ระดับตลิ่ง(ม.)", f"ระดับน้ำ ('{formatted_date})", f"วันที่ ({formatted_date})", "ร้อยละความจุ(%)", "สถานะ/แนวโน้ม", timestamp_header]
        log_header_bottom = ["", "", "", "", "", "ความจุลำน้ำ(ลบ.ม./วินาที)", f"ปริมาณน้ำ ('{formatted_date})", "", "", "", ""]
        headers_for_log = [log_header_top, log_header_bottom]; data_row_top = full_data[2]; data_row_bottom = full_data[3]
        log_row_top = []; log_row_bottom = []
        for i in range(6): log_row_top.append(data_row_top[i]); log_row_bottom.append(data_row_bottom[i])
        q1_val_parts_top = data_row_top[12].split(); log_row_top.append(q1_val_parts_top[0] if len(q1_val_parts_top) > 0 else "N/A")
        q1_val_parts_bottom = data_row_bottom[12].split(); log_row_bottom.append(q1_val_parts_bottom[0] if len(q1_val_parts_bottom) > 0 else "N/A")
        log_row_top.append(formatted_date); log_row_bottom.append("") # วันที่ วว/ดด/ปปปป
        log_row_top.append(data_row_top[15]); log_row_top.append(data_row_top[16]); log_row_bottom.append(""); log_row_bottom.append("")
        data_to_append_to_log = []; check_range = f"{sheet_name}!A1"; result = service.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=check_range).execute(); existing_values = result.get('values', [])
        run_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S');
        if not existing_values: logging.info(f"ชีต '{sheet_name}' ว่าง, เขียน Header"); data_to_append_to_log.extend(headers_for_log)
        else: logging.info(f"ชีต '{sheet_name}' มีข้อมูลแล้ว, เขียนเฉพาะข้อมูลใหม่")
        num_final_headers = len(log_header_top)
        while len(log_row_top) < num_final_headers : log_row_top.append('')
        while len(log_row_bottom) < num_final_headers -1 : log_row_bottom.append('')
        log_row_bottom.append(run_timestamp)
        data_to_append_to_log.append(log_row_top); data_to_append_to_log.append(log_row_bottom)
        append_range = f"{sheet_name}!A1"; body = {'values': data_to_append_to_log }
        logging.info(f"กำลังเขียน {len(data_to_append_to_log)} แถวใหม่ ต่อท้ายชีต '{sheet_name}' (valueInputOption=RAW)...")
        result = service.spreadsheets().values().append(spreadsheetId=SPREADSHEET_ID, range=append_range, valueInputOption='RAW', insertDataOption='INSERT_ROWS', body=body).execute()
        updates = result.get('updates', {}); updated_rows = updates.get('updatedRows', 'N/A'); logging.info(f"เขียนข้อมูลต่อท้ายชีต '{sheet_name}' สำเร็จ! แถวที่เพิ่ม: {updated_rows}")
        return True
    except HttpError as error: logging.error(f"เกิด HttpError ตอนเขียนชีต Log '{sheet_name}': {error}"); return False
    except Exception as e: logging.error(f"เกิดข้อผิดพลาดตอนเขียนชีต Log '{sheet_name}': {e}"); import traceback; traceback.print_exc(); return False

# --- ส่วนหลักในการรันสคริปต์ ---
if __name__ == '__main__':
    start_time = time.time()
    logging.info(f"--- Script Start: Selenium Scrape & Log (Row ID {TARGET_ROW_ID} - Added Timeout & Options) ---") # อัปเดตชื่อ Log
    sheet_service = authenticate_google_sheets()
    if sheet_service:
        formatted_data = scrape_format_like_web()
        # --- แก้ไขการตรวจสอบ formatted_data ---
        if formatted_data: # ตรวจสอบแค่ว่าไม่เป็น None (เพราะ scrape_format_like_web คืน None ถ้า Error)
            update_latest_sheet(service=sheet_service, data_to_write=[row[:] for row in formatted_data])
            append_data_to_log_sheet(service=sheet_service, full_data=formatted_data)
        # ------------------------------------
        else: logging.warning(f"ไม่สามารถดึง/จัดรูปแบบข้อมูลจากแถว ID='{TARGET_ROW_ID}' ได้")
    else: logging.error("ไม่สามารถเชื่อมต่อ Google Sheets API ได้")
    end_time = time.time()
    logging.info(f"--- Script End: Total Time: {end_time - start_time:.2f} seconds ---")
