# -*- coding: utf-8 -*-
from pathlib import Path
import json
import uuid
from datetime import datetime
import mimetypes
import re
import base64
import time
import os
import sys
from urllib.parse import quote, quote_plus, urlparse, parse_qs, unquote

import requests
from flask import Flask, request, jsonify, send_from_directory, send_file, session, redirect, url_for
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
DATA_DIR = BASE_DIR / "data"
HISTORY_FILE = DATA_DIR / "chats.json"
ACCOUNT_FILE = DATA_DIR / "accounts.json"
UPLOAD_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

API_BASE = "https://api.groq.com/openai/v1"
API_KEY = ""
MODEL = ""
VISION_MODEL = ""
TRANSCRIBE_MODEL = "whisper-large-v3-turbo"
IMAGE_GEN_PROVIDER = "pollinations"
POLLINATIONS_API_KEY = ""
GENERATED_DIR = BASE_DIR / "generated"
GENERATED_DIR.mkdir(exist_ok=True)
FILES_DIR = BASE_DIR / "generated_files"
FILES_DIR.mkdir(exist_ok=True)

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
app.config["JSON_AS_ASCII"] = False
app.config["SECRET_KEY"] = os.environ.get("MERAJ_SECRET_KEY", "") or uuid.uuid4().hex + uuid.uuid4().hex
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"



def read_account():
    if not ACCOUNT_FILE.exists():
        return None
    try:
        data = json.loads(ACCOUNT_FILE.read_text(encoding="utf-8"))
        # accounts.json stores the account(s). Keep backward compatibility with
        # both a single account object and an {"accounts": [...]} structure.
        if isinstance(data, dict) and data.get("username") and data.get("password_hash"):
            return data
        if isinstance(data, dict) and isinstance(data.get("accounts"), list):
            for account in data["accounts"]:
                if isinstance(account, dict) and account.get("username") and account.get("password_hash"):
                    return account
    except Exception as e:
        print("[ACCOUNT READ ERROR]", repr(e))
    return None


def write_account(username, password):
    ACCOUNT_FILE.write_text(
        json.dumps({
            "username": username,
            "password_hash": generate_password_hash(password),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            if request.path.startswith("/api/") or request.path.startswith("/generated"):
                return jsonify(error="نیاز به ورود دارید.", login_required=True), 401
            return redirect(url_for("login_page"))
        return view(*args, **kwargs)
    return wrapped


def auth_page(mode="login", error=""):
    account_exists = read_account() is not None
    if mode == "register" and account_exists:
        mode = "login"
    title = "ثبت‌نام | Taha's Helper Bot" if mode == "register" else "ورود | Taha's Helper Bot"
    heading = "ساخت حساب" if mode == "register" else "ورود به Taha's Helper Bot"
    error_html = f'<div class="err">{error}</div>' if error else ""
    if mode == "register":
        body = """
        <form method="post" action="/register" class="auth-form">
          <label>نام کاربری</label>
          <input name="username" autocomplete="username" maxlength="40" required placeholder="نام کاربری">
          <label>رمز عبور</label>
          <input name="password" type="password" autocomplete="new-password" minlength="6" required placeholder="رمز عبور">
          <label>تکرار رمز عبور</label>
          <input name="password2" type="password" autocomplete="new-password" minlength="6" required placeholder="تکرار رمز عبور">
          <button type="submit">ثبت‌نام</button>
        </form>
        <p class="switch">حساب داری؟ <a href="/login">ورود</a></p>
        """
    else:
        body = """
        <form method="post" action="/login" class="auth-form">
          <label>رمز عبور</label>
          <input name="password" type="password" autocomplete="current-password" required placeholder="رمز عبور">
          <button type="submit">ورود</button>
        </form>
        """
        if not account_exists:
            body += '<p class="switch">هنوز حسابی ساخته نشده؟ <a href="/register">ثبت‌نام</a></p>'
    return f"""<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>{title}</title>
<style>
*{{box-sizing:border-box}}html,body{{margin:0;min-height:100%;background:#0b0b0f;color:#f4f4f5;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Tahoma,Arial,sans-serif}}body{{display:grid;place-items:center;padding:20px}}.card{{width:min(430px,100%);background:#17171c;border:1px solid #30303a;border-radius:22px;padding:28px;box-shadow:0 20px 60px #0008}}h1{{margin:0 0 8px;font-size:27px}}.sub{{color:#a1a1aa;margin:0 0 24px}}.auth-form{{display:grid;gap:10px}}label{{font-size:14px;color:#d4d4d8;margin-top:4px}}input{{width:100%;border:1px solid #383842;background:#0f0f13;color:#fff;border-radius:12px;padding:14px;font:inherit;outline:none}}input:focus{{border-color:#777}}button{{border:0;border-radius:12px;background:#f4f4f5;color:#111;padding:14px;font:inherit;font-weight:700;cursor:pointer;margin-top:8px}}.switch{{text-align:center;color:#a1a1aa;margin:18px 0 0}}a{{color:#fff}}.err{{background:#3a1d22;border:1px solid #6b2d38;color:#ffb4bd;padding:11px 13px;border-radius:12px;margin-bottom:14px}}
</style></head><body><main class="card"><h1>Taha's Helper Bot</h1><p class="sub">{heading}</p>{error_html}{body}</main></body></html>"""

def get_models(key):
    r = requests.get(
        f"{API_BASE}/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get("data", [])


def choose_model(models):
    ids = [m.get("id", "") for m in models]
    preferred = [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
    ]
    for x in preferred:
        if x in ids:
            return x
    blocked = ("whisper", "guard", "embedding", "tts")
    for x in ids:
        if x and not any(b in x.lower() for b in blocked):
            return x
    return ids[0] if ids else None


def setup():
    global API_KEY, MODEL, VISION_MODEL, TRANSCRIBE_MODEL, POLLINATIONS_API_KEY
    print("\n" + "=" * 56)
    print("                    Taha's Helper Bot")
    print("=" * 56)

    # On hosting platforms such as Render, secrets are supplied as
    # environment variables. Locally, keep the old terminal input behavior.
    API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
    POLLINATIONS_API_KEY = os.environ.get("POLLINATIONS_API_KEY", "").strip()

    if not API_KEY and sys.stdin.isatty():
        print("🔑 API Key را همین‌جا در ترمینال وارد کن.")
        print("API Key: ", end="", flush=True)
        API_KEY = input().strip()

    if not POLLINATIONS_API_KEY and sys.stdin.isatty():
        print("🖼 برای ساخت تصویر، کلید Pollinations را هم وارد کن.")
        print("اگر فعلاً نمی‌خواهی ساخت تصویر استفاده شود، Enter بزن.")
        print("Pollinations Key: ", end="", flush=True)
        POLLINATIONS_API_KEY = input().strip()

    if not API_KEY:
        print("❌ GROQ_API_KEY تنظیم نشده است.")
        return False
    try:
        models = get_models(API_KEY)
        MODEL = choose_model(models)
        available = {m.get("id", "") for m in models}
        # Vision-capable model. Groq currently documents Qwen 3.6/3.8 27B
        # as multimodal models for image understanding.
        vision_preferred = [
            "qwen/qwen3.6-27b",
            "qwen/qwen3.8-27b",
            "meta-llama/llama-4-maverick-17b-128e-instruct",
            "meta-llama/llama-4-scout-17b-16e-instruct",
        ]
        VISION_MODEL = next((x for x in vision_preferred if x in available), "")
        if "whisper-large-v3-turbo" in available:
            TRANSCRIBE_MODEL = "whisper-large-v3-turbo"
        elif "whisper-large-v3" in available:
            TRANSCRIBE_MODEL = "whisper-large-v3"
        if not MODEL:
            print("❌ مدل متنی پیدا نشد.")
            return False
        print("✅ API Key دریافت شد.")
        print("🤖 مدل متنی خودکار:", MODEL)
        print("🖼 مدل تصویر خودکار:", VISION_MODEL or "پیدا نشد")
        print("🎙 مدل صدا:", TRANSCRIBE_MODEL)
        return True
    except Exception as e:
        print("❌ خطا در اتصال به Groq:", e)
        return False


def read_history():
    if not HISTORY_FILE.exists():
        return {}
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print("[HISTORY READ ERROR]", repr(e))
        return {}


def write_history(data):
    tmp = HISTORY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(HISTORY_FILE)


def save_chat(chat_id, messages, title=None):
    data = read_history()
    old = data.get(chat_id, {})
    clean_messages = []
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role in ("user", "assistant", "system") and isinstance(content, str):
            item = {"role": role, "content": content}
            if isinstance(m.get("sources"), list): item["sources"] = m.get("sources")
            if isinstance(m.get("image_url"), str): item["image_url"] = m.get("image_url")
            if isinstance(m.get("image_prompt"), str): item["image_prompt"] = m.get("image_prompt")
            if isinstance(m.get("file_url"), str): item["file_url"] = m.get("file_url")
            if isinstance(m.get("file_name"), str): item["file_name"] = m.get("file_name")
            clean_messages.append(item)
    if title is None:
        title = old.get("title") or "چت جدید"
    data[chat_id] = {
        "id": chat_id,
        "title": title[:70] if title else "چت جدید",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "messages": clean_messages,
    }
    write_history(data)


def make_title(messages):
    for m in messages:
        if m.get("role") == "user" and m.get("content", "").strip():
            text = re.sub(r"\\s+", " ", m["content"]).strip()
            return text[:55] + ("…" if len(text) > 55 else "")
    return "چت جدید"


def extract_text(path):
    """Extract common text-like files without PyMuPDF/FFmpeg or extra packages."""
    ext = path.suffix.lower()
    text_exts = {
        ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl",
        ".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".htm",
        ".xml", ".yaml", ".yml", ".ini", ".cfg", ".log", ".sql", ".java",
        ".c", ".cpp", ".h", ".hpp", ".php", ".rb", ".go", ".rs",
    }
    if ext not in text_exts:
        return None
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp1256", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def build_file_context(path):
    ext = path.suffix.lower()
    text = extract_text(path)
    if text is not None:
        limit = 35_000
        clipped = text[:limit]
        suffix_note = "\n[ادامه فایل به دلیل محدودیت طول ارسال حذف شد.]" if len(text) > limit else ""
        return f"\n\n--- فایل پیوست: {path.name} ---\n{clipped}{suffix_note}\n--- پایان فایل ---"
    if ext == ".pdf":
        return (
            f"\n\n--- فایل پیوست: {path.name} ---\n"
            "این فایل PDF است. نسخهٔ فعلی Taha's Helper Bot استخراج مستقیم متن PDF را بدون PyMuPDF انجام نمی‌دهد؛ "
            "اگر متن PDF را به TXT تبدیل کنی، می‌توانم محتوایش را تحلیل کنم.\n--- پایان فایل ---"
        )
    return (
        f"\n\n--- فایل پیوست: {path.name} ---\n"
        "این نوع فایل در نسخهٔ فعلی قابل خواندن متنی نیست، اما فایل در سرور ذخیره شده است.\n"
        "--- پایان فایل ---"
    )


WEB_TRIGGER_WORDS = [
    "امروز", "الان", "جدیدترین", "آخرین", "اخبار", "خبر", "قیمت", "نرخ", "آب و هوا",
    "هوا", "بورس", "دلار", "یورو", "زمان", "ساعت", "نتیجه", "مسابقه", "بازی", "امتیاز",
    "2026", "2025", "latest", "today", "now", "news", "price", "weather", "score", "match",
    "current", "recent", "who is", "what happened", "search", "web"
]

def should_search_web(text):
    t = (text or "").lower().strip()
    return any(k in t for k in WEB_TRIGGER_WORDS)

def _clean_html_text(x):
    x = re.sub(r"<[^>]+>", " ", x or "")
    x = re.sub(r"\s+", " ", x).strip()
    return x

def search_web(query, max_results=5):
    try:
        url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Android) AppleWebKit/537.36 Chrome/120 Safari/537.36"}, timeout=15)
        r.raise_for_status()
        html = r.text
        results = []
        pattern = re.compile(r'<a[^>]+class=["\']result__a["\'][^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
        for match in pattern.finditer(html):
            href, title_html = match.groups()
            title = _clean_html_text(title_html)
            if href.startswith('//'):
                href = 'https:' + href
            if 'uddg=' in href:
                try:
                    href = unquote(parse_qs(urlparse(href).query).get('uddg', [href])[0])
                except Exception:
                    pass
            if not href.startswith(('http://', 'https://')) or not title:
                continue
            tail = html[match.end():match.end()+2500]
            sm = re.search(r'class=["\'][^"\']*result__snippet[^"\']*["\'][^>]*>(.*?)</', tail, re.I | re.S)
            snippet = _clean_html_text(sm.group(1)) if sm else ''
            results.append({"title": title, "url": href, "snippet": snippet[:600]})
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        print("[WEB SEARCH ERROR]", repr(e))
        return []

def web_context(results):
    if not results:
        return ""
    lines = ["نتایج جستجوی وب زیر را به‌عنوان زمینه استفاده کن. اگر پاسخ در آنها نیست، حدس نزن."]
    for i, item in enumerate(results, 1):
        lines.append(f"[{i}] {item['title']} — {item['url']}\nخلاصه: {item.get('snippet','')}")
    return "\n".join(lines)



def is_file_request(text):
    t = (text or "").lower()
    triggers = [
        "فایل بساز", "فایل ایجاد", "فایل درست کن", "فایل آماده کن", "فایل تولید کن",
        "فایل برام", "فایل برای من", "فایل بده", "فایل خروجی", "فایل دانلود",
        "create a file", "make a file", "generate a file", "create file", "make file",
        "downloadable file", "export as file"
    ]
    if any(x in t for x in triggers):
        return True
    return "فایل" in t and any(v in t for v in ("بساز", "ایجاد", "درست کن", "تولید", "آماده", "بده"))


def requested_file_instruction(user_text):
    return (
        "کاربر صراحتاً درخواست ساخت فایل کرده است. پاسخ را طوری تولید کن که محتوای فایل دقیق و کامل باشد. "
        "اگر فایل کدنویسی/متنی است، کل محتوای فایل را فقط داخل یک بلوک کد سه‌تایی قرار بده و بیرون آن حداکثر یک توضیح کوتاه بنویس. "
        "داخل بلوک کد هیچ توضیح اضافه، مقدمه یا markdown دیگری نگذار. "
        "اگر کاربر نام فایل یا پسوند مشخصی خواست، همان را رعایت کن. درخواست کاربر: " + (user_text or "")
    )


def infer_file_name(user_text, reply):
    text = ((user_text or "") + " " + (reply or "")).lower()
    # Explicit filename with a common extension.
    m = re.search(r"[\\/\\w.-]+\.(py|js|ts|html|css|json|xml|csv|txt|md|java|c|cpp|h|hpp|sql|sh|bat|yml|yaml|toml|ini|jsx|tsx)$", text, re.I)
    if m:
        name = Path(m.group(0).split('/')[-1].split('\\')[-1]).name
        return name
    ext = ".txt"
    mapping = [
        (("python", "پایتون", "py"), ".py"),
        (("javascript", "جاوااسکریپت", "js"), ".js"),
        (("typescript", "تایپ‌اسکریپت", "typescript", "ts"), ".ts"),
        (("html", "اچ تی ام ال"), ".html"),
        (("css", "سی اس اس"), ".css"),
        (("json",), ".json"),
        (("csv",), ".csv"),
        (("markdown", "مارک‌داون", "md"), ".md"),
        (("sql",), ".sql"),
        (("java",), ".java"),
        (("c++", "cpp"), ".cpp"),
    ]
    for words, candidate in mapping:
        if any(w in text for w in words):
            ext = candidate
            break
    base = "meraj_file"
    # A simple Persian/English hint for a nicer filename, without unsafe characters.
    if "پروژه" in text or "project" in text:
        base = "project"
    elif "کد" in text or "code" in text:
        base = "code"
    elif "لیست" in text or "list" in text:
        base = "list"
    return base + ext


def extract_file_content(reply):
    matches = re.findall(r"```(?:[\w#+.-]+)?\s*\n?(.*?)```", reply or "", flags=re.S)
    if matches:
        return matches[0].strip()
    return (reply or "").strip()


def save_requested_file(user_text, reply):
    content = extract_file_content(reply)
    if not content:
        return None
    name = infer_file_name(user_text, reply)
    # Prevent path traversal and duplicate names.
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", name).strip("._") or "meraj_file.txt"
    if "." not in safe_name:
        safe_name += ".txt"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target = FILES_DIR / f"{stamp}_{safe_name}"
    target.write_text(content, encoding="utf-8")
    return {"url": "/generated-download-file/" + target.name, "name": safe_name}

def groq_chat(messages, use_vision=False):
    model = VISION_MODEL if use_vision and VISION_MODEL else MODEL
    if use_vision and not VISION_MODEL:
        raise RuntimeError("مدل بینایی در API پیدا نشد. این API Key باید یک مدل vision داشته باشد.")
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "Connection": "close",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "max_completion_tokens": 600,
    }

    last_error = None
    for attempt in range(2):
        try:
            r = requests.post(
                f"{API_BASE}/chat/completions",
                headers=headers,
                json=payload,
                timeout=(15, 75 if not use_vision else 105),
            )
            if r.status_code == 429:
                try:
                    detail = r.json()
                except Exception:
                    detail = {}
                msg = str(detail.get("error", {}).get("message", "")) if isinstance(detail, dict) else str(detail)
                if "output tokens per minute" in msg.lower() or "rate_limit_exceeded" in str(detail).lower():
                    payload["max_completion_tokens"] = 350
                    r = requests.post(
                        f"{API_BASE}/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=(15, 75 if not use_vision else 105),
                    )
            if not r.ok:
                try:
                    detail = r.json()
                except Exception:
                    detail = r.text
                if r.status_code == 429:
                    raise RuntimeError("محدودیت مدل فعال شده است. چند لحظه صبر کن و دوباره امتحان کن.")
                raise RuntimeError(f"API error {r.status_code}: {detail}")
            data = r.json()
            return data["choices"][0]["message"]["content"]
        except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError, requests.exceptions.Timeout) as e:
            last_error = e
            if attempt < 1:
                time.sleep(1.2)
                continue
            raise RuntimeError("اتصال Groq قطع شد یا پاسخ دیر رسید. دوباره امتحان کن.") from e
        except requests.exceptions.RequestException as e:
            last_error = e
            if attempt < 1:
                time.sleep(1.2)
                continue
            raise RuntimeError("ارتباط با سرویس هوش مصنوعی برقرار نشد. اینترنت و API Key را بررسی کن.") from e
    raise RuntimeError(str(last_error or "خطای ناشناخته در اتصال به Groq"))


def is_image_file(path):
    return path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def image_data_url(path):
    if not path.exists():
        raise FileNotFoundError(f"فایل تصویر پیدا نشد: {path.name}")
    size = path.stat().st_size
    if size > 20 * 1024 * 1024:
        raise RuntimeError("حجم تصویر بیشتر از 20MB است.")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        raise RuntimeError("فرمت تصویر پشتیبانی نمی‌شود.")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def prepare_messages(raw_messages):
    """Prepare a compact API history so old images are not re-uploaded on every turn."""
    prepared = []
    has_image = False
    # Sending the entire history plus old base64 images can cause remote
    # disconnects. Keep recent context only; the complete history is still saved locally.
    raw_messages = raw_messages[-10:]
    latest_user_index = max((i for i, m in enumerate(raw_messages) if m.get("role") == "user"), default=-1)
    for idx, m in enumerate(raw_messages):
        role = m.get("role")
        content = m.get("content", "")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue

        image_match = re.search(r"\[تصویر پیوست شد: (.+?)\]", content)
        if image_match:
            # Only the newest user image is sent as base64. Older images stay in history
            # but are represented as text to keep requests small and reliable.
            if idx != latest_user_index:
                prepared.append({"role": role, "content": re.sub(r"\[تصویر پیوست شد: .+?\]", "[تصویر قبلی پیوست شد]", content)})
                continue
            filename = Path(image_match.group(1)).name
            path = UPLOAD_DIR / filename
            if not path.exists() or not is_image_file(path):
                prepared.append({"role": role, "content": content})
                continue
            text = re.sub(r"\[تصویر پیوست شد: .+?\]", "", content).strip()
            if not text:
                text = "این تصویر را با دقت بررسی کن و محتوای آن را توضیح بده. اگر داخل تصویر متن، جدول، نمودار یا خطایی وجود دارد، آن را هم توضیح بده."
            prepared.append({
                "role": role,
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": image_data_url(path)}},
                ],
            })
            has_image = True
            continue

        # Existing text-file marker: append actual file contents to the message.
        marker = re.search(r"\[فایل پیوست شد: (.+?)\]", content)
        if marker:
            filename = Path(marker.group(1)).name
            path = UPLOAD_DIR / filename
            if path.exists():
                content += build_file_context(path)
        prepared.append({"role": role, "content": content})
    return prepared, has_image


def _save_generated_bytes(data, content_type="image/jpeg"):
    """Save returned image bytes and return the local browser URL."""
    if not data:
        raise RuntimeError("سرویس تصویر هیچ داده‌ای برنگرداند.")
    content_type = (content_type or "image/jpeg").split(";")[0].lower()
    ext = {
        "image/png": ".png",
        "image/webp": ".webp",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
    }.get(content_type, ".jpg")
    name = f"img_{uuid.uuid4().hex}{ext}"
    target = GENERATED_DIR / name
    target.write_bytes(data)
    return "/generated/" + name


def _save_pollinations_json_image(payload):
    """Handle OpenAI-compatible image responses from Pollinations."""
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        raise RuntimeError("پاسخ Pollinations ساختار تصویر قابل استفاده ندارد.")
    item = items[0] if isinstance(items[0], dict) else {}

    b64 = item.get("b64_json")
    if isinstance(b64, str) and b64.strip():
        try:
            raw = base64.b64decode(b64, validate=False)
        except Exception as e:
            raise RuntimeError("تصویر دریافتی از Pollinations خراب بود.") from e
        return _save_generated_bytes(raw, "image/png")

    remote_url = item.get("url")
    if isinstance(remote_url, str) and remote_url.startswith(("http://", "https://")):
        rr = requests.get(
            remote_url,
            headers={"Authorization": f"Bearer {POLLINATIONS_API_KEY}", "Cache-Control": "no-cache"},
            timeout=120,
        )
        rr.raise_for_status()
        return _save_generated_bytes(rr.content, rr.headers.get("Content-Type"))

    raise RuntimeError("Pollinations تصویری در پاسخ برنگرداند.")


def edit_image_file(image_path, prompt):
    """Edit a user-supplied image through Pollinations' OpenAI-compatible edits API."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise RuntimeError("دستور ویرایش تصویر را بنویس.")
    if not POLLINATIONS_API_KEY:
        raise RuntimeError("برای ویرایش تصویر باید Pollinations API Key را در ترمینال وارد کنی.")
    if not image_path.exists() or not is_image_file(image_path):
        raise RuntimeError("فایل تصویر معتبر نیست.")
    if len(prompt) > 4000:
        prompt = prompt[:4000]

    headers = {
        "Authorization": f"Bearer {POLLINATIONS_API_KEY}",
        "Cache-Control": "no-cache",
        "X-Meraj-Request-ID": uuid.uuid4().hex,
    }
    # OpenAI-compatible multipart format: image + prompt + model.
    mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    try:
        with image_path.open("rb") as fh:
            files = {"image": (image_path.name, fh, mime)}
            data = {"model": "gptimage", "prompt": prompt}
            print("\n[IMAGE EDIT PROMPT]", repr(prompt))
            r = requests.post(
                "https://gen.pollinations.ai/v1/images/edits",
                headers=headers,
                files=files,
                data=data,
                timeout=180,
            )
        if not r.ok:
            print("[IMAGE EDIT ERROR]", r.status_code, r.text[:1000])
            raise requests.HTTPError(f"Pollinations image edit HTTP {r.status_code}", response=r)
        return _save_pollinations_json_image(r.json())
    except requests.HTTPError:
        raise
    except requests.RequestException:
        raise
    except ValueError as e:
        raise RuntimeError("پاسخ سرویس ویرایش تصویر قابل خواندن نبود.") from e


def upload_reference_to_pollinations(image_path):
    """Upload a local reference image so Pollinations can use it as image input."""
    if not image_path or not image_path.exists() or not is_image_file(image_path):
        raise RuntimeError("تصویر مرجع معتبر نیست.")
    mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    headers = {
        "Authorization": f"Bearer {POLLINATIONS_API_KEY}",
        "Cache-Control": "no-cache",
    }
    with image_path.open("rb") as fh:
        r = requests.post(
            "https://media.pollinations.ai/upload",
            headers=headers,
            files={"file": (image_path.name, fh, mime)},
            timeout=90,
        )
    r.raise_for_status()
    # Pollinations may return a JSON URL or a Link header depending on the deployment.
    try:
        data = r.json()
    except ValueError:
        data = {}
    for key in ("url", "media_url", "link"):
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    link = r.headers.get("Link", "")
    m = re.search(r"<([^>]+)>;\s*rel=[\"']enclosure[\"']", link)
    if m:
        return m.group(1)
    if r.text.strip().startswith("http"):
        return r.text.strip().split()[0]
    raise RuntimeError("نتوانستم آدرس تصویر مرجع را از سرویس تصویر دریافت کنم.")


def analyze_generated_image(local_url):
    """Send the generated image back to Groq Vision for a short verification."""
    if not VISION_MODEL or not isinstance(local_url, str) or not local_url.startswith("/generated/"):
        return ""
    filename = Path(local_url).name
    path = GENERATED_DIR / filename
    if not path.exists() or not is_image_file(path):
        return ""
    try:
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "این تصویر تازه ساخته شده است. خیلی کوتاه و دقیق بگو چه چیزهایی در آن دیده می‌شود و آیا با این درخواست هم‌خوانی دارد: " + getattr(analyze_generated_image, "_last_prompt", "")},
                {"type": "image_url", "image_url": {"url": image_data_url(path)}},
            ],
        }]
        return groq_chat(messages, use_vision=True)
    except Exception as e:
        print("[GENERATED IMAGE VISION ERROR]", repr(e))
        return ""


def generate_image_file(prompt, reference_image_path=None):
    """Generate creatively from the user's topic, optionally using an uploaded reference image."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise RuntimeError("توضیح تصویر خالی است.")
    if not POLLINATIONS_API_KEY:
        raise RuntimeError("برای ساخت تصویر باید Pollinations API Key را در ترمینال وارد کنی.")

    original_prompt = prompt
    if len(prompt) > 4000:
        prompt = prompt[:4000]

    # The user supplies the topic; the model is deliberately allowed to be creative
    # about scene details, composition, lighting, style and supporting details.
    # Explicit user constraints still have priority.
    creative_prompt = (
        "Create a rich, imaginative, polished image based on the user's requested topic. "
        "Use strong creative judgment for composition, environment, lighting, color, camera angle, "
        "depth, atmosphere, materials, visual storytelling and tasteful supporting details that naturally "
        "fit the requested topic. Do not require the user to specify every detail. Expand a short idea into "
        "a complete, visually compelling scene. However, never contradict an explicit instruction from the user, "
        "never change requested text, and never replace the main subject or requested identity. "
        "If a reference image is provided, use it as visual guidance and preserve the important identity, "
        "shape, subject and distinctive features unless the user explicitly asks to change them. "
        "Creative freedom means artistic elaboration of the requested idea, not ignoring the user's intent. "
        "User request: " + prompt
    )

    # gptimage is preferred for instruction-following and text/layout work.
    image_model = "gptimage"
    headers = {
        "Authorization": f"Bearer {POLLINATIONS_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "X-Meraj-Request-ID": uuid.uuid4().hex,
    }
    payload = {"model": image_model, "prompt": creative_prompt, "response_format": "b64_json", "size": "1024x1024", "quality": "high"}
    if reference_image_path:
        payload["image"] = upload_reference_to_pollinations(reference_image_path)

    print("\n[IMAGE GEN USER PROMPT]", repr(original_prompt))
    print("[IMAGE GEN CREATIVE PROMPT]", repr(creative_prompt))
    try:
        r = requests.post(
            "https://gen.pollinations.ai/v1/images/generations",
            headers=headers,
            json=payload,
            timeout=180,
        )
        if r.ok:
            return _save_pollinations_json_image(r.json())
        print("[IMAGE GEN POST ERROR]", r.status_code, r.text[:800])
    except requests.RequestException as e:
        print("[IMAGE GEN POST CONNECTION ERROR]", repr(e))
    except ValueError as e:
        print("[IMAGE GEN POST JSON ERROR]", repr(e))

    encoded_prompt = quote(creative_prompt, safe="")
    fallback_url = "https://gen.pollinations.ai/image/" + encoded_prompt
    fallback_params = {
        "model": image_model,
        "width": 1024,
        "height": 1024,
        "nologo": "true",
        "seed": uuid.uuid4().int % 2147483647,
    }
    if reference_image_path:
        fallback_params["image"] = upload_reference_to_pollinations(reference_image_path)
    r = requests.get(
        fallback_url,
        params=fallback_params,
        headers={
            "Authorization": f"Bearer {POLLINATIONS_API_KEY}",
            "Accept": "image/*",
            "Cache-Control": "no-cache",
            "X-Meraj-Request-ID": uuid.uuid4().hex,
        },
        timeout=180,
    )
    r.raise_for_status()
    return _save_generated_bytes(r.content, r.headers.get("Content-Type"))


HTML_PAGE = r'''<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Taha's Helper Bot</title>
<style>
*{box-sizing:border-box}
:root{--bg:#0b0b0f;--line:#2a2a33;--text:#f4f4f5;--muted:#a1a1aa;--bubble:#202024;--accent:#fff}
html,body{margin:0;height:100%;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Tahoma,Arial,sans-serif}
body{overflow:hidden}button{font:inherit;color:inherit}
.app{height:100dvh;display:flex;background:var(--bg)}
.sidebar{width:290px;background:#101014;border-left:1px solid var(--line);display:flex;flex-direction:column;padding:12px;gap:10px;position:fixed;right:0;top:0;bottom:0;z-index:20;transform:translateX(100%);transition:.22s ease}
.sidebar.open{transform:translateX(0)}.side-top{display:flex;align-items:center;gap:8px}
.newchat{flex:1;border:1px solid #303039;background:#1b1b21;border-radius:12px;padding:12px;cursor:pointer;text-align:right}
.close-side,.iconbtn{width:44px;height:44px;border:0;border-radius:12px;background:transparent;cursor:pointer;font-size:23px;display:grid;place-items:center}
.close-side:hover,.iconbtn:hover,.newchat:hover{background:#23232a}
.history{overflow:auto;flex:1;padding-top:6px}.history-title{font-size:12px;color:var(--muted);padding:10px 8px}
.history-item{border:0;background:transparent;width:100%;padding:11px 10px;border-radius:10px;text-align:right;cursor:pointer;color:#ddd;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:3px}
.history-item:hover,.history-item.active{background:#24242b}.history-empty{font-size:13px;color:#666;padding:12px 8px}
.main{height:100%;width:100%;display:flex;flex-direction:column}
.topbar{height:64px;display:flex;align-items:center;justify-content:space-between;padding:8px 12px;border-bottom:1px solid #202027;background:rgba(11,11,15,.94);backdrop-filter:blur(14px);position:relative;z-index:5}
.top-right,.top-left{display:flex;align-items:center;gap:6px}.brand{font-weight:700;font-size:17px}.model{font-size:11px;color:var(--muted);margin-right:6px;max-width:230px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.menu{position:absolute;left:12px;top:54px;width:220px;background:#1a1a20;border:1px solid #30303a;border-radius:14px;padding:6px;display:none;box-shadow:0 18px 45px #0008;z-index:30}
.menu.open{display:block}.menu button{width:100%;border:0;background:transparent;padding:12px;text-align:right;border-radius:9px;cursor:pointer}.menu button:hover{background:#292930}
.messages{flex:1;overflow:auto;padding:22px 14px 150px}.empty{height:100%;display:grid;place-items:center;text-align:center;color:#eee}.empty h1{font-size:30px;margin:0 0 10px}.empty p{color:var(--muted);margin:0}
.msg{max-width:820px;margin:0 auto 18px;display:flex;gap:12px;align-items:flex-start}.avatar{width:32px;height:32px;border-radius:9px;background:#23232a;display:grid;place-items:center;flex:none}.msg.user .avatar{background:#2d2d35}
.bubble{white-space:pre-wrap;line-height:1.8;font-size:15px;flex:1;padding-top:3px}.msg.assistant .bubble{background:var(--bubble);padding:12px 14px;border-radius:15px}
.composer-wrap{position:fixed;left:0;right:0;bottom:0;padding:12px 14px 16px;background:linear-gradient(transparent,var(--bg) 22%);z-index:10}
.composer{max-width:820px;margin:auto;background:#1d1d22;border:1px solid #35353e;border-radius:18px;display:flex;align-items:flex-end;gap:8px;padding:8px}
textarea{flex:1;resize:none;border:0;outline:0;background:transparent;color:white;min-height:44px;max-height:160px;padding:11px;font-size:16px;line-height:1.5}
.send,.attach,.mic,.imagegen{width:44px;height:44px;border:0;border-radius:13px;cursor:pointer;display:grid;place-items:center}.send{background:#fff;color:#111;font-size:20px}.attach,.mic,.imagegen{background:#292930}.imagegen.active{background:#3b2f70}.mic.recording{background:#7f1d1d;animation:pulse 1s infinite}.send:disabled,.mic:disabled{opacity:.35}.file-input{display:none}
.status{max-width:820px;margin:6px auto 0;color:#888;font-size:11px;padding:0 8px}.file-pill{max-width:820px;margin:0 auto 7px;color:#bbb;font-size:12px;padding:0 8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.msg-actions{display:flex;gap:6px;margin-top:8px}.msg-action{border:1px solid #34343d;background:#18181d;color:#bcbcc5;border-radius:9px;padding:5px 9px;font-size:11px;cursor:pointer}.msg-action:hover{background:#292930;color:#fff}.sources{margin-top:9px;padding-top:8px;border-top:1px solid #34343d;font-size:12px}.sources-title{color:#aaa;margin-bottom:5px}.source-link{display:block;color:#b9b9c5;text-decoration:none;padding:3px 0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.source-link:hover{color:#fff;text-decoration:underline}.web-badge{display:inline-block;font-size:10px;color:#aaa;background:#17171c;border:1px solid #303039;border-radius:7px;padding:2px 6px;margin-bottom:7px}
.image-panel{max-width:820px;margin:0 auto 7px;display:none;padding:8px;background:#17171c;border:1px solid #30303a;border-radius:14px;gap:7px;align-items:center}.image-panel.open{display:flex}.image-panel input{flex:1;min-width:0;background:#0f0f13;border:1px solid #30303a;color:#fff;border-radius:10px;padding:10px;outline:none}.image-panel button{border:1px solid #34343d;background:#25252c;color:#fff;border-radius:10px;padding:9px 12px;cursor:pointer}.generated-image{max-width:100%;border-radius:14px;margin-top:10px;display:block}.image-tools{display:flex;gap:7px;margin-top:8px;align-items:center}.image-download{text-decoration:none!important;display:inline-flex!important;align-items:center;justify-content:center}.image-caption{font-size:12px;color:#aaa;margin-top:6px}.file-tools{display:flex;gap:7px;margin-top:9px;align-items:center;flex-wrap:wrap}.file-download{text-decoration:none!important}.code-block{margin:12px 0 4px;background:#0d0d10;border:1px solid #30303a;border-radius:12px;overflow:hidden;direction:ltr;text-align:left}.code-head{display:flex;align-items:center;justify-content:space-between;padding:7px 9px;background:#17171c;border-bottom:1px solid #30303a;color:#aaa;font-size:11px}.code-copy{border:1px solid #34343d;background:#25252c;color:#ddd;border-radius:7px;padding:4px 8px;font-size:11px;cursor:pointer}.code-block code{display:block;white-space:pre;overflow:auto;padding:12px;font:13px/1.65 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#eee}.cancel-btn{display:none;border:1px solid #4a2d2d;background:#241719;color:#ddd;border-radius:9px;padding:4px 8px;font-size:11px;cursor:pointer}.cancel-btn.show{display:inline-block}

.overlay{display:none;position:fixed;inset:0;background:#0007;z-index:15}
.overlay.open{display:block}@keyframes pulse{50%{opacity:.55}}
@media(min-width:900px){.sidebar{position:relative;transform:none;order:2;border-left:1px solid var(--line)}.close-side{display:none}.overlay{display:none!important}.main{width:calc(100% - 290px)}}
</style>
</head>
<body>
<div class="app">
<aside class="sidebar" id="sidebar">
 <div class="side-top"><button class="newchat" onclick="newChat()">＋ چت جدید</button><button class="close-side" onclick="toggleSide()">×</button></div>
 <div class="history"><div class="history-title">تاریخچه</div><div id="history"></div></div>
</aside>
<div class="overlay" id="overlay" onclick="toggleSide()"></div>
<main class="main">
<header class="topbar">
 <div class="top-right"><button class="iconbtn" onclick="toggleSide()">☰</button><div><div class="brand">Taha's Helper Bot</div><div class="model" id="modelLabel">در حال اتصال...</div></div></div>
 <div class="top-left"><button class="iconbtn" onclick="newChat()">✎</button><button class="iconbtn" onclick="toggleMenu()">⋮</button></div>
 <div class="menu" id="menu"><form method="post" action="/logout"><button type="submit">🚪 خروج از حساب</button></form><button onclick="newChat();toggleMenu()">چت جدید</button><button onclick="clearChat();toggleMenu()">پاک کردن این چت</button><button onclick="showAbout();toggleMenu()">درباره</button><button onclick="alert('جستجوی وب برای پرسش‌های خبری و به‌روز به‌صورت خودکار فعال است.')">🌐 جستجوی وب</button></div>
</header>
<section class="messages" id="messages"><div class="empty"><div><h1>Taha's Helper Bot</h1><p>هر چیزی می‌خواهی بنویس…</p></div></div></section>
<div class="composer-wrap">
 <div class="image-panel" id="imagePanel"><input id="imagePrompt" placeholder="موضوع تصویر را بنویس؛ اگر عکس آپلود کرده‌ای، همان عکس هم به‌عنوان مرجع استفاده می‌شود"><button onclick="generateImage()">ساخت تصویر</button><button onclick="toggleImagePanel()">×</button></div> <div class="file-pill" id="filePill"></div>
 <div class="composer">
  <input id="file" class="file-input" type="file" accept="*/*" onchange="filePicked(this)">
  <button class="attach" title="ارسال فایل" onclick="document.getElementById('file').click()">📎</button>
  <button class="imagegen" id="imageGenBtn" title="ساخت تصویر" onclick="toggleImagePanel()">🎨</button>
  <textarea id="input" placeholder="پیامت را بنویس..." rows="1"></textarea>
  <button class="mic" id="mic" title="ضبط صدا" onclick="toggleRecording()">🎙️</button>
  <button class="send" id="send" title="ارسال" onclick="sendMessage()">↑</button>
 </div>
 <div class="status" id="status">آماده <button id="cancelBtn" class="cancel-btn" onclick="cancelRequest()">توقف</button></div>
</div>
</main>
</div>
<script>
let messages=[],chatBusy=false,imageBusy=false,currentChatId=null,mediaRecorder=null,audioChunks=[],recording=false,chatController=null,lastUploadedImageName="";
const input=document.getElementById('input');

function escapeHtml(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function addMessage(role,content,meta={}){
 messages.push({role,content:String(content??''),...(meta||{})});
 render();
}
function renderContent(text){
 const frag=document.createDocumentFragment();
 const re=/```([^\\n`]*)\\n([\\s\\S]*?)```/g;
 let last=0,m;
 while((m=re.exec(text))){
  if(m.index>last) frag.appendChild(document.createTextNode(text.slice(last,m.index)));
  const block=document.createElement('div');block.className='code-block';
  const head=document.createElement('div');head.className='code-head';
  const lang=document.createElement('span');lang.textContent=(m[1]||'code').trim()||'code';
  const cp=document.createElement('button');cp.className='code-copy';cp.textContent='کپی کد';
  cp.onclick=async()=>{try{await navigator.clipboard.writeText(m[2]);cp.textContent='کپی شد ✓';setTimeout(()=>cp.textContent='کپی کد',1200)}catch(e){cp.textContent='کپی نشد'}};
  head.append(lang,cp);block.appendChild(head);
  const code=document.createElement('code');code.textContent=m[2];block.appendChild(code);frag.appendChild(block);last=m.index+m[0].length;
 }
 if(last<text.length) frag.appendChild(document.createTextNode(text.slice(last)));
 return frag;
}
function render(){
 const box=document.getElementById('messages');box.innerHTML='';
 if(!messages.length){box.innerHTML='<div class="empty"><div><h1>Taha's Helper Bot</h1><p>هر چیزی می‌خواهی بنویس…</p></div></div>';return;}
 for(const m of messages){
  const row=document.createElement('div');row.className='msg '+(m.role==='user'?'user':'assistant');
  const av=document.createElement('div');av.className='avatar';av.textContent=m.role==='user'?'ش':'م';
  const wrap=document.createElement('div');wrap.className='bubble';
  if(m.image_url){
   if(m.content) wrap.appendChild(renderContent(m.content));
   const img=document.createElement('img');img.className='generated-image';img.src=m.image_url;img.alt=m.image_prompt||'تصویر ساخته‌شده';img.loading='lazy';wrap.appendChild(img);
   const tools=document.createElement('div');tools.className='image-tools';
   const dl=document.createElement('a');dl.className='msg-action image-download';dl.href=m.image_url;dl.download='meraj-generated-image.png';dl.target='_blank';dl.textContent='⬇️ دریافت تصویر';tools.appendChild(dl);
   const cp=document.createElement('button');cp.className='msg-action';cp.textContent='کپی لینک';cp.onclick=async()=>{try{await navigator.clipboard.writeText(m.image_url);cp.textContent='کپی شد ✓';setTimeout(()=>cp.textContent='کپی لینک',1200)}catch(e){}};tools.appendChild(cp);wrap.appendChild(tools);
  }else{
   wrap.appendChild(renderContent(String(m.content||'')));
  }
  if(m.file_url){const ft=document.createElement('div');ft.className='file-tools';const a=document.createElement('a');a.className='msg-action file-download';a.href=m.file_url;a.download=m.file_name||'';a.textContent='📁 دریافت فایل'+(m.file_name?' — '+m.file_name:'');ft.appendChild(a);wrap.appendChild(ft);}
  if(Array.isArray(m.sources)&&m.sources.length){const src=document.createElement('div');src.className='sources';const title=document.createElement('div');title.className='sources-title';title.textContent='منابع وب';src.appendChild(title);m.sources.forEach((x,i)=>{const a=document.createElement('a');a.className='source-link';a.href=x.url||'#';a.target='_blank';a.rel='noopener noreferrer';a.textContent='['+(i+1)+'] '+(x.title||x.url||'منبع');src.appendChild(a)});wrap.appendChild(src);}
  const actions=document.createElement('div');actions.className='msg-actions';
  const copy=document.createElement('button');copy.className='msg-action';copy.textContent='کپی';copy.onclick=async()=>{try{await navigator.clipboard.writeText(String(m.content||''));copy.textContent='کپی شد ✓';setTimeout(()=>copy.textContent='کپی',1200)}catch(e){}};
  const share=document.createElement('button');share.className='msg-action';share.textContent='اشتراک‌گذاری';share.onclick=async()=>{try{if(navigator.share)await navigator.share({text:String(m.content||'')});else await navigator.clipboard.writeText(String(m.content||''));}catch(e){}};
  actions.append(copy,share);wrap.appendChild(actions);
  row.append(av,wrap);box.appendChild(row);
 }
 box.scrollTop=box.scrollHeight;
}
function newChat(){messages=[];currentChatId=null;lastUploadedImageName='';render();input.value='';document.getElementById('file').value='';document.getElementById('filePill').textContent='';input.focus();document.getElementById('status').textContent='آماده';loadHistory().catch(()=>{});}
async function clearChat(){if(!currentChatId){newChat();return;}try{await fetch('/api/chats/'+encodeURIComponent(currentChatId),{method:'DELETE'});}catch(e){}newChat();}
function showAbout(){alert('Taha's Helper Bot\nدستیار هوش مصنوعی Flask');}
function filePicked(el){const f=el.files&&el.files[0];lastUploadedImageName=(f&&f.type&&f.type.startsWith('image/'))?'__pending__':'';document.getElementById('filePill').textContent=f?'📎 '+f.name:'';}
function setBusy(v){setChatBusy(v);}
input.addEventListener('input',()=>{input.style.height='auto';input.style.height=Math.min(input.scrollHeight,160)+'px'});
input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMessage()}});
function toggleSide(){document.getElementById('sidebar').classList.toggle('open');document.getElementById('overlay').classList.toggle('open')}
function toggleMenu(){document.getElementById('menu').classList.toggle('open')}
function toggleImagePanel(){const p=document.getElementById('imagePanel');const b=document.getElementById('imageGenBtn');if(!p||!b)return; p.classList.toggle('open');b.classList.toggle('active');if(p.classList.contains('open')){const inp=document.getElementById('imagePrompt');if(inp)inp.focus();}}
function cancelRequest(){if(chatController){chatController.abort();document.getElementById('status').textContent='درخواست متوقف شد';}}
function setChatBusy(v){chatBusy=v;document.getElementById('send').disabled=v;document.getElementById('mic').disabled=v;document.getElementById('cancelBtn').classList.toggle('show',v);if(!v&&!imageBusy)document.getElementById('status').textContent='آماده';}
function setImageBusy(v){imageBusy=v;const panel=document.getElementById('imagePanel');if(panel){const btn=panel.querySelector('button');if(btn)btn.disabled=v;}if(!v&&!chatBusy)document.getElementById('status').textContent='آماده';}
async function saveLocalState(){try{if(messages.length) localStorage.setItem('meraj_last_chat',JSON.stringify({id:currentChatId,messages}));}catch(e){}}
async function sendMessage(){
 if(chatBusy)return;
 const text=input.value.trim();
 const file=document.getElementById('file').files[0];
 if(!text && !file)return;
 setChatBusy(true);
 let shownText=text;
 chatController=new AbortController();
 const controller=chatController;
 const timer=setTimeout(()=>controller.abort(),90000);
 try{
  if(file){
   document.getElementById('status').textContent='در حال خواندن فایل…';
   const fd=new FormData();fd.append('file',file);
   const ur=await fetch('/api/upload',{method:'POST',body:fd,signal:controller.signal});
   const ud=await ur.json().catch(()=>({}));if(!ur.ok)throw new Error(ud.error||('خطا در ارسال فایل ('+ur.status+')'));if(!ud.name)throw new Error('سرور فایل را دریافت نکرد. دوباره انتخابش کن.');
   const marker=ud.kind==='image'?'[تصویر پیوست شد: '+ud.name+']':'[فایل پیوست شد: '+ud.name+']'; if(ud.kind==='image')lastUploadedImageName=ud.name;
   shownText=(shownText?shownText+'\n\n':'')+marker;document.getElementById('file').value='';document.getElementById('filePill').textContent='';
  }
  addMessage('user',shownText);input.value='';input.style.height='auto';
  const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:currentChatId,messages}),signal:controller.signal});
  const data=await r.json().catch(()=>({}));if(!r.ok)throw new Error(data.error||'خطای سرور');
  currentChatId=data.chat_id||currentChatId;addMessage('assistant',data.reply||'پاسخی دریافت نشد.',{sources:data.sources||[],file_url:data.file_url||'',file_name:data.file_name||''});
  document.getElementById('modelLabel').textContent=data.model||'';
  // مهم: قفل ارسال همین‌جا آزاد می‌شود؛ ذخیره‌سازی تاریخچه نباید ارسال پیام بعدی را متوقف کند.
  setChatBusy(false);
  loadHistory().catch(()=>{});saveLocalState().catch(()=>{});
 }catch(e){
  if(e.name==='AbortError')addMessage('assistant','⏱️ پاسخ طول کشید و متوقف شد. دوباره ارسال کن.');
  else addMessage('assistant','❌ '+e.message);
 }finally{clearTimeout(timer);if(chatController===controller)chatController=null;setChatBusy(false);input.focus();}
}
async function generateImage(){
 const p=document.getElementById('imagePrompt').value.trim();
 if(imageBusy)return;
 if(!p){document.getElementById('status').textContent='توضیح تصویر را بنویس.';return}
 setImageBusy(true);document.getElementById('status').textContent='🎨 در حال ساخت تصویر…';
 const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),60000);
 try{
  const r=await fetch('/api/generate-image',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:p,image_name:lastUploadedImageName||''}),signal:controller.signal});
  const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.error||'ساخت تصویر ناموفق بود');
  messages.push({role:'assistant',content:'تصویر ساخته شد\n\nپرامپت: '+p+(d.vision_analysis?'\n\n🔎 بررسی Groq Vision:\n'+d.vision_analysis:''),image_url:d.url,image_prompt:p,sources:[]});render();
  const ip=document.getElementById('imagePrompt');if(ip)ip.value='';const panel=document.getElementById('imagePanel');const genBtn=document.getElementById('imageGenBtn');if(panel)panel.classList.remove('open');if(genBtn)genBtn.classList.remove('active');
  if(!currentChatId)currentChatId=crypto.randomUUID?crypto.randomUUID():String(Date.now());
  // تصویر مستقل از چت است؛ بعد از آماده‌شدن تصویر، ارسال پیام متنی همچنان آزاد است.
  setImageBusy(false);
  fetch('/api/save-image',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:currentChatId,messages})}).catch(()=>{});
  loadHistory().catch(()=>{});
 }catch(e){document.getElementById('status').textContent=e.name==='AbortError'?'ساخت تصویر زمان زیادی برد. دوباره امتحان کن.':'خطا در ساخت تصویر: '+e.message}
 finally{clearTimeout(timer);setImageBusy(false);input.focus();}
}

async function loadHistory(){
 const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),8000);
 try{const r=await fetch('/api/chats',{cache:'no-store',signal:controller.signal});const d=await r.json().catch(()=>({chats:[]}));if(!r.ok)throw new Error(d.error||'history');const h=document.getElementById('history');h.innerHTML='';
  if(!Array.isArray(d.chats)||!d.chats.length){h.innerHTML='<div class="history-empty">هنوز چتی ذخیره نشده</div>';return}
  d.chats.forEach(c=>{const b=document.createElement('button');b.className='history-item'+(c.id===currentChatId?' active':'');b.textContent=c.title||'چت جدید';b.onclick=()=>openChat(c.id);h.appendChild(b)})
 }catch(e){}finally{clearTimeout(timer)}
}
async function openChat(id){
 try{const r=await fetch('/api/chats/'+encodeURIComponent(id));const d=await r.json();if(!r.ok)throw new Error(d.error);currentChatId=id;messages=d.messages||[];render();document.getElementById('status').textContent='ذخیره شد';loadHistory();if(window.innerWidth<900)toggleSide();}
 catch(e){alert('خطا در باز کردن تاریخچه: '+e.message)}
}
async function toggleRecording(){
 if(recording){
  try{mediaRecorder.stop();}catch(e){finishRecordingFallback();}
  return;
 }
 // Prefer the browser's speech recognition on Android Chrome when available.
 // It puts Persian text directly in the input. If unavailable, fall back to
 // recording WebM and sending it to Groq Whisper through Flask.
 const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
 if(SR){
  try{
   const rec=new SR();
   rec.lang='fa-IR';rec.continuous=false;rec.interimResults=true;rec.maxAlternatives=1;
   recording=true;
   const btn=document.getElementById('mic');btn.classList.add('recording');btn.textContent='⏹️';
   document.getElementById('status').textContent='🎙️ در حال شنیدن… صحبت کن';
   let finalText='';
   rec.onresult=e=>{
    let interim='';
    for(let i=e.resultIndex;i<e.results.length;i++){
     const t=e.results[i][0].transcript;
     if(e.results[i].isFinal) finalText+=t+' '; else interim+=t;
    }
    input.value=(finalText+interim).trim();input.dispatchEvent(new Event('input'));
   };
   rec.onerror=e=>{
    recording=false;btn.classList.remove('recording');btn.textContent='🎙️';
    // Only fall back to MediaRecorder for errors that indicate speech recognition isn't usable.
    if(e.error==='not-allowed'||e.error==='service-not-allowed'){
     document.getElementById('status').textContent='دسترسی میکروفون مجاز نیست';
    }else{
     document.getElementById('status').textContent='تبدیل صدا ناموفق بود؛ حالت جایگزین را امتحان کن';
    }
   };
   rec.onend=()=>{
    recording=false;btn.classList.remove('recording');btn.textContent='🎙️';
    if(input.value.trim()) document.getElementById('status').textContent='متن آماده است؛ برای ارسال ↑ را بزن';
    else document.getElementById('status').textContent='آماده';
   };
   window.__merajSpeechRec=rec;rec.start();return;
  }catch(e){recording=false;}
 }
 // Fallback: actual audio recording -> Groq Whisper.
 if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia||!window.MediaRecorder){
  alert('این مرورگر قابلیت ضبط صدا را ندارد. Chrome را با آدرس localhost:5000 باز کن.');return;
 }
 try{
  const stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true}});
  audioChunks=[];
  const types=['audio/webm;codecs=opus','audio/webm','audio/ogg;codecs=opus','audio/mp4'];
  const mime=types.find(t=>MediaRecorder.isTypeSupported(t))||'';
  mediaRecorder=mime?new MediaRecorder(stream,{mimeType:mime}):new MediaRecorder(stream);
  recording=true;window.__recordingStream=stream;
  const btn=document.getElementById('mic');btn.classList.add('recording');btn.textContent='⏹️';
  document.getElementById('status').textContent='🎙️ در حال ضبط… دوباره بزن تا تمام شود';
  mediaRecorder.ondataavailable=e=>{if(e.data&&e.data.size)audioChunks.push(e.data)};
  mediaRecorder.onerror=e=>{console.error(e);document.getElementById('status').textContent='خطا در ضبط صدا'};
  mediaRecorder.onstop=async()=>{
   recording=false;btn.classList.remove('recording');btn.textContent='🎙️';stream.getTracks().forEach(t=>t.stop());
   const actualType=mediaRecorder.mimeType||mime||'audio/webm';
   const ext=actualType.includes('ogg')?'ogg':actualType.includes('mp4')?'mp4':'webm';
   const blob=new Blob(audioChunks,{type:actualType});
   if(blob.size<1000){document.getElementById('status').textContent='صدای کافی ضبط نشد؛ کمی بیشتر صحبت کن.';return;}
   await transcribe(blob,'voice.'+ext);
  };
  mediaRecorder.start(250);
 }catch(e){
  recording=false;document.getElementById('mic').classList.remove('recording');document.getElementById('mic').textContent='🎙️';
  alert('دسترسی میکروفون داده نشد: '+e.message);
 }
}
function finishRecordingFallback(){
 recording=false;document.getElementById('mic').classList.remove('recording');document.getElementById('mic').textContent='🎙️';
 if(window.__recordingStream)window.__recordingStream.getTracks().forEach(t=>t.stop());
}
async function transcribe(blob,filename){
 setBusy(true);document.getElementById('status').textContent='در حال تبدیل صدا به متن…';
 try{
  const fd=new FormData();fd.append('audio',blob,filename||'voice.webm');
  const r=await fetch('/api/transcribe',{method:'POST',body:fd});
  let d={};try{d=await r.json()}catch(e){throw new Error('پاسخ نامعتبر از سرور دریافت شد')}
  if(!r.ok)throw new Error(d.error||'خطا در تبدیل صدا');
  const text=(d.text||'').trim();
  if(!text)throw new Error('صدایی به متن تبدیل نشد. واضح‌تر و کمی طولانی‌تر صحبت کن.');
  input.value=text;input.dispatchEvent(new Event('input'));
  setBusy(false);document.getElementById('status').textContent='متن آماده است؛ برای ارسال ↑ را بزن';input.focus();
 }catch(e){setBusy(false);document.getElementById('status').textContent='آماده';alert('❌ '+e.message)}
}

async function loadInfo(){
 try{
  const r=await fetch('/health',{cache:'no-store'});
  const d=await r.json().catch(()=>({}));
  if(!r.ok) throw new Error('health '+r.status);
  document.getElementById('modelLabel').textContent=d.model||'مدل آماده';
 }catch(e){
  // /health is public, so a session/auth problem cannot leave the UI
  // permanently stuck on "در حال اتصال...".
  const el=document.getElementById('modelLabel');
  if(el) el.textContent='مدل آماده';
 }
}
loadInfo();loadHistory();
</script>
</body>
</html>'''


@app.get("/")
def home():
    if not read_account():
        return redirect(url_for("register_page"))
    if not session.get("authenticated"):
        return redirect(url_for("login_page"))
    return HTML_PAGE


@app.get("/register")
def register_page():
    # Always allow opening the registration page directly.
    # If an account already exists, show the login page instead of creating another account.
    if read_account():
        return auth_page("login")
    return auth_page("register")


@app.post("/register")
def register_submit():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    password2 = request.form.get("password2") or ""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username):
        return auth_page("register", "نام کاربری باید ۳ تا ۴۰ کاراکتر و فقط شامل حروف انگلیسی، عدد، نقطه، خط تیره یا زیرخط باشد.")
    if len(password) < 6:
        return auth_page("register", "رمز عبور باید حداقل ۶ کاراکتر باشد.")
    if password != password2:
        return auth_page("register", "تکرار رمز عبور یکسان نیست.")
    try:
        write_account(username, password)
    except Exception as e:
        print("[ACCOUNT WRITE ERROR]", repr(e))
        return auth_page("register", "ساخت حساب انجام نشد. دوباره تلاش کن.")
    return redirect(url_for("login_page"))


@app.get("/login")
def login_page():
    # Never redirect /login back to /register just because the account file
    # is temporarily unavailable (for example after a Render restart).
    if session.get("authenticated"):
        return redirect(url_for("home"))
    return auth_page("login")


@app.post("/login")
def login_submit():
    account = read_account()
    password = request.form.get("password") or ""
    if not account:
        return auth_page("login", "حسابی روی این سرور پیدا نشد. اگر سرویس Render ری‌استارت شده، باید دوباره ثبت‌نام کنی.")
    if not check_password_hash(account.get("password_hash", ""), password):
        return auth_page("login", "رمز عبور اشتباه است.")
    session.clear()
    session["authenticated"] = True
    return redirect(url_for("home"))


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


@app.get("/health")
def health():
    return jsonify(ok=True, model=MODEL, vision_model=VISION_MODEL, transcription_model=TRANSCRIBE_MODEL, image_generation=IMAGE_GEN_PROVIDER)


@app.get("/api/info")
@login_required
def info():
    return jsonify(ok=True, model=MODEL, vision_model=VISION_MODEL, transcription_model=TRANSCRIBE_MODEL, image_generation=IMAGE_GEN_PROVIDER)


@app.get("/api/chats")
@login_required
def list_chats():
    data = read_history()
    chats = []
    for c in data.values():
        chats.append({
            "id": c.get("id"),
            "title": c.get("title", "چت جدید"),
            "updated_at": c.get("updated_at", ""),
        })
    chats.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return jsonify(chats=chats)


@app.get("/api/chats/<chat_id>")
@login_required
def get_chat(chat_id):
    data = read_history()
    chat = data.get(chat_id)
    if not chat:
        return jsonify(error="این چت پیدا نشد."), 404
    return jsonify(
        id=chat_id,
        title=chat.get("title", "چت جدید"),
        messages=chat.get("messages", []),
    )


@app.delete("/api/chats/<chat_id>")
@login_required
def delete_chat(chat_id):
    data = read_history()
    if chat_id in data:
        del data[chat_id]
        write_history(data)
    return jsonify(ok=True)


@app.post("/api/chat")
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    messages = data.get("messages") or []
    chat_id = data.get("chat_id") or str(uuid.uuid4())
    if not messages:
        return jsonify(error="پیام خالی است."), 400

    try:
        # Keep the server-side history independent from the browser refresh.
        normalized = []
        for m in messages:
            if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
                item = {"role": m["role"], "content": m["content"]}
                if isinstance(m.get("sources"), list): item["sources"] = m.get("sources")
                if isinstance(m.get("image_url"), str): item["image_url"] = m.get("image_url")
                if isinstance(m.get("image_prompt"), str): item["image_prompt"] = m.get("image_prompt")
                if isinstance(m.get("file_url"), str): item["file_url"] = m.get("file_url")
                if isinstance(m.get("file_name"), str): item["file_name"] = m.get("file_name")
                normalized.append(item)
        if not normalized:
            return jsonify(error="پیام قابل پردازش نیست."), 400

        prepared, has_image = prepare_messages(normalized)
        sources = []
        last_user = next((m.get("content", "") for m in reversed(normalized) if m.get("role") == "user"), "")
        if isinstance(last_user, str) and should_search_web(last_user) and not has_image:
            sources = search_web(last_user, 5)
            if sources:
                prepared.insert(0, {"role": "system", "content": (
                    "برای پرسش‌های به‌روز از نتایج وب زیر استفاده کن. در صورت استفاده، شماره منبع را مثل [1] داخل پاسخ بنویس. منبع جدید جعل نکن.\n\n" + web_context(sources)
                )})
        file_requested = is_file_request(last_user)
        if file_requested:
            prepared.insert(0, {"role": "system", "content": requested_file_instruction(last_user)})
        reply = groq_chat(prepared, use_vision=has_image)
        file_info = save_requested_file(last_user, reply) if file_requested else None
        assistant_item = {"role": "assistant", "content": reply, "sources": sources}
        if file_info:
            assistant_item["file_url"] = file_info["url"]
            assistant_item["file_name"] = file_info["name"]
        normalized.append(assistant_item)
        save_chat(chat_id, normalized, make_title(normalized))
        return jsonify(reply=reply, model=(VISION_MODEL if has_image and VISION_MODEL else MODEL), chat_id=chat_id, sources=sources, file_url=(file_info["url"] if file_info else None), file_name=(file_info["name"] if file_info else None))
    except Exception as e:
        print("\n[CHAT ERROR]", repr(e))
        return jsonify(error=str(e)), 500


@app.post("/api/upload")
@login_required
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="فایلی انتخاب نشده."), 400
    name = Path(f.filename).name
    if not name:
        return jsonify(error="نام فایل نامعتبر است."), 400
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_name = f"{stamp}_{name}"
    target = UPLOAD_DIR / safe_name
    f.save(target)
    kind = "image" if is_image_file(target) else "file"
    return jsonify(ok=True, name=safe_name, original_name=name, kind=kind)


@app.get("/generated/<path:filename>")
@login_required
def generated_file(filename):
    return send_from_directory(GENERATED_DIR, filename)


@app.get("/generated-download/<path:filename>")
@login_required
def generated_download(filename):
    # Force a real file download on Android/mobile browsers.
    safe = (GENERATED_DIR / filename).resolve()
    try:
        safe.relative_to(GENERATED_DIR.resolve())
    except ValueError:
        return jsonify(error="فایل نامعتبر است."), 400
    if not safe.is_file():
        return jsonify(error="فایل پیدا نشد."), 404
    return send_file(safe, as_attachment=True, download_name=safe.name)


@app.get("/generated-download-file/<path:filename>")
@login_required
def generated_download_file(filename):
    safe = (FILES_DIR / filename).resolve()
    try:
        safe.relative_to(FILES_DIR.resolve())
    except ValueError:
        return jsonify(error="فایل نامعتبر است."), 400
    if not safe.is_file():
        return jsonify(error="فایل پیدا نشد."), 404
    return send_file(safe, as_attachment=True, download_name=(safe.name.split("_", 2)[-1] or safe.name))


@app.post("/api/generate-image")
@login_required
def generate_image():
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    image_name = Path(str(data.get("image_name") or "")).name
    reference_path = UPLOAD_DIR / image_name if image_name else None
    if not prompt:
        return jsonify(error="توضیح تصویر را بنویس."), 400
    if reference_path and (not reference_path.exists() or not is_image_file(reference_path)):
        reference_path = None
    try:
        image_url = generate_image_file(prompt, reference_path)
        analyze_generated_image._last_prompt = prompt
        analysis = analyze_generated_image(image_url)
        return jsonify(ok=True, url=image_url, provider=IMAGE_GEN_PROVIDER, vision_analysis=analysis)
    except requests.HTTPError as e:
        detail = getattr(e.response, "text", "")[:500] if getattr(e, "response", None) is not None else str(e)
        print("\n[IMAGE GEN HTTP ERROR]", detail)
        return jsonify(error="سرویس ساخت تصویر خطا داد. کلید Pollinations و اینترنت را بررسی کن."), 502
    except requests.RequestException as e:
        print("\n[IMAGE GEN CONNECTION ERROR]", repr(e))
        return jsonify(error="اتصال به سرویس ساخت تصویر برقرار نشد. دوباره تلاش کن."), 502
    except Exception as e:
        print("\n[IMAGE GEN ERROR]", repr(e))
        return jsonify(error=str(e)), 500


@app.post("/api/save-image")
@login_required
def save_generated_image():
    data = request.get_json(silent=True) or {}
    chat_id = data.get("chat_id") or str(uuid.uuid4())
    messages = data.get("messages") or []
    if not messages:
        return jsonify(error="پیامی برای ذخیره نیست."), 400
    try:
        save_chat(chat_id, messages, make_title(messages))
        return jsonify(ok=True, chat_id=chat_id)
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.post("/api/transcribe")
@login_required
def transcribe():
    f = request.files.get("audio")
    if not f or not f.filename:
        return jsonify(error="صدایی دریافت نشد."), 400
    try:
        audio_bytes = f.read()
        if not audio_bytes:
            return jsonify(error="فایل صدا خالی است. دوباره ضبط کن و حداقل یک ثانیه صحبت کن."), 400
        if len(audio_bytes) > 25 * 1024 * 1024:
            return jsonify(error="حجم صدای ضبط‌شده بیشتر از 25MB است."), 413

        filename = Path(f.filename).name or "voice.webm"
        mime = (f.mimetype or "audio/webm").split(";")[0]
        # Groq accepts webm, ogg, wav, mp3, m4a, etc. No FFmpeg is needed.
        files = {"file": (filename, audio_bytes, mime)}
        payload = {
            "model": TRANSCRIBE_MODEL or "whisper-large-v3-turbo",
            "response_format": "json",
            "language": "fa",
            "temperature": "0",
        }
        r = requests.post(
            f"{API_BASE}/audio/transcriptions",
            headers={"Authorization": f"Bearer {API_KEY}"},
            files=files,
            data=payload,
            timeout=120,
        )
        if not r.ok:
            try:
                detail = r.json()
            except Exception:
                detail = r.text
            print("\n[TRANSCRIBE API ERROR]", r.status_code, detail)
            return jsonify(error=f"خطای تبدیل صدا {r.status_code}: {detail}"), 502
        result = r.json()
        text = (result.get("text") or "").strip()
        return jsonify(text=text, model=payload["model"])
    except requests.RequestException as e:
        print("\n[TRANSCRIBE NETWORK ERROR]", repr(e))
        return jsonify(error="اتصال به سرویس تبدیل صدا برقرار نشد. اینترنت و API Key را بررسی کن."), 502
    except Exception as e:
        print("\n[TRANSCRIBE ERROR]", repr(e))
        return jsonify(error=str(e)), 500


@app.errorhandler(413)
def too_large(e):
    return jsonify(error="حجم فایل بیشتر از 25MB است."), 413


@app.errorhandler(Exception)
def handle_error(e):
    print("\n[SERVER ERROR]", repr(e))
    return jsonify(error=str(e)), 500


# Initialize secrets/models for both Gunicorn (Render) and local Python runs.
if not setup():
    raise SystemExit(1)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"\n🌐 http://127.0.0.1:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
