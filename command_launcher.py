#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys, json, os, subprocess, pty, select, shutil, time, threading
import datetime, signal, re, fcntl, termios, uuid, urllib.parse, tempfile, socket, socketserver, base64, getpass, pwd, html
from pathlib import Path

# --- vk-api ---
try:
    import vk_api
    from vk_api.longpoll import VkLongPoll, VkEventType
    from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
    from vk_api.keyboard import VkKeyboard, VkKeyboardColor
except ImportError:
    print("Для VK-бота установите vk-api: pip install vk-api")
    vk_api = None; VkLongPoll = None; VkEventType = None
    VkBotLongPoll = None; VkBotEventType = None
    VkKeyboard = None; VkKeyboardColor = None

# --- Telegram ---
try:
    import telebot
    from telebot import types as telebot_types
except ImportError:
    print("Для Telegram-бота установите pyTelegramBotAPI: pip install pyTelegramBotAPI")
    telebot = None; telebot_types = None

# --- requests ---
try:
    import requests
except ImportError:
    print("Установите requests: pip install requests[socks]")
    requests = None

try:
    import socks  # noqa: F401
    _HAVE_SOCKS = True
except ImportError:
    _HAVE_SOCKS = False

# --- PySide6 ---
try:
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QPushButton, QLabel, QLineEdit, QComboBox, QDialog, QDialogButtonBox,
        QFrame, QTextEdit, QMessageBox, QColorDialog, QSplitter,
        QSizePolicy, QStackedWidget, QListWidget, QListWidgetItem,
        QCheckBox, QGroupBox, QFileDialog, QTabWidget, QMenu,
        QSystemTrayIcon, QStyle, QInputDialog, QProgressBar, QSpinBox,
        QTableWidget, QTableWidgetItem, QHeaderView, QPlainTextEdit
    )
    from PySide6.QtCore import (Qt, QThread, Signal, QObject, QSize, QPoint,
                                QTimer, QEvent)
    from PySide6.QtGui import (QFont, QColor, QDrag, QPixmap, QPainter, QPen,
                               QIcon, QAction, QTextCursor, QFontMetrics,
                               QKeyEvent, QTextOption)
except ImportError:
    print("Для GUI установите PySide6: pip install PySide6")


# ============================================================
# Константы
# ============================================================
CONFIG_DIR = Path.home() / ".config" / "mycommandlauncher"
CONFIG_FILE = CONFIG_DIR / "config.json"
DAEMON_LOG = CONFIG_DIR / "daemon.log"
PROXY_DIR = CONFIG_DIR / "proxies"
XRAY_CONFIG_FILE = PROXY_DIR / "telegram_xray_runtime.json"
XRAY_LOG_FILE = PROXY_DIR / "telegram_xray.log"

MAX_BUTTON_NAME_LENGTH = 40
PING_URLS = [
    "https://api.telegram.org",
    "https://cp.cloudflare.com/",
]
# Ускоренные параметры пинга.
PING_TIMEOUT = 3.0
AUTO_PING_INTERVAL = 10
# Частота обновления столбцов пинга в таблице менеджера прокси (мс).
PING_TABLE_REFRESH_MS = 500


# ============================================================
# Утилиты
# ============================================================
def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(host, port, total=4.0, step=0.15):
    """Ждёт, пока порт начнёт принимать соединения."""
    end = time.time() + total
    while time.time() < end:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except Exception:
            time.sleep(step)
    return False


def proxy_to_url(proxy):
    if not proxy:
        return None
    ptype = proxy.get("type", "socks5")
    if ptype == "xray":
        return None
    host = (proxy.get("host") or "").strip()
    if not host:
        return None
    port = int(proxy.get("port") or 0)
    if port <= 0:
        return None
    user = (proxy.get("user") or "").strip()
    pwd = proxy.get("pass") or ""
    if user and pwd:
        auth = f"{urllib.parse.quote(user)}:{urllib.parse.quote(pwd)}@"
    elif user:
        auth = f"{urllib.parse.quote(user)}@"
    else:
        auth = ""
    return f"{ptype}://{auth}{host}:{port}"


def find_xray_binary(user_path=None):
    if user_path:
        p = Path(user_path)
        if p.exists() and os.access(p, os.X_OK):
            return str(p)
    for name in ("xray", "v2ray"):
        p = shutil.which(name)
        if p:
            return p
    for cand in ("/usr/local/bin/xray", "/usr/bin/xray",
                 "/usr/local/bin/v2ray", "/usr/bin/v2ray",
                 "/opt/xray/xray", "/opt/v2ray/v2ray"):
        if Path(cand).exists() and os.access(cand, os.X_OK):
            return cand
    return None


def xray_version(xray_path):
    """Возвращает строку версии или None."""
    if not xray_path or not Path(xray_path).exists():
        return None
    for args in (["version"], ["-version"], ["--version"]):
        try:
            r = subprocess.run([xray_path] + args, capture_output=True,
                               text=True, timeout=5)
            out = (r.stdout or "") + (r.stderr or "")
            if out.strip():
                return out.strip().splitlines()[0]
        except Exception:
            continue
    return None


# ============================================================
# Пинг простых прокси
# ============================================================
def ping_proxy_url(proxy_url, timeout=PING_TIMEOUT):
    """Проверяет прокси через отдельную requests-сессию без переменных окружения."""
    if not requests:
        return None, "requests не установлен"
    if not proxy_url:
        return None, "пустой URL прокси"
    if proxy_url.startswith("socks") and not _HAVE_SOCKS:
        return None, ("Для SOCKS-прокси установите PySocks:\n"
                      "    pip install requests[socks]")

    proxies = {"http": proxy_url, "https": proxy_url}
    last_err = None
    session = requests.Session()
    session.trust_env = False
    try:
        for url in PING_URLS:
            try:
                t0 = time.perf_counter()
                r = session.get(url, proxies=proxies, timeout=timeout,
                                allow_redirects=False)
                ms = int((time.perf_counter() - t0) * 1000)
                return ms, None
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
        return None, last_err or "нет ответа"
    finally:
        session.close()


# ============================================================
# Пинг Xray-outbound (запуск временного xray)
# ============================================================
def ping_xray_outbound(outbound, timeout=PING_TIMEOUT, xray_path=None,
                       reuse_port=None):
    """Проверяет Xray-outbound. Если передан reuse_port, временный Xray не запускается."""
    if not requests:
        return None, "requests не установлен"
    if not isinstance(outbound, dict):
        return None, "outbound не задан"
    if not _HAVE_SOCKS:
        return None, ("Для SOCKS-клиента нужен PySocks:\n"
                      "    pip install requests[socks]")

    if reuse_port and _wait_port("127.0.0.1", int(reuse_port), total=0.15, step=0.03):
        proxy_url = f"socks5h://127.0.0.1:{int(reuse_port)}"
        return ping_proxy_url(proxy_url, timeout=timeout)

    binary = find_xray_binary(xray_path)
    if not binary:
        return None, ("xray/v2ray не найден. Установите Xray или укажите путь в менеджере.")

    port = _find_free_port()
    tmp_dir = PROXY_DIR / "_ping"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return None, f"не создать временную папку: {e}"

    tag = outbound.get("tag") or "proxy"
    ob = dict(outbound)
    ob["tag"] = tag

    cfg_path = tmp_dir / f"ping_{port}.json"
    log_path = tmp_dir / f"ping_{port}.log"
    try:
        log_path.unlink(missing_ok=True)
    except Exception:
        pass

    cfg = {
        "log": {"loglevel": "warning", "error": str(log_path)},
        "inbounds": [{
            "listen": "127.0.0.1",
            "port": port,
            "protocol": "socks",
            "settings": {"udp": False, "auth": "noauth"}
        }],
        "outbounds": [ob]
    }
    try:
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        return None, f"не записать конфиг xray: {e}"

    proc = None
    try:
        proc = subprocess.Popen([binary, "run", "-c", str(cfg_path)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, close_fds=True)
        if not _wait_port("127.0.0.1", port, total=1.2, step=0.04):
            tail = ""
            try:
                if log_path.exists():
                    tail = log_path.read_text(encoding="utf-8", errors="replace")[-700:]
            except Exception:
                pass
            if proc.poll() is not None:
                return None, f"xray упал при запуске:\n{tail or '(лог пуст)'}"
            return None, f"xray не слушает порт {port}.\nЛог:\n{tail or '(пусто)'}"

        return ping_proxy_url(f"socks5h://127.0.0.1:{port}", timeout=timeout)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    finally:
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1.5)
                    except subprocess.TimeoutExpired:
                        proc.kill(); proc.wait(timeout=1)
            except Exception:
                pass
        try:
            cfg_path.unlink(missing_ok=True)
        except Exception:
            pass


# ============================================================
# Извлечение данных из xray outbound
# ============================================================
def outbound_address(outbound):
    if not isinstance(outbound, dict):
        return "", ""
    proto = outbound.get("protocol", "?")
    s = outbound.get("settings") or {}
    for key in ("vnext", "servers"):
        lst = s.get(key)
        if isinstance(lst, list) and lst:
            v = lst[0] or {}
            host = v.get("address", "?")
            port = v.get("port", "?")
            return f"{host}:{port}", proto
    return "", proto


def extract_display(entry):
    name = (entry.get("name") or "").strip()
    ptype = entry.get("type", "socks5")
    if ptype == "xray":
        ob = entry.get("xray_outbound") or {}
        addr, proto = outbound_address(ob)
        type_disp = f"xray/{proto}" if proto and proto != "?" else "xray"
        if not name:
            name = entry.get("remarks") or entry.get("tag") or "xray"
        return name, type_disp, addr
    addr = f"{entry.get('host','')}:{entry.get('port','')}"
    return name, ptype, addr


# ============================================================
# Парсер JSON любого формата
# ============================================================
def is_full_xray_config(data):
    return isinstance(data, dict) and "outbounds" in data and (
        "inbounds" in data or "routing" in data or "dns" in data
        or data.get("remarks") or data.get("log"))


def extract_proxy_outbound_from_full_config(cfg):
    outbounds = cfg.get("outbounds") or []
    proxy_ob = None
    for ob in outbounds:
        if not isinstance(ob, dict):
            continue
        proto = (ob.get("protocol") or "").lower()
        tag = (ob.get("tag") or "").lower()
        if proto in ("freedom", "blackhole", "dns") or tag in ("direct", "block", "dns"):
            continue
        proxy_ob = ob
        break
    if proxy_ob is None:
        return None, None
    remarks = cfg.get("remarks") or cfg.get("name")
    return proxy_ob, remarks


def normalize_to_entries(data):
    out = []

    def handle(item):
        if isinstance(item, str):
            try:
                handle(json.loads(item))
            except Exception:
                pass
            return
        if not isinstance(item, dict):
            return
        if "type" in item and item.get("type") in ("socks5", "socks4", "http", "xray"):
            if item["type"] == "xray":
                ob = item.get("xray_outbound")
                if isinstance(ob, dict) and is_full_xray_config(ob):
                    real_ob, remarks = extract_proxy_outbound_from_full_config(ob)
                    if real_ob:
                        item["xray_outbound"] = real_ob
                        if remarks and not item.get("name"):
                            item["name"] = remarks
            item.setdefault("id", uuid.uuid4().hex[:8])
            out.append(item)
            return
        if is_full_xray_config(item):
            ob, remarks = extract_proxy_outbound_from_full_config(item)
            if ob is None:
                return
            name = remarks or item.get("name") or f"xray-{uuid.uuid4().hex[:4]}"
            out.append({
                "id": uuid.uuid4().hex[:8],
                "name": name,
                "type": "xray",
                "remarks": remarks,
                "xray_outbound": ob,
            })
            return
        for key in ("configs", "proxies", "items", "list"):
            if key in item and isinstance(item[key], list):
                for sub in item[key]:
                    handle(sub)
                return
        if "host" in item and "port" in item:
            item.setdefault("type", "socks5")
            item.setdefault("id", uuid.uuid4().hex[:8])
            item.setdefault("name", f"{item.get('host')}:{item.get('port')}")
            out.append(item)
            return
        if "protocol" in item and "settings" in item:
            out.append({
                "id": uuid.uuid4().hex[:8],
                "name": item.get("tag") or f"xray-{item.get('protocol')}",
                "type": "xray",
                "xray_outbound": item,
            })
            return

    if isinstance(data, dict):
        handle(data)
    elif isinstance(data, list):
        for item in data:
            handle(item)
    return out


# ============================================================
# Парсер ссылок vless/vmess/trojan/ss
# ============================================================
def parse_proxy_link_to_outbound(link):
    link = (link or "").strip()
    if link.startswith("vless://"): return _parse_vless(link)
    if link.startswith("vmess://"): return _parse_vmess(link)
    if link.startswith("trojan://"): return _parse_trojan(link)
    if link.startswith("ss://"): return _parse_ss(link)
    return None, None


def parse_simple_proxy_uri(link):
    """Разбирает socks5://, socks4:// и http://host:port в запись менеджера."""
    link = (link or "").strip()
    u = urllib.parse.urlparse(link)
    scheme = (u.scheme or "").lower()
    if scheme not in ("socks5", "socks5h", "socks4", "socks4a", "http") or not u.hostname or not u.port:
        return None
    ptype = "socks5" if scheme in ("socks5", "socks5h") else ("socks4" if scheme in ("socks4", "socks4a") else "http")
    return {
        "id": uuid.uuid4().hex[:8],
        "name": urllib.parse.unquote(u.fragment) if u.fragment else f"{u.hostname}:{u.port}",
        "type": ptype,
        "host": u.hostname,
        "port": int(u.port),
        "user": urllib.parse.unquote(u.username or ""),
        "pass": urllib.parse.unquote(u.password or ""),
    }


def _extract_proxy_uris(text):
    text = urllib.parse.unquote(text or "")
    pattern = re.compile(r"(?i)(?:vless|vmess|trojan|ss|socks5h?|socks4a?|http)://[^\s<>\"']+")
    found = []
    seen = set()
    for m in pattern.finditer(text):
        uri = m.group(0).rstrip(".,;])}\"")
        if uri not in seen:
            seen.add(uri); found.append(uri)
    return found


def _try_decode_subscription_base64(data):
    """Пытается распознать стандартный и URL-safe base64 без ложных срабатываний."""
    if isinstance(data, bytes):
        raw = data.decode("utf-8", "ignore")
    else:
        raw = str(data)
    compact = re.sub(r"\s+", "", raw).strip()
    if len(compact) < 16:
        return None
    pad = "=" * (-len(compact) % 4)
    candidates = []
    try:
        candidates.append(base64.b64decode(compact + pad, validate=False))
    except Exception:
        pass
    try:
        candidates.append(base64.urlsafe_b64decode(compact + pad))
    except Exception:
        pass
    for b in candidates:
        if not b:
            continue
        try:
            text = b.decode("utf-8-sig", "replace")
        except Exception:
            continue
        low = text.lower()
        if any(x in low for x in ("vless://", "vmess://", "trojan://", "ss://", "socks5://", "socks4://")):
            return text
        if text.lstrip().startswith(("[", "{")):
            return text
    return None


def _extract_candidate_urls_from_html(content, base_url=""):
    """Извлекает возможные URL подписки из HTML/JS страницы."""
    content = html.unescape(content or "")
    out, seen = [], set()

    def add(u):
        u = (u or "").strip().strip("\"'<>`")
        if not u:
            return
        if u.startswith("//"):
            u = (urllib.parse.urlsplit(base_url).scheme or "https") + ":" + u
        elif base_url and u.startswith("/"):
            u = urllib.parse.urljoin(base_url, u)
        elif base_url and not re.match(r"^[a-z][a-z0-9+.-]*://", u, re.I):
            u = urllib.parse.urljoin(base_url, u)
        if re.match(r"^https?://", u, re.I) and u not in seen:
            seen.add(u); out.append(u)

    patterns = [
        r"(?:href|src|action|data-url|data-href|data-subscription|data-config|data-link)\s*=\s*[\"']([^\"']+)[\"']",
        r"(?:window\.location(?:\.href)?|location\.href|redirect|subscription_url|subscription|subscribe_url|config_url)\s*[:=]\s*[\"']([^\"']+)[\"']",
        r"https?://[^\s\"'<>]+",
    ]
    for pat in patterns:
        try:
            for m in re.finditer(pat, content, flags=re.I):
                add(m.group(1) if m.lastindex else m.group(0))
        except Exception:
            pass
    return out


def _extract_embedded_base64_candidates(content):
    vals, seen = [], set()
    for m in re.finditer(r"(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{40,}={0,2})(?![A-Za-z0-9_-])", content or ""):
        x=m.group(1)
        if x not in seen:
            seen.add(x); vals.append(x)
    return vals[:80]


def load_proxy_source(source, timeout=20):
    """Загрузка proxy-подписок, включая web-страницы с вложенной ссылкой подписки."""
    if not requests:
        raise RuntimeError("requests не установлен")
    source=(source or "").strip()
    if not source:
        raise ValueError("Пустая ссылка/текст")

    direct=parse_simple_proxy_uri(source)
    if direct: return [direct]
    try:
        ob,name=parse_proxy_link_to_outbound(source)
    except Exception:
        ob,name=None,None
    if ob:
        return [{"id":uuid.uuid4().hex[:8],"name":name or "proxy","type":"xray","remarks":name,"xray_outbound":ob}]

    headers={
        "User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/150 Safari/537.36",
        "Accept":"text/plain,application/json,application/octet-stream,text/html;q=0.9,*/*;q=0.8",
        "Accept-Language":"ru,en;q=0.8",
        "Cache-Control":"no-cache",
    }
    visited_urls=set(); visited_contents=set(); queue=[]
    if re.match(r"^https?://", source, re.I):
        queue.append((source,0))

    downloaded=[]
    if queue:
        session=requests.Session(); session.trust_env=False
        try:
            while queue and len(downloaded)<12:
                url,depth=queue.pop(0)
                if depth>2 or url in visited_urls: continue
                visited_urls.add(url)
                try:
                    r=session.get(url,timeout=timeout,allow_redirects=True,headers=headers)
                    content=r.content or b""
                    text=r.text or content.decode("utf-8","replace")
                    downloaded.append((r.url,text,r.headers.get("content-type","").lower(),r.status_code))
                    if len(text)>2_000_000: text=text[:2_000_000]
                    if "text/html" in r.headers.get("content-type","").lower() or re.search(r"<html|<script|href=|window\.location",text,re.I):
                        for u in _extract_candidate_urls_from_html(text,r.url):
                            host0=urllib.parse.urlsplit(r.url).netloc
                            host1=urllib.parse.urlsplit(u).netloc
                            low=u.lower()
                            likely=any(k in low for k in ("sub","subscription","config","proxy","v2ray","clash","sing-box","mihomo","api","download","access","key"))
                            if host1==host0 or likely:
                                if u not in visited_urls: queue.append((u,depth+1))
                except Exception:
                    continue
        finally:
            session.close()

    contents=[(u,t) for u,t,_,_ in downloaded] if downloaded else [(source,source)]

    def consume(content, depth=0):
        if depth>4: return []
        content=(content or "").lstrip("\ufeff")
        key=content[:150000]
        if key in visited_contents: return []
        visited_contents.add(key)
        found=[]
        try:
            parsed=json.loads(content)
            found.extend(normalize_to_entries(parsed))
        except Exception:
            pass
        if found: return found
        if content.lstrip().startswith(("---","proxies:","proxy-groups:")):
            try:
                import yaml
                y=yaml.safe_load(content)
                if isinstance(y,dict): found.extend(normalize_to_entries(y))
            except Exception:
                pass
        if found: return found
        for uri in _extract_proxy_uris(content):
            one=parse_simple_proxy_uri(uri)
            if one:
                found.append(one); continue
            try: ob,nm=parse_proxy_link_to_outbound(uri)
            except Exception: ob,nm=None,None
            if ob:
                found.append({"id":uuid.uuid4().hex[:8],"name":nm or "proxy","type":"xray","remarks":nm,"xray_outbound":ob})
        if found: return found
        for candidate in _extract_embedded_base64_candidates(content):
            dec=_try_decode_subscription_base64(candidate)
            if dec and dec!=candidate:
                got=consume(dec,depth+1)
                if got: return got
        dec=_try_decode_subscription_base64(content)
        if dec and dec!=content:
            got=consume(dec,depth+1)
            if got: return got
        return []

    all_entries=[]
    for _,content in contents:
        got=consume(content,0)
        if got: all_entries.extend(got)
    if not all_entries:
        got=consume(source,0)
        if got: all_entries.extend(got)
    if not all_entries:
        raise ValueError("Не удалось найти прокси. Поддерживаются vless/vmess/trojan/ss/socks/http, JSON/NDJSON/YAML, base64-подписки и web-страницы со ссылкой на подписку.")
    uniq=[]; seen=set()
    for e in all_entries:
        key=json.dumps(e,sort_keys=True,ensure_ascii=False)
        if key not in seen:
            seen.add(key); uniq.append(e)
    return uniq


def _parse_vless(link):
    u = urllib.parse.urlparse(link)
    q = urllib.parse.parse_qs(u.query)
    host = u.hostname; port = u.port or 443
    uuid_ = u.username
    flow = q.get("flow", [""])[0]
    security = q.get("security", ["none"])[0]
    net = q.get("type", ["tcp"])[0]
    sni = q.get("sni", [q.get("host", [""])[0]])[0]
    path = q.get("path", ["/"])[0]
    host_hdr = q.get("host", [""])[0]
    fp = q.get("fp", [""])[0]
    pbk = q.get("pbk", [""])[0]
    sid = q.get("sid", [""])[0]
    spx = q.get("spx", ["/"])[0]
    user = {"id": uuid_, "encryption": "none"}
    if flow: user["flow"] = flow
    stream = {"network": net, "security": security}
    if security == "tls":
        stream["tlsSettings"] = {"serverName": sni, "fingerprint": fp or "chrome"}
    elif security == "reality":
        stream["realitySettings"] = {
            "serverName": sni, "fingerprint": fp or "chrome",
            "publicKey": pbk, "shortId": sid, "spiderX": spx}
    if net == "ws":
        stream["wsSettings"] = {"path": path, "headers": {"Host": host_hdr or sni}}
    elif net == "grpc":
        stream["grpcSettings"] = {"serviceName": path.lstrip("/")}
    ob = {"protocol": "vless",
          "settings": {"vnext": [{"address": host, "port": port, "users": [user]}]},
          "streamSettings": stream}
    return ob, (u.fragment or f"vless-{host}")


def _parse_vmess(link):
    import base64
    raw = link[8:]
    try:
        decoded = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", "replace")
        data = json.loads(decoded)
    except Exception as e:
        raise ValueError(f"vmess: {e}")
    host = data.get("add"); port = int(data.get("port", 0))
    uuid_ = data.get("id"); aid = int(data.get("aid", 0))
    net = data.get("net", "tcp"); sec = data.get("tls", "")
    sni = data.get("sni") or data.get("host") or ""
    path = data.get("path", "/"); host_hdr = data.get("host", "")
    user = {"id": uuid_, "alterId": aid, "security": "auto"}
    stream = {"network": net}
    if sec:
        stream["security"] = "tls"
        stream["tlsSettings"] = {"serverName": sni}
    if net == "ws":
        stream["wsSettings"] = {"path": path, "headers": {"Host": host_hdr or sni}}
    elif net == "grpc":
        stream["grpcSettings"] = {"serviceName": path.lstrip("/")}
    ob = {"protocol": "vmess",
          "settings": {"vnext": [{"address": host, "port": port, "users": [user]}]},
          "streamSettings": stream}
    return ob, (data.get("ps") or f"vmess-{host}")


def _parse_trojan(link):
    u = urllib.parse.urlparse(link)
    q = urllib.parse.parse_qs(u.query)
    host = u.hostname; port = u.port or 443
    pwd = u.username
    sni = q.get("sni", [q.get("peer", [host])[0]])[0]
    net = q.get("type", ["tcp"])[0]
    path = q.get("path", ["/"])[0]
    host_hdr = q.get("host", [""])[0]
    stream = {"network": net, "security": "tls", "tlsSettings": {"serverName": sni}}
    if net == "ws":
        stream["wsSettings"] = {"path": path, "headers": {"Host": host_hdr or sni}}
    elif net == "grpc":
        stream["grpcSettings"] = {"serviceName": path.lstrip("/")}
    ob = {"protocol": "trojan",
          "settings": {"servers": [{"address": host, "port": port, "password": pwd}]},
          "streamSettings": stream}
    return ob, (u.fragment or f"trojan-{host}")


def _parse_ss(link):
    import base64
    body = link[5:]
    name = ""
    if "#" in body:
        body, name = body.split("#", 1)
    if "@" in body:
        userinfo, hostpart = body.rsplit("@", 1)
        try:
            dec = base64.b64decode(userinfo + "=" * (-len(userinfo) % 4)).decode()
        except Exception:
            dec = userinfo
        method, _, password = dec.partition(":")
        host, _, port = hostpart.partition(":")
        return {"protocol": "shadowsocks",
                "settings": {"servers": [{"address": host, "port": int(port or 0),
                                          "method": method, "password": password}]}}, \
               (name or f"ss-{host}")
    try:
        decoded = base64.b64decode(body + "=" * (-len(body) % 4)).decode()
    except Exception:
        return None, None
    userinfo, hostpart = decoded.rsplit("@", 1)
    method, _, password = userinfo.partition(":")
    host, _, port = hostpart.partition(":")
    return {"protocol": "shadowsocks",
            "settings": {"servers": [{"address": host, "port": int(port or 0),
                                      "method": method, "password": password}]}}, \
           (name or f"ss-{host}")


# ============================================================
# ProxyManager — прокси используется ТОЛЬКО Telegram-ботом
# ============================================================
class ProxyManager:
    def __init__(self, settings):
        self.settings = settings
        self._tg_xray_proc = None
        self._tg_xray_port = None
        self._tg_xray_proxy_id = None
        self._lock = threading.RLock()
        self._auto_thread = None
        self._stop_auto = threading.Event()
        self.ping_results = {}
        self.ping_errors = {}
        self.ping_in_progress = set()
        self._tg_manual_stopped_ids = set()

    def get_active_proxy(self):
        configs = self.settings.get_proxy_configs()
        if not configs:
            return None
        active_id = self.settings.get_active_proxy_id()
        if active_id == "auto" or self.settings.get_auto_select_proxy():
            return self._pick_best(configs)
        for p in configs:
            if p.get("id") == active_id:
                return p
        return self._pick_best(configs)

    def _pick_best(self, configs):
        with self._lock:
            results = dict(self.ping_results)
        working = [(p, results.get(p.get("id"))) for p in configs if results.get(p.get("id")) is not None]
        if working:
            working.sort(key=lambda x: x[1])
            return working[0][0]
        for p in configs:
            if p.get("type") in ("socks5", "socks4", "http"):
                return p
        return configs[0] if configs else None

    @staticmethod
    def _normalize_proxy_url(url):
        if not url:
            return None
        if url.startswith("socks5://"):
            return "socks5h://" + url[len("socks5://"):]
        if url.startswith("socks4://"):
            return "socks4a://" + url[len("socks4://"):]
        return url

    def _validate_xray_config(self, xray_path, cfg_path):
        try:
            r = subprocess.run(
                [xray_path, "run", "-test", "-c", str(cfg_path)],
                capture_output=True, text=True, timeout=8
            )
            if r.returncode == 0:
                return True, ""
            return False, (r.stderr or r.stdout or "ошибка проверки конфигурации").strip()[-3000:]
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    def _start_tg_xray(self, entry):
        with self._lock:
            if (self._tg_xray_proc is not None and self._tg_xray_proc.poll() is None
                    and self._tg_xray_proxy_id == entry.get("id")
                    and self._tg_xray_port and _wait_port("127.0.0.1", self._tg_xray_port, 0.15, 0.03)):
                return f"socks5h://127.0.0.1:{int(self._tg_xray_port)}", None

        self.stop_telegram_xray()
        binary = find_xray_binary(self.settings.get_xray_path())
        if not binary:
            return None, "Для Xray-прокси Telegram не найден xray/v2ray. Укажите путь или установите Xray."
        outbound = entry.get("xray_outbound")
        if not isinstance(outbound, dict):
            return None, "У Xray-прокси отсутствует outbound."

        port = _find_free_port()
        PROXY_DIR.mkdir(parents=True, exist_ok=True)
        cfg_path = XRAY_CONFIG_FILE
        log_path = XRAY_LOG_FILE
        outbound = dict(outbound)
        outbound["tag"] = "telegram-proxy"
        cfg = {
            "log": {"loglevel": "warning", "error": str(log_path)},
            "inbounds": [{
                "tag": "telegram-socks",
                "listen": "127.0.0.1",
                "port": port,
                "protocol": "socks",
                "settings": {"udp": False, "auth": "noauth"}
            }],
            "outbounds": [outbound]
        }
        try:
            cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
            log_path.unlink(missing_ok=True)
        except Exception as e:
            return None, f"Не удалось записать Telegram-Xray конфиг: {e}"

        ok, err = self._validate_xray_config(binary, cfg_path)
        if not ok:
            return None, "Xray отклонил Telegram-конфигурацию:\n" + err

        try:
            proc = subprocess.Popen(
                [binary, "run", "-c", str(cfg_path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, close_fds=True
            )
        except Exception as e:
            return None, f"Не удалось запустить Xray для Telegram: {e}"

        if not _wait_port("127.0.0.1", port, total=2.5, step=0.05):
            tail = ""
            try:
                if log_path.exists():
                    tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            except Exception:
                pass
            try:
                proc.terminate(); proc.wait(timeout=2)
            except Exception:
                try: proc.kill()
                except Exception: pass
            return None, f"Xray для Telegram не открыл локальный SOCKS5 {port}.\nЛог:\n{tail or '(пусто)'}"

        proxy_url = f"socks5h://127.0.0.1:{port}"
        ms, perr = ping_proxy_url(proxy_url, timeout=5)
        if ms is None:
            try:
                proc.terminate(); proc.wait(timeout=2)
            except Exception:
                try: proc.kill()
                except Exception: pass
            return None, f"Telegram-Xray запущен, но канал не работает: {perr or 'нет ответа'}"

        with self._lock:
            self._tg_xray_proc = proc
            self._tg_xray_port = port
            self._tg_xray_proxy_id = entry.get("id")
        return proxy_url, f"Xray Telegram запущен на 127.0.0.1:{port}, проверка {ms} мс"

    def stop_telegram_xray(self):
        with self._lock:
            proc = self._tg_xray_proc
            self._tg_xray_proc = None
            self._tg_xray_port = None
            self._tg_xray_proxy_id = None
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill(); proc.wait(timeout=1)
            except Exception:
                pass

    def start_telegram_proxy(self, entry):
        if not entry:
            return None, "Прокси не выбран"
        pid = entry.get("id")
        if pid:
            self.settings.set_active_proxy_id(pid)
        self.settings.set_auto_select_proxy(False)

        if entry.get("type") == "xray":
            url, info = self._start_tg_xray(entry)
            if not url:
                self.settings.set_telegram_proxy_enabled(False)
                return None, info or "Не удалось запустить Xray-прокси"
        else:
            url = self._normalize_proxy_url(proxy_to_url(entry))
            if not url:
                self.settings.set_telegram_proxy_enabled(False)
                return None, "Некорректный прокси"
            self.stop_telegram_xray()
            info = f"{entry.get('name') or entry.get('host') or 'выбранный прокси'} готов к использованию Telegram"

        self.settings.set_telegram_proxy_enabled(True)
        return url, info

    def stop_telegram_proxy(self, proxy_id=None):
        # Затрагивает ТОЛЬКО Telegram. Ни VK, ни какие-либо другие боты и
        # сервисы не изменяются. Демон сам подхватит флаг из config.json и
        # остановит только Telegram-поток.
        self.settings.set_telegram_proxy_enabled(False)
        self.stop_telegram_xray()

    def get_active_proxy_url(self):
        if not self.settings.get_telegram_proxy_enabled():
            return None
        proxy = self.get_active_proxy()
        if not proxy:
            return None
        if proxy.get("type") == "xray":
            with self._lock:
                if self._tg_xray_proc is not None and self._tg_xray_proc.poll() is None and self._tg_xray_port:
                    if self._tg_xray_proxy_id == proxy.get("id"):
                        return f"socks5h://127.0.0.1:{self._tg_xray_port}"
            return None
        return self._normalize_proxy_url(proxy_to_url(proxy))

    def telegram_proxy_state(self):
        if not self.settings.get_telegram_proxy_enabled():
            self.stop_telegram_xray()
            return None, True, "прокси Telegram отключён кнопкой «Остановить»"
        proxy = self.get_active_proxy()
        if not proxy:
            self.stop_telegram_xray()
            return None, True, "для Telegram не выбран прокси"
        if proxy.get("type") == "xray":
            url, info = self._start_tg_xray(proxy)
            if url:
                return url, True, info or "Xray-прокси Telegram"
            return None, True, info or "Xray-прокси Telegram недоступен"
        self.stop_telegram_xray()
        url = self._normalize_proxy_url(proxy_to_url(proxy))
        if not url:
            return None, True, "некорректный прокси Telegram"
        return url, True, f"{proxy.get('name') or proxy.get('host') or 'выбранный прокси'}"

    def ping_entry(self, entry):
        ptype = entry.get("type", "socks5")
        if ptype == "xray":
            reuse = None
            with self._lock:
                if (self._tg_xray_proc is not None and self._tg_xray_proc.poll() is None
                        and self._tg_xray_proxy_id == entry.get("id")):
                    reuse = self._tg_xray_port
            return ping_xray_outbound(
                entry.get("xray_outbound") or {},
                xray_path=self.settings.get_xray_path(),
                reuse_port=reuse
            )
        url = self._normalize_proxy_url(proxy_to_url(entry))
        if not url:
            return None, "нет host/port"
        return ping_proxy_url(url)

    def ping_all(self, progress_cb=None):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        configs = list(self.settings.get_proxy_configs())
        if not configs:
            return {}

        def one(entry):
            pid = entry["id"]
            with self._lock:
                self.ping_in_progress.add(pid)
            try:
                return pid, self.ping_entry(entry)
            except Exception as e:
                return pid, (None, f"{type(e).__name__}: {e}")

        results = {}
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(configs))), thread_name_prefix="proxy-ping") as pool:
            futures = [pool.submit(one, p) for p in configs]
            for fut in as_completed(futures):
                pid, (ms, err) = fut.result()
                with self._lock:
                    self.ping_in_progress.discard(pid)
                    self.ping_results[pid] = ms
                    if ms is None and err:
                        self.ping_errors[pid] = err
                    else:
                        self.ping_errors.pop(pid, None)
                results[pid] = ms
                if progress_cb:
                    try: progress_cb(pid, ms, err)
                    except Exception: pass
        return results

    def start_auto_ping(self):
        if self._auto_thread and self._auto_thread.is_alive():
            return
        self._stop_auto.clear()
        def loop():
            while not self._stop_auto.is_set():
                try: self.ping_all()
                except Exception: pass
                self._stop_auto.wait(AUTO_PING_INTERVAL)
        self._auto_thread = threading.Thread(target=loop, daemon=True)
        self._auto_thread.start()

    def stop_auto_ping(self):
        self._stop_auto.set()
        if self._auto_thread:
            self._auto_thread.join(timeout=2)
            self._auto_thread = None


# ============================================================
# SettingsManager
# ============================================================
class SettingsManager:
    def __init__(self):
        self.config = {}
        self.load()

    def load(self):
        if CONFIG_FILE.exists():
            try:
                self.config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception:
                self.config = {}
        else:
            self.config = {}
        d = self.config
        if not d.get("_telegram_proxy_cleanup_v1"):
            d["theme"] = "dark"
            d["_telegram_proxy_cleanup_v1"] = True
        d.setdefault("theme", "dark")
        d.setdefault("custom_theme", {})
        d.setdefault("password", "")
        d.setdefault("buttons", [])
        d.setdefault("splitter_sizes", [200, 600])
        for k, v in (("vk_token", ""), ("vk_group_id", ""),
                     ("vk_enabled", False), ("vk_log_enabled", True),
                     ("vk_log_file", str(CONFIG_DIR / "vk_bot_log.txt")),
                     ("vk_autostart", False)):
            d.setdefault(k, v)
        for k, v in (("telegram_token", ""), ("telegram_enabled", False),
                     ("telegram_log_enabled", True),
                     ("telegram_log_file", str(CONFIG_DIR / "telegram_bot_log.txt")),
                     ("telegram_autostart", False)):
            d.setdefault(k, v)
        d.setdefault("proxy_configs", [])
        d.setdefault("active_proxy_id", "auto")
        d.setdefault("auto_select_proxy", True)
        if "telegram_proxy_enabled" not in d:
            d["telegram_proxy_enabled"] = bool(d.get("proxy_configs"))
        d.setdefault("telegram_proxy_enabled", False)
        d.setdefault("xray_path", "")
        c = d["custom_theme"]
        for k, v in (("main_color", "#0078d7"), ("background", "#f0f0f0"),
                     ("foreground", "#000000"), ("button_fg", "#ffffff"),
                     ("frame_bg", "#ffffff"), ("input_bg", "#ffffff"),
                     ("input_border", "#cccccc")):
            c.setdefault(k, v)
        d["custom_theme"] = c

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(self.config, indent=4, ensure_ascii=False),
                               encoding="utf-8")

    def get_theme(self):
        # Только светлая/тёмная/своя тема. Тёмная — значение по умолчанию.
        t = self.config.get("theme", "dark")
        return t if t in ("dark", "light", "custom") else "dark"
    def set_theme(self, t): self.config["theme"] = t; self.save()
    def get_custom_theme(self): return self.config.get("custom_theme", {})
    def set_custom_theme(self, d): self.config["custom_theme"] = d; self.save()
    def get_password(self): return self.config.get("password", "")
    def set_password(self, p): self.config["password"] = p; self.save()
    def get_buttons(self): return self.config.get("buttons", [])
    def set_buttons(self, b): self.config["buttons"] = b; self.save()
    def add_button(self, name, cmd, color=None):
        b = self.get_buttons(); b.append({"name": name, "command": cmd, "color": color})
        self.config["buttons"] = b; self.save()
    def update_button(self, i, name, cmd, color=None):
        b = self.get_buttons()
        if 0 <= i < len(b): b[i] = {"name": name, "command": cmd, "color": color}
        self.config["buttons"] = b; self.save()
    def remove_button(self, i):
        b = self.get_buttons()
        if 0 <= i < len(b): b.pop(i)
        self.config["buttons"] = b; self.save()
    def get_splitter_sizes(self): return self.config.get("splitter_sizes", [200, 600])
    def set_splitter_sizes(self, s): self.config["splitter_sizes"] = s; self.save()

    # VK
    def get_vk_token(self): return self.config.get("vk_token", "")
    def set_vk_token(self, v): self.config["vk_token"] = v; self.save()
    def get_vk_group_id(self): return self.config.get("vk_group_id", "")
    def set_vk_group_id(self, v): self.config["vk_group_id"] = v; self.save()
    def get_vk_enabled(self): return self.config.get("vk_enabled", False)
    def set_vk_enabled(self, v): self.config["vk_enabled"] = v; self.save()
    def get_vk_log_enabled(self): return bool(self.config.get("vk_log_enabled", True))
    def set_vk_log_enabled(self, v): self.config["vk_log_enabled"] = v; self.save()
    def get_vk_log_file(self): return self.config.get("vk_log_file")
    def set_vk_log_file(self, v): self.config["vk_log_file"] = v; self.save()
    def get_vk_autostart(self): return self.config.get("vk_autostart", False)
    def set_vk_autostart(self, v): self.config["vk_autostart"] = v; self.save()

    # TG
    def get_telegram_token(self): return self.config.get("telegram_token", "")
    def set_telegram_token(self, v): self.config["telegram_token"] = v; self.save()
    def get_telegram_enabled(self): return self.config.get("telegram_enabled", False)
    def set_telegram_enabled(self, v): self.config["telegram_enabled"] = v; self.save()
    def get_telegram_log_enabled(self): return bool(self.config.get("telegram_log_enabled", True))
    def set_telegram_log_enabled(self, v): self.config["telegram_log_enabled"] = v; self.save()
    def get_telegram_log_file(self): return self.config.get("telegram_log_file")
    def set_telegram_log_file(self, v): self.config["telegram_log_file"] = v; self.save()
    def get_telegram_autostart(self): return self.config.get("telegram_autostart", False)
    def set_telegram_autostart(self, v): self.config["telegram_autostart"] = v; self.save()

    # Proxy
    def get_proxy_configs(self): return self.config.get("proxy_configs", [])
    def set_proxy_configs(self, lst): self.config["proxy_configs"] = lst; self.save()
    def add_proxy_config(self, entry):
        lst = list(self.get_proxy_configs())
        if not entry.get("id"): entry["id"] = uuid.uuid4().hex[:8]
        lst.append(entry); self.set_proxy_configs(lst); return entry["id"]
    def add_proxy_configs(self, entries):
        lst = list(self.get_proxy_configs()); ids = []
        for e in entries:
            if not e.get("id"): e["id"] = uuid.uuid4().hex[:8]
            lst.append(e); ids.append(e["id"])
        self.set_proxy_configs(lst); return ids
    def update_proxy_config(self, pid, entry):
        lst = list(self.get_proxy_configs())
        for i, p in enumerate(lst):
            if p.get("id") == pid:
                entry["id"] = pid; lst[i] = entry; break
        self.set_proxy_configs(lst)
    def remove_proxy_config(self, pid):
        lst = [p for p in self.get_proxy_configs() if p.get("id") != pid]
        self.set_proxy_configs(lst)
        if self.get_active_proxy_id() == pid:
            self.set_active_proxy_id("auto")
    def get_active_proxy_id(self): return self.config.get("active_proxy_id", "auto")
    def set_active_proxy_id(self, v): self.config["active_proxy_id"] = v; self.save()
    def get_auto_select_proxy(self): return self.config.get("auto_select_proxy", True)
    def set_auto_select_proxy(self, v): self.config["auto_select_proxy"] = v; self.save()
    def get_telegram_proxy_enabled(self): return bool(self.config.get("telegram_proxy_enabled", False))
    def set_telegram_proxy_enabled(self, v): self.config["telegram_proxy_enabled"] = bool(v); self.save()
    def get_xray_path(self): return self.config.get("xray_path", "")
    def set_xray_path(self, v): self.config["xray_path"] = v; self.save()


# ============================================================
# Диалог редактирования прокси
# ============================================================
class ProxyEditDialog(QDialog):
    def __init__(self, parent=None, entry=None):
        super().__init__(parent)
        self.setWindowTitle("Редактирование прокси" if entry else "Новый прокси")
        self.setModal(True); self.setMinimumWidth(620)
        self.entry = dict(entry) if entry else {}
        layout = QVBoxLayout(self)

        r = QHBoxLayout(); r.addWidget(QLabel("Имя:"))
        self.name_edit = QLineEdit(self.entry.get("name", ""))
        r.addWidget(self.name_edit); layout.addLayout(r)

        r = QHBoxLayout(); r.addWidget(QLabel("Тип:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["socks5", "socks4", "http", "xray"])
        self.type_combo.setCurrentText(self.entry.get("type", "socks5"))
        self.type_combo.currentTextChanged.connect(self.on_type_changed)
        r.addWidget(self.type_combo); r.addStretch(); layout.addLayout(r)

        self.simple_group = QGroupBox("Параметры (socks/http)")
        sg = QVBoxLayout(self.simple_group)
        r = QHBoxLayout(); r.addWidget(QLabel("Хост:"))
        self.host_edit = QLineEdit(self.entry.get("host", ""))
        r.addWidget(self.host_edit)
        r.addWidget(QLabel("Порт:"))
        self.port_spin = QSpinBox(); self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(int(self.entry.get("port") or 1080))
        r.addWidget(self.port_spin); sg.addLayout(r)
        r = QHBoxLayout(); r.addWidget(QLabel("Логин:"))
        self.user_edit = QLineEdit(self.entry.get("user", "")); r.addWidget(self.user_edit)
        r.addWidget(QLabel("Пароль:"))
        self.pass_edit = QLineEdit(self.entry.get("pass", ""))
        self.pass_edit.setEchoMode(QLineEdit.Password)
        r.addWidget(self.pass_edit); sg.addLayout(r)
        layout.addWidget(self.simple_group)

        self.xray_group = QGroupBox("Xray outbound (JSON)")
        xg = QVBoxLayout(self.xray_group)
        self.xray_edit = QPlainTextEdit(); self.xray_edit.setFont(QFont("Monospace", 9))
        self.xray_edit.setPlaceholderText(
            'Одиночный outbound:\n'
            '{\n  "protocol": "vless",\n  "settings": {...},\n  "streamSettings": {...}\n}\n\n'
            'Или ПОЛНЫЙ конфиг Xray (с "inbounds"/"outbounds"/"remarks") —\n'
            'из него автоматически извлечётся proxy-outbound.'
        )
        ob = self.entry.get("xray_outbound")
        if isinstance(ob, dict):
            self.xray_edit.setPlainText(json.dumps(ob, indent=2, ensure_ascii=False))
        xg.addWidget(self.xray_edit)
        r = QHBoxLayout()
        b1 = QPushButton("Вставить полный JSON")
        b1.clicked.connect(self.paste_full_config); r.addWidget(b1)
        b2 = QPushButton("Вставить из ссылки")
        b2.clicked.connect(self.parse_from_link); r.addWidget(b2)
        r.addStretch(); xg.addLayout(r)
        layout.addWidget(self.xray_group)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept); btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self.on_type_changed(self.type_combo.currentText())

    def on_type_changed(self, t):
        is_xray = (t == "xray")
        self.simple_group.setVisible(not is_xray)
        self.xray_group.setVisible(is_xray)
        self.adjustSize()

    def paste_full_config(self):
        text, ok = QInputDialog.getMultiLineText(self, "JSON", "Вставьте JSON:")
        if not ok or not text.strip(): return
        try: data = json.loads(text)
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Некорректный JSON: {e}"); return
        if is_full_xray_config(data):
            ob, remarks = extract_proxy_outbound_from_full_config(data)
            if ob is None:
                QMessageBox.warning(self, "Ошибка", "Не найден proxy-outbound.")
                return
            self.xray_edit.setPlainText(json.dumps(ob, indent=2, ensure_ascii=False))
            if remarks and not self.name_edit.text().strip():
                self.name_edit.setText(remarks)
            self.type_combo.setCurrentText("xray")
        elif isinstance(data, dict) and "protocol" in data and "settings" in data:
            self.xray_edit.setPlainText(json.dumps(data, indent=2, ensure_ascii=False))
            if not self.name_edit.text().strip() and data.get("tag"):
                self.name_edit.setText(data["tag"])
            self.type_combo.setCurrentText("xray")
        else:
            QMessageBox.warning(self, "Ошибка", "Это не xray-конфиг и не outbound.")

    def parse_from_link(self):
        text, ok = QInputDialog.getMultiLineText(
            self, "Ссылка", "vless:// / vmess:// / trojan:// / ss://")
        if not ok or not text.strip(): return
        link = text.strip().splitlines()[0]
        try: ob, name = parse_proxy_link_to_outbound(link)
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Разбор ссылки: {e}"); return
        if ob is None:
            QMessageBox.warning(self, "Ошибка", "Ссылка не распознана."); return
        self.xray_edit.setPlainText(json.dumps(ob, indent=2, ensure_ascii=False))
        if name and not self.name_edit.text().strip():
            self.name_edit.setText(name)
        self.type_combo.setCurrentText("xray")

    def get_data(self):
        data = {
            "id": self.entry.get("id") or uuid.uuid4().hex[:8],
            "name": self.name_edit.text().strip() or "proxy",
            "type": self.type_combo.currentText(),
        }
        if data["type"] == "xray":
            try: raw = json.loads(self.xray_edit.toPlainText())
            except Exception as e: raise ValueError(f"Некорректный JSON: {e}")
            if is_full_xray_config(raw):
                ob, remarks = extract_proxy_outbound_from_full_config(raw)
                if ob is None: raise ValueError("Не найден proxy-outbound")
                data["xray_outbound"] = ob
                if remarks and not self.name_edit.text().strip():
                    data["name"] = remarks
            else:
                data["xray_outbound"] = raw
        else:
            data["host"] = self.host_edit.text().strip()
            data["port"] = self.port_spin.value()
            data["user"] = self.user_edit.text().strip()
            data["pass"] = self.pass_edit.text()
        return data


# ============================================================
# Диалог менеджера прокси — НЕмодальный.
# Открывается отдельным окном и не блокирует ни главное окно,
# ни окно настроек. Можно свободно пользоваться программой,
# пока менеджер прокси открыт.
# ============================================================
class ProxyDialogSignals(QObject):
    single_done = Signal(object, object, object, bool)
    progress = Signal(str, object, object)


class ProxyManagerDialog(QDialog):
    """Менеджер прокси только для Telegram. Немодальное окно."""
    def __init__(self, settings, proxy_manager, parent=None):
        super().__init__(parent)
        self.settings = settings; self.pm = proxy_manager
        self.setWindowTitle("Прокси Telegram")
        self.setMinimumSize(980, 650)
        # Немодальное окно: не блокирует главное окно и окно настроек.
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowFlag(Qt.Window, True)
        self.signals = ProxyDialogSignals()
        self.signals.single_done.connect(self._on_single_ping)
        self.signals.progress.connect(self._apply_ping)
        layout = QVBoxLayout(self)

        xrow = QHBoxLayout()
        xrow.addWidget(QLabel("Xray/v2ray для VLESS/VMess/Trojan/SS:"))
        self.xray_edit = QLineEdit(self.settings.get_xray_path())
        xrow.addWidget(self.xray_edit, 1)
        b = QPushButton("Автопоиск"); b.clicked.connect(self.autodetect_xray); xrow.addWidget(b)
        b = QPushButton("Тест"); b.clicked.connect(self.test_xray); xrow.addWidget(b)
        layout.addLayout(xrow)

        info = QLabel(
            "Прокси действует только внутри Telegram-бота. VK, терминал, браузер и остальные приложения "
            "не получают этот прокси и не меняют системные настройки.\n"
            "Xray-записи запускаются только на localhost-порту, доступном Telegram.\n"
            "Пинг проверяется автоматически каждые 10 секунд; таблица обновляется почти сразу.\n"
            "Это окно немодальное — можно пользоваться основной программой."
        )
        info.setWordWrap(True); info.setStyleSheet("color: gray;"); layout.addWidget(info)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Имя", "Тип", "Адрес", "Пинг, мс", "✓"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.doubleClicked.connect(self.edit_selected)
        layout.addWidget(self.table, 1)

        row = QHBoxLayout()
        for label, cb in (
            ("➕ Добавить", self.add_proxy),
            ("✎ Изменить", self.edit_selected),
            ("✕ Удалить", self.delete_selected),
            ("▶ Запустить", self.start_selected_proxy),
            ("⏹ Остановить", self.stop_selected_proxy),
            ("🔍 Проверить", self.ping_selected),
            ("📡 Проверить все", self.ping_all_clicked),
            ("📥 JSON", self.import_from_json),
            ("📤 JSON", self.export_to_json),
        ):
            b = QPushButton(label); b.clicked.connect(cb); row.addWidget(b)
        row.addStretch(); layout.addLayout(row)
        self.proxy_status = QLabel("Прокси Telegram: " + ("включён" if self.settings.get_telegram_proxy_enabled() else "выключен"))
        self.proxy_status.setStyleSheet("color: #2ecc71; font-weight: bold;" if self.settings.get_telegram_proxy_enabled() else "color: #e74c3c; font-weight: bold;")
        layout.addWidget(self.proxy_status)

        auto_row = QHBoxLayout()
        self.auto_check = QCheckBox("Автовыбор по минимальному пингу")
        self.auto_check.setChecked(self.settings.get_auto_select_proxy())
        self.auto_check.toggled.connect(self.on_auto_toggled)
        auto_row.addWidget(self.auto_check)
        auto_row.addWidget(QLabel("Активный прокси Telegram:"))
        self.active_combo = QComboBox()
        self.active_combo.currentIndexChanged.connect(self.on_active_changed)
        auto_row.addWidget(self.active_combo, 1)
        layout.addLayout(auto_row)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.close); bb.accepted.connect(self.close)
        layout.addWidget(bb)

        # Постоянная автоматическая проверка пинга в фоне.
        try:
            if self.settings.get_proxy_configs():
                self.pm.start_auto_ping()
        except Exception:
            pass

        # Быстрое (500 мс) обновление столбцов пинга без пересоздания строк.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._update_ping_columns)
        self._refresh_timer.start(PING_TABLE_REFRESH_MS)

        self.refresh_list()

    def closeEvent(self, event):
        try:
            self._refresh_timer.stop()
        except Exception:
            pass
        super().closeEvent(event)

    def _update_ping_columns(self):
        """Обновляет только столбцы пинга и активного прокси, без пересоздания строк."""
        configs = self.settings.get_proxy_configs()
        if self.table.rowCount() != len(configs):
            self.refresh_list()
            return
        active = self.pm.get_active_proxy()
        active_id = active.get("id") if active else None
        for i, p in enumerate(configs):
            pid = p["id"]
            ms = self.pm.ping_results.get(pid)
            err = self.pm.ping_errors.get(pid)
            in_prog = pid in self.pm.ping_in_progress
            if in_prog:
                it_ping = QTableWidgetItem("…")
            elif err and ms is None:
                it_ping = QTableWidgetItem("ошибка")
                it_ping.setForeground(QColor("#e74c3c"))
                it_ping.setToolTip(err)
            elif ms is None:
                it_ping = QTableWidgetItem("—")
            else:
                it_ping = QTableWidgetItem(str(ms))
                it_ping.setForeground(QColor("#2ecc71") if ms < 200 else QColor("#f1c40f") if ms < 600 else QColor("#e74c3c"))
            self.table.setItem(i, 3, it_ping)
            self.table.setItem(i, 4, QTableWidgetItem("✓" if pid == active_id else ""))

    def autodetect_xray(self):
        p = find_xray_binary()
        if p:
            self.xray_edit.setText(p); self.settings.set_xray_path(p)
            QMessageBox.information(self, "Xray", f"Найден:\n{p}\n\n{xray_version(p) or ''}")
        else:
            QMessageBox.warning(self, "Xray", "xray/v2ray не найден в PATH.")

    def test_xray(self):
        path = self.xray_edit.text().strip() or self.settings.get_xray_path()
        p = find_xray_binary(path) if path else find_xray_binary()
        if not p:
            QMessageBox.warning(self, "Xray", "xray/v2ray не найден."); return
        self.settings.set_xray_path(p); self.xray_edit.setText(p)
        QMessageBox.information(self, "Xray", f"✅ Бинарь работает:\n{p}\n\n{xray_version(p) or '(версия не определена)'}")

    def refresh_list(self):
        configs = self.settings.get_proxy_configs()
        active = self.pm.get_active_proxy()
        active_id = active.get("id") if active else None
        self.table.setRowCount(len(configs))
        for i, p in enumerate(configs):
            name, type_disp, addr = extract_display(p)
            pid = p["id"]; ms = self.pm.ping_results.get(pid); err = self.pm.ping_errors.get(pid)
            in_prog = pid in self.pm.ping_in_progress
            self.table.setItem(i, 0, QTableWidgetItem(name or "(без имени)"))
            self.table.setItem(i, 1, QTableWidgetItem(type_disp))
            self.table.setItem(i, 2, QTableWidgetItem(addr))
            if in_prog:
                it_ping = QTableWidgetItem("…")
            elif err and ms is None:
                it_ping = QTableWidgetItem("ошибка"); it_ping.setForeground(QColor("#e74c3c")); it_ping.setToolTip(err)
            elif ms is None:
                it_ping = QTableWidgetItem("—")
            else:
                it_ping = QTableWidgetItem(str(ms))
                it_ping.setForeground(QColor("#2ecc71") if ms < 200 else QColor("#f1c40f") if ms < 600 else QColor("#e74c3c"))
            self.table.setItem(i, 3, it_ping)
            self.table.setItem(i, 4, QTableWidgetItem("✓" if pid == active_id else ""))

        self.active_combo.blockSignals(True)
        self.active_combo.clear(); self.active_combo.addItem("— авто —", "auto")
        for p in configs:
            nm, _, _ = extract_display(p)
            self.active_combo.addItem(nm or p.get("id", "?"), p["id"])
        cur = self.settings.get_active_proxy_id(); idx = self.active_combo.findData(cur)
        self.active_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.active_combo.setEnabled(not self.auto_check.isChecked())
        self.active_combo.blockSignals(False)
        enabled = self.settings.get_telegram_proxy_enabled()
        self.proxy_status.setText("Прокси Telegram: " + ("включён" if enabled else "выключен"))
        self.proxy_status.setStyleSheet("color: #2ecc71; font-weight: bold;" if enabled else "color: #e74c3c; font-weight: bold;")

    def _selected_id(self):
        rows = {i.row() for i in self.table.selectedItems()}
        if not rows: return None
        idx = next(iter(rows)); configs = self.settings.get_proxy_configs()
        return configs[idx]["id"] if 0 <= idx < len(configs) else None

    def add_proxy(self):
        dlg = ProxyEditDialog(self)
        if dlg.exec() == QDialog.Accepted:
            try: data = dlg.get_data()
            except ValueError as e: QMessageBox.warning(self, "Ошибка", str(e)); return
            self.settings.add_proxy_config(data)
            try:
                if self.settings.get_proxy_configs():
                    self.pm.start_auto_ping()
            except Exception:
                pass
            self.refresh_list()

    def edit_selected(self):
        pid = self._selected_id()
        if not pid: return
        entry = next((p for p in self.settings.get_proxy_configs() if p.get("id") == pid), None)
        if not entry: return
        dlg = ProxyEditDialog(self, entry=entry)
        if dlg.exec() == QDialog.Accepted:
            try: data = dlg.get_data()
            except ValueError as e: QMessageBox.warning(self, "Ошибка", str(e)); return
            self.settings.update_proxy_config(pid, data); self.refresh_list()

    def delete_selected(self):
        pid = self._selected_id()
        if not pid: return
        if QMessageBox.question(self, "Удаление", "Удалить прокси?") != QMessageBox.Yes: return
        self.settings.remove_proxy_config(pid); self.refresh_list()

    def start_selected_proxy(self):
        """
        Кнопка «Запустить».
        - Если выделена строка — запускается именно этот прокси.
        - Если ничего не выделено и включён автовыбор — сразу запускается
          прокси с наименьшим пингом (без диалога «выберите прокси»).
        ВАЖНО: здесь НЕ вызывается start_service/restart_service — иначе
        демон перезапустился бы, и VK-бот на мгновение остановился бы.
        Изменения конфига живой демон подхватит сам через config.json.
        """
        auto_mode = self.auto_check.isChecked()
        pid = self._selected_id()

        if pid is None:
            if not auto_mode:
                QMessageBox.information(self, "Прокси",
                    "Выберите прокси в таблице или включите автовыбор по минимальному пингу.")
                return
            best = self.pm.get_active_proxy()
            if not best:
                QMessageBox.information(self, "Прокси",
                    "Нет доступных прокси. Добавьте хотя бы один прокси.")
                return
            entry = best
            pid = entry.get("id")
            if not self.pm.ping_results or all(
                v is None for v in self.pm.ping_results.values()
            ):
                try: self.pm.start_auto_ping()
                except Exception: pass
        else:
            entry = next((p for p in self.settings.get_proxy_configs()
                          if p.get("id") == pid), None)
            if not entry:
                return
            if not auto_mode:
                self.settings.set_auto_select_proxy(False)
                self.settings.set_active_proxy_id(pid)

        # Только флаг прокси Telegram. Ни VK, ни systemd-сервис не трогаем.
        self.settings.set_telegram_proxy_enabled(True)

        name, type_disp, addr = extract_display(entry)
        self.refresh_list()
        QMessageBox.information(
            self, "Прокси Telegram",
            f"▶ Прокси включён для Telegram.\n\n"
            f"{name}\n{type_disp}\n{addr}\n\n"
            "Демон Telegram автоматически подключится через этот прокси.\n"
            "VK-бот и другие приложения не затрагиваются."
        )

    def stop_selected_proxy(self):
        # Отключает прокси ТОЛЬКО для Telegram. Ни VK, ни systemd, ни что-либо
        # ещё — не трогается.
        self.pm.stop_telegram_proxy()
        self.refresh_list()
        QMessageBox.information(
            self, "Прокси Telegram",
            "⏹ Прокси полностью отключён для Telegram.\n\n"
            "Telegram-бот будет остановлен и не сможет перейти на прямое соединение.\n"
            "VK-бот, терминал и остальные приложения продолжают работу."
        )

    def ping_selected(self):
        pid=self._selected_id()
        if not pid:
            QMessageBox.information(self,"Проверка","Выберите строку в таблице."); return
        entry=next((p for p in self.settings.get_proxy_configs() if p.get("id")==pid),None)
        if not entry: return
        with self.pm._lock: self.pm.ping_in_progress.add(pid)
        self.refresh_list()
        threading.Thread(target=self._ping_single_worker,args=(entry,),daemon=True).start()

    def _ping_single_worker(self,entry):
        pid=entry["id"]
        try: ms,err=self.pm.ping_entry(entry)
        except Exception as e: ms,err=None,f"{type(e).__name__}: {e}"
        with self.pm._lock:
            self.pm.ping_in_progress.discard(pid)
            self.pm.ping_results[pid]=ms
            if ms is None and err: self.pm.ping_errors[pid]=err
            else: self.pm.ping_errors.pop(pid,None)
        self.signals.single_done.emit(entry,ms,err,True)

    def _on_single_ping(self,entry,ms,err,show_dialog=True):
        self.refresh_list()
        if not show_dialog: return
        name,type_disp,addr=extract_display(entry)
        if ms is not None:
            QMessageBox.information(self,"Проверка прокси",f"Прокси: {name}\nТип: {type_disp}\nАдрес: {addr}\n\n✅ Пинг: {ms} мс")
        else:
            QMessageBox.warning(self,"Проверка прокси",f"Прокси: {name}\nТип: {type_disp}\nАдрес: {addr}\n\n❌ Ошибка:\n{err or 'неизвестно'}")

    def ping_all_clicked(self):
        self.refresh_list()
        threading.Thread(target=self._ping_all_worker,daemon=True).start()

    def _ping_all_worker(self):
        try:
            self.pm.ping_all(progress_cb=lambda pid,ms,err:self.signals.progress.emit(pid,ms,err))
        except Exception:
            pass

    def _apply_ping(self,pid,ms,err):
        configs=self.settings.get_proxy_configs()
        for i,p in enumerate(configs):
            if p.get("id")!=pid: continue
            if ms is None:
                it=QTableWidgetItem("ошибка"); it.setForeground(QColor("#e74c3c")); it.setToolTip(err or "неизвестно")
            else:
                it=QTableWidgetItem(str(ms)); it.setForeground(QColor("#2ecc71") if ms<200 else QColor("#f1c40f") if ms<600 else QColor("#e74c3c"))
            self.table.setItem(i,3,it); break
        active=self.pm.get_active_proxy(); aid=active.get("id") if active else None
        for i,p in enumerate(configs): self.table.setItem(i,4,QTableWidgetItem("✓" if p.get("id")==aid else ""))

    def import_from_json(self):
        path, _ = QFileDialog.getOpenFileName(self, "Импорт JSON", "", "JSON (*.json);;Все файлы (*)")
        if not path: return
        try:
            entries = normalize_to_entries(json.loads(Path(path).read_text(encoding="utf-8")))
            if not entries: raise ValueError("В JSON не найдено поддерживаемых прокси.")
            self.settings.add_proxy_configs(entries)
            try: self.pm.start_auto_ping()
            except Exception: pass
            self.refresh_list()
            QMessageBox.information(self, "Импорт", f"Добавлено: {len(entries)}")
        except Exception as e:
            QMessageBox.warning(self, "Ошибка импорта", str(e))

    def export_to_json(self):
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт JSON", "telegram_proxies.json", "JSON (*.json)")
        if not path: return
        try:
            Path(path).write_text(json.dumps(self.settings.get_proxy_configs(), indent=2, ensure_ascii=False), encoding="utf-8")
            QMessageBox.information(self, "Экспорт", "Сохранено.")
        except Exception as e: QMessageBox.warning(self, "Ошибка", str(e))

    def on_auto_toggled(self, value):
        self.settings.set_auto_select_proxy(value)
        try:
            if value and self.settings.get_proxy_configs():
                self.pm.start_auto_ping()
        except Exception:
            pass
        self.refresh_list()

    def on_active_changed(self, index):
        if self.auto_check.isChecked(): return
        self.settings.set_active_proxy_id(self.active_combo.itemData(index) or "auto")
        self.refresh_list()


class InteractiveWorker(QObject):
    output_signal = Signal(str)
    finished_signal = Signal()

    def __init__(self, password, command=None):
        super().__init__()
        self.password = password; self.command = command
        self._is_running = True; self._process = None
        self._master_fd = None; self._password_sent = False

    def run(self):
        try:
            if not shutil.which('sudo'):
                self.output_signal.emit("Ошибка: sudo не найден\n")
                self.finished_signal.emit(); return
            env = os.environ.copy(); env['TERM'] = 'xterm-256color'
            master_fd, slave_fd = pty.openpty(); self._master_fd = master_fd
            def _ctty():
                os.setsid(); fcntl.ioctl(0, termios.TIOCSCTTY, 0)
            cmd = (['sudo','-S','bash','-l','-c',self.command] if self.command
                   else ['sudo','-S','-i'])
            self._process = subprocess.Popen(cmd, stdin=slave_fd, stdout=slave_fd,
                stderr=slave_fd, close_fds=True, text=False,
                preexec_fn=_ctty, env=env)
            os.close(slave_fd)
            while self._is_running and (self._process.poll() is None):
                try: rlist, _, _ = select.select([master_fd], [], [], 0.1)
                except (OSError, ValueError): break
                if master_fd in rlist:
                    try: data = os.read(master_fd, 1024)
                    except OSError: break
                    if not data: break
                    text = data.decode('utf-8', errors='replace')
                    if not self._password_sent and "password" in text.lower():
                        if self.password:
                            os.write(master_fd, (self.password+'\n').encode())
                            self._password_sent = True; continue
                        else:
                            self.output_signal.emit("Пароль не задан.\n")
                            self._process.terminate(); break
                    self.output_signal.emit(text)
            while self._is_running:
                try: rlist, _, _ = select.select([master_fd], [], [], 0.2)
                except (OSError, ValueError): break
                if master_fd not in rlist: break
                try: data = os.read(master_fd, 1024)
                except OSError: break
                if not data: break
                self.output_signal.emit(data.decode('utf-8', errors='replace'))
            if self._process: self._process.wait()
            if self._master_fd is not None:
                os.close(self._master_fd); self._master_fd = None
        except Exception as e:
            self.output_signal.emit(f"Ошибка: {e}\n")
        finally:
            if self._master_fd is not None:
                try: os.close(self._master_fd)
                except: pass
                self._master_fd = None
            self.finished_signal.emit()

    def stop(self):
        self._is_running = False
        if self._process and self._process.poll() is None:
            self._process.terminate(); time.sleep(0.5)
            if self._process.poll() is None: self._process.kill()
            self._process.wait()

    def send_raw(self, data):
        if self._master_fd is not None and self._is_running:
            try: os.write(self._master_fd, data.encode('utf-8', errors='replace'))
            except OSError: pass


class TerminalCell:
    __slots__ = ("char","fg","bg","bold","reverse")
    def __init__(self, char=" ", fg=None, bg=None, bold=False, reverse=False):
        self.char = char; self.fg = fg; self.bg = bg
        self.bold = bold; self.reverse = reverse


class InteractiveTerminal(QWidget):
    create_button_requested = Signal(str)
    ANSI_COLORS = {30:"black",31:"red",32:"green",33:"yellow",34:"blue",35:"magenta",
                   36:"cyan",37:"white",90:"gray",91:"brightred",92:"brightgreen",
                   93:"brightyellow",94:"brightblue",95:"brightmagenta",96:"brightcyan",97:"brightwhite"}
    ANSI_BG_COLORS = {40:"black",41:"red",42:"green",43:"yellow",44:"blue",45:"magenta",
                      46:"cyan",47:"white",100:"gray",101:"brightred",102:"brightgreen",
                      103:"brightyellow",104:"brightblue",105:"brightmagenta",106:"brightcyan",107:"brightwhite"}
    COLOR_CSS = {"black":"#000000","red":"#cd3131","green":"#0dbc79","yellow":"#e5e510",
                 "blue":"#2472c8","magenta":"#bc3fbc","cyan":"#11a8cd","white":"#e5e5e5",
                 "gray":"#808080","brightred":"#f14c4c","brightgreen":"#23d18b",
                 "brightyellow":"#f5f543","brightblue":"#3b8eea","brightmagenta":"#d670d6",
                 "brightcyan":"#29b8db","brightwhite":"#ffffff"}
    TERMINAL_BG = "#1e1e1e"; TERMINAL_FG = "#ffffff"

    def __init__(self, password_getter, parent=None):
        super().__init__(parent)
        self.password_getter = password_getter
        self.worker = None; self.thread = None; self.is_running = False
        self.lines = []; self.screen_row = 0; self.screen_col = 0
        self.saved_cursor = (0,0); self.scrollback_limit = 5000
        self.fg = None; self.bg = None; self.bold = False; self.reverse = False
        self._pending_raw = ""
        self.REDRAW_INTERVAL_MS = 30; self._needs_redraw = False
        self._redraw_timer = QTimer(self); self._redraw_timer.setSingleShot(True)
        self._redraw_timer.timeout.connect(self._do_redraw)
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
        self.terminal_text = QTextEdit(); self.terminal_text.setReadOnly(True)
        self.terminal_text.setFont(QFont("Monospace", 10))
        self.terminal_text.setLineWrapMode(QTextEdit.WidgetWidth)
        self.terminal_text.setWordWrapMode(QTextOption.WrapAnywhere)
        self.terminal_text.setStyleSheet(
            f"QTextEdit {{ background-color: {self.TERMINAL_BG}; color: {self.TERMINAL_FG}; "
            "selection-background-color: #264f78; }")
        self.terminal_text.installEventFilter(self)
        self.terminal_text.setFocusPolicy(Qt.StrongFocus)
        self.terminal_text.setContextMenuPolicy(Qt.CustomContextMenu)
        self.terminal_text.customContextMenuRequested.connect(self.show_terminal_context_menu)
        layout.addWidget(self.terminal_text)
        self.cursor_visible = True
        self.cursor_timer = QTimer(self); self.cursor_timer.timeout.connect(self.toggle_cursor)
        self.cursor_timer.start(500)
        self._ensure_line(0); self.prompt = "$ "
        self.update_display(); self.start_terminal()

    def _schedule_redraw(self):
        self._needs_redraw = True
        if not self._redraw_timer.isActive():
            self._redraw_timer.start(self.REDRAW_INTERVAL_MS)
    def _do_redraw(self):
        if self._needs_redraw: self.update_display()
    def _ensure_line(self, row):
        while len(self.lines) <= row: self.lines.append([])
    def _trim_lines(self):
        if len(self.lines) > self.scrollback_limit:
            extra = len(self.lines) - self.scrollback_limit
            del self.lines[:extra]
            self.screen_row = max(0, self.screen_row - extra)
            self.saved_cursor = (max(0, self.saved_cursor[0]-extra), self.saved_cursor[1])
    def _newline(self):
        self.screen_row += 1; self._ensure_line(self.screen_row)
    def _put_char(self, ch):
        if ch == "\x00": return
        self._ensure_line(self.screen_row)
        line = self.lines[self.screen_row]
        while len(line) <= self.screen_col: line.append(TerminalCell())
        line[self.screen_col] = TerminalCell(ch, self.fg, self.bg, self.bold, self.reverse)
        self.screen_col += 1
    def _erase_line_from_cursor(self):
        self._ensure_line(self.screen_row)
        line = self.lines[self.screen_row]
        if self.screen_col < len(line):
            for i in range(self.screen_col, len(line)): line[i] = TerminalCell()
    def _erase_line_to_cursor(self):
        self._ensure_line(self.screen_row)
        line = self.lines[self.screen_row]
        end = min(self.screen_col, len(line)-1)
        for i in range(end+1): line[i] = TerminalCell()
    def _erase_line_all(self):
        self._ensure_line(self.screen_row); self.lines[self.screen_row] = []
    def _erase_display(self, mode):
        if mode == 2:
            for i in range(len(self.lines)): self.lines[i] = []
            self.screen_row = 0; self.screen_col = 0
        elif mode == 1:
            for r in range(0, min(self.screen_row+1, len(self.lines))):
                if r < self.screen_row: self.lines[r] = []
                else: self._erase_line_to_cursor()
        else:
            self._erase_line_from_cursor()
            for r in range(self.screen_row+1, len(self.lines)): self.lines[r] = []

    @staticmethod
    def _parse_params(params):
        if not params: return []
        cleaned = params.lstrip("? >")
        if cleaned == "": return []
        result = []
        for item in cleaned.split(";"):
            if item == "": result.append(None)
            else:
                try: result.append(int(item))
                except ValueError: result.append(None)
        return result

    @staticmethod
    def _param(params, index=0, default=1):
        if index >= len(params) or params[index] in (None, 0): return default
        return params[index]

    def _handle_sgr(self, params):
        if not params: params = [0]
        i = 0
        while i < len(params):
            code = params[i] or 0
            if code == 0: self.fg=None; self.bg=None; self.bold=False; self.reverse=False
            elif code == 1: self.bold = True
            elif code == 22: self.bold = False
            elif code == 7: self.reverse = True
            elif code == 27: self.reverse = False
            elif code == 39: self.fg = None
            elif code == 49: self.bg = None
            elif code in self.ANSI_COLORS: self.fg = self.ANSI_COLORS[code]
            elif code in self.ANSI_BG_COLORS: self.bg = self.ANSI_BG_COLORS[code]
            elif code == 38 and i+1 < len(params):
                mode = params[i+1]
                if mode == 5 and i+2 < len(params):
                    self.fg = f"ansi256({params[i+2]})"; i += 2
                elif mode == 2 and i+4 < len(params):
                    rgb = params[i+2:i+5]
                    if all(v is not None for v in rgb):
                        self.fg = "rgb(%d,%d,%d)" % tuple(rgb)
                    i += 4
            elif code == 48 and i+1 < len(params):
                mode = params[i+1]
                if mode == 5 and i+2 < len(params):
                    self.bg = f"ansi256({params[i+2]})"; i += 2
                elif mode == 2 and i+4 < len(params):
                    rgb = params[i+2:i+5]
                    if all(v is not None for v in rgb):
                        self.bg = "rgb(%d,%d,%d)" % tuple(rgb)
                    i += 4
            i += 1

    @staticmethod
    def _ansi256_css(value):
        try: n = max(0, min(255, int(value)))
        except (TypeError, ValueError): return "#ffffff"
        basic = ["#000000","#800000","#008000","#808000","#000080","#800080",
                 "#008080","#c0c0c0","#808080","#ff0000","#00ff00","#ffff00",
                 "#0000ff","#ff00ff","#00ffff","#ffffff"]
        if n < 16: return basic[n]
        if 16 <= n <= 231:
            n -= 16; r = n // 36; g = (n % 36) // 6; b = n % 6
            levels = [0, 95, 135, 175, 215, 255]
            return "#{:02x}{:02x}{:02x}".format(levels[r], levels[g], levels[b])
        gray = 8 + (n - 232) * 10
        return "#{0:02x}{0:02x}{0:02x}".format(gray)

    def _css_color(self, color):
        if not color: return None
        if color.startswith("ansi256(") and color.endswith(")"):
            return self._ansi256_css(color[8:-1])
        if color.startswith("rgb("): return color
        return self.COLOR_CSS.get(color, color)

    def _handle_csi(self, params_text, final):
        params = self._parse_params(params_text)
        if final == "m": self._handle_sgr(params); return
        if final in ("H","f"):
            self.screen_row = max(0, self._param(params,0,1)-1)
            self.screen_col = max(0, self._param(params,1,1)-1)
            self._ensure_line(self.screen_row); return
        if final == "G": self.screen_col = max(0, self._param(params,0,1)-1); return
        if final == "d":
            self.screen_row = max(0, self._param(params,0,1)-1)
            self._ensure_line(self.screen_row); return
        if final == "A": self.screen_row = max(0, self.screen_row - self._param(params)); return
        if final == "B":
            self.screen_row += self._param(params); self._ensure_line(self.screen_row); return
        if final == "C": self.screen_col += self._param(params); return
        if final in ("D","E"):
            self.screen_col = max(0, self.screen_col - self._param(params))
            if final == "E":
                self.screen_row += 1; self._ensure_line(self.screen_row)
            return
        if final == "F": self.screen_row = max(0, self.screen_row - self._param(params)); return
        if final == "P":
            count = self._param(params); self._ensure_line(self.screen_row)
            line = self.lines[self.screen_row]
            if self.screen_col < len(line): del line[self.screen_col:self.screen_col+count]
            return
        if final == "@":
            count = self._param(params); self._ensure_line(self.screen_row)
            line = self.lines[self.screen_row]
            for _ in range(count): line.insert(self.screen_col, TerminalCell())
            return
        if final == "X":
            count = self._param(params); self._ensure_line(self.screen_row)
            line = self.lines[self.screen_row]
            for i in range(self.screen_col, min(self.screen_col+count, len(line))):
                line[i] = TerminalCell()
            return
        if final == "J":
            self._erase_display(params[0] if params and params[0] is not None else 0); return
        if final == "K":
            mode = params[0] if params and params[0] is not None else 0
            if mode == 0: self._erase_line_from_cursor()
            elif mode == 1: self._erase_line_to_cursor()
            elif mode == 2: self._erase_line_all()
            return
        if final == "s": self.saved_cursor = (self.screen_row, self.screen_col); return
        if final == "u":
            self.screen_row, self.screen_col = self.saved_cursor
            self._ensure_line(self.screen_row); return

    def _feed_terminal_stream(self, text):
        if not text: return
        text = self._pending_raw + text; self._pending_raw = ""
        i = 0; n = len(text)
        while i < n:
            ch = text[i]
            if ch == "\x1b":
                if i+1 >= n: self._pending_raw = text[i:]; break
                nxt = text[i+1]
                if nxt == "[":
                    j = i + 2
                    while j < n and 0x30 <= ord(text[j]) <= 0x3f: j += 1
                    while j < n and 0x20 <= ord(text[j]) <= 0x2f: j += 1
                    if j >= n: self._pending_raw = text[i:]; break
                    final = text[j]
                    if 0x40 <= ord(final) <= 0x7e:
                        self._handle_csi(text[i+2:j], final); i = j + 1; continue
                    i += 1; continue
                if nxt == "]":
                    j = i + 2; terminated = False
                    while j < n:
                        if text[j] == "\x07": j += 1; terminated = True; break
                        if text[j] == "\x1b" and j+1 < n and text[j+1] == "\\":
                            j += 2; terminated = True; break
                        j += 1
                    if not terminated: self._pending_raw = text[i:]; break
                    i = j; continue
                if nxt in "()*+-./":
                    if i+2 >= n: self._pending_raw = text[i:]; break
                    i += 3; continue
                i += 2; continue
            if ch == "\r": self.screen_col = 0; i += 1; continue
            if ch == "\n": self._newline(); i += 1; continue
            if ch == "\b": self.screen_col = max(0, self.screen_col-1); i += 1; continue
            if ch == "\t": self.screen_col = ((self.screen_col//8)+1)*8; i += 1; continue
            if ch == "\x07": i += 1; continue
            if ord(ch) < 0x20: i += 1; continue
            self._put_char(ch); i += 1
        self._trim_lines(); self._schedule_redraw()

    @staticmethod
    def escape_html(text):
        return (text.replace("&","&amp;").replace("<","&lt;")
                .replace(">","&gt;").replace('"',"&quot;"))

    def _cell_to_html(self, cell):
        attrs = []; fg = self._css_color(cell.fg); bg = self._css_color(cell.bg)
        if cell.reverse: fg, bg = bg, fg
        if fg: attrs.append(f"color:{fg}")
        if bg: attrs.append(f"background-color:{bg}")
        if cell.bold: attrs.append("font-weight:bold")
        ch = self.escape_html(cell.char)
        if ch == " ": ch = "&nbsp;"
        if attrs: return f'<span style="{";".join(attrs)}">{ch}</span>'
        return ch

    def _line_to_html(self, line):
        last = len(line)
        while last > 0 and line[last-1].char == " " and not line[last-1].fg \
                and not line[last-1].bg and not line[last-1].bold:
            last -= 1
        return "".join(self._cell_to_html(c) for c in line[:last])

    def _cursor_cell_html(self, cell):
        fg = self._css_color(cell.fg) or self.TERMINAL_FG
        bg = self._css_color(cell.bg) or self.TERMINAL_BG
        if cell.reverse: fg, bg = bg, fg
        fg, bg = bg, fg
        ch = self.escape_html(cell.char)
        if ch in (" ", ""): ch = "&nbsp;"
        attrs = [f"color:{fg}", f"background-color:{bg}"]
        if cell.bold: attrs.append("font-weight:bold")
        return f'<span style="{";".join(attrs)}">{ch}</span>'

    def _line_to_html_with_cursor(self, line, col):
        cells = list(line)
        while len(cells) <= col: cells.append(TerminalCell())
        last = len(cells)
        while last > col+1 and cells[last-1].char == " " and not cells[last-1].fg \
                and not cells[last-1].bg and not cells[last-1].bold:
            last -= 1
        parts = []
        for i in range(last):
            parts.append(self._cursor_cell_html(cells[i]) if i == col
                         else self._cell_to_html(cells[i]))
        return "".join(parts)

    def _blank_cursor_html(self):
        return (f'<span style="color:{self.TERMINAL_BG};'
                f'background-color:{self.TERMINAL_FG};">&nbsp;</span>')

    def _screen_html(self):
        rendered = []; show = self.cursor_visible and self.is_running
        for row, line in enumerate(self.lines):
            rendered.append(self._line_to_html_with_cursor(line, self.screen_col)
                            if show and row == self.screen_row
                            else self._line_to_html(line))
        if not rendered:
            text = self.escape_html(self.prompt)
            if show: text += self._blank_cursor_html()
            rendered = [text]
        elif show and self.screen_row >= len(rendered):
            rendered.append(self._blank_cursor_html())
        return ("<pre style=\"margin:0; white-space:pre-wrap; word-break:break-all;\">"
                + "\n".join(rendered) + "</pre>")

    def update_display(self):
        if self.terminal_text.textCursor().hasSelection():
            self._needs_redraw = True
            if not self._redraw_timer.isActive(): self._redraw_timer.start(80)
            return
        sb = self.terminal_text.verticalScrollBar()
        if sb.isSliderDown():
            # Пользователь сейчас тащит мышкой ползунок скроллбара — не
            # перерисовываем содержимое и не трогаем sb.setValue(), иначе
            # каждый тик таймера будет "выдёргивать" ползунок обратно и
            # перетаскивание станет невозможным (будет работать только
            # колесо мыши, которое не конфликтует с этим циклом).
            self._needs_redraw = True
            if not self._redraw_timer.isActive(): self._redraw_timer.start(80)
            return
        self._needs_redraw = False
        was_bottom = sb.value() >= sb.maximum() - 4
        old_val = sb.value()
        self.terminal_text.setUpdatesEnabled(False)
        try: self.terminal_text.setHtml(self._screen_html())
        finally: self.terminal_text.setUpdatesEnabled(True)
        self.terminal_text.viewport().update()
        if was_bottom:
            self.terminal_text.moveCursor(QTextCursor.End); sb.setValue(sb.maximum())
        else:
            sb.setValue(min(old_val, sb.maximum()))

    def toggle_cursor(self):
        if self.terminal_text.textCursor().hasSelection(): return
        self.cursor_visible = not self.cursor_visible; self._schedule_redraw()

    def show_terminal_context_menu(self, pos):
        cur = self.terminal_text.textCursor()
        sel = cur.selectedText().replace('\u2029', '\n').strip()
        menu = self.terminal_text.createStandardContextMenu()
        if sel:
            menu.addSeparator()
            act = QAction("➕ Создать кнопку", self.terminal_text)
            act.triggered.connect(lambda _=False, c=sel: self.create_button_requested.emit(c))
            menu.addAction(act)
        menu.exec(self.terminal_text.viewport().mapToGlobal(pos))

    def eventFilter(self, obj, event):
        if obj is self.terminal_text and event.type() == QEvent.KeyPress:
            key = event.key(); mods = event.modifiers()
            if mods == Qt.ControlModifier and key in (
                Qt.Key_A,Qt.Key_B,Qt.Key_C,Qt.Key_D,Qt.Key_E,Qt.Key_F,Qt.Key_H,
                Qt.Key_K,Qt.Key_L,Qt.Key_N,Qt.Key_O,Qt.Key_P,Qt.Key_R,Qt.Key_T,
                Qt.Key_U,Qt.Key_W,Qt.Key_X,Qt.Key_Y,Qt.Key_Z):
                if key == Qt.Key_C and self.terminal_text.textCursor().hasSelection():
                    return False
                if self.worker and self.is_running:
                    codes = {Qt.Key_A:"\x01",Qt.Key_B:"\x02",Qt.Key_C:"\x03",
                             Qt.Key_D:"\x04",Qt.Key_E:"\x05",Qt.Key_F:"\x06",
                             Qt.Key_H:"\x08",Qt.Key_K:"\x0b",Qt.Key_L:"\x0c",
                             Qt.Key_N:"\x0e",Qt.Key_O:"\x0f",Qt.Key_P:"\x10",
                             Qt.Key_R:"\x12",Qt.Key_T:"\x14",Qt.Key_U:"\x15",
                             Qt.Key_W:"\x17",Qt.Key_X:"\x18",Qt.Key_Y:"\x19",
                             Qt.Key_Z:"\x1a"}
                    self.worker.send_raw(codes[key])
                return True
            if key == Qt.Key_V and mods in (Qt.ControlModifier, Qt.ControlModifier|Qt.ShiftModifier):
                t = QApplication.clipboard().text()
                if t and self.worker and self.is_running: self.worker.send_raw(t)
                return True
            if not self.worker or not self.is_running: return True
            if key in (Qt.Key_Return, Qt.Key_Enter): self.worker.send_raw("\r"); return True
            if key == Qt.Key_Tab: self.worker.send_raw("\t"); return True
            if key == Qt.Key_Backspace: self.worker.send_raw("\x7f"); return True
            if key == Qt.Key_Delete: self.worker.send_raw("\x1b[3~"); return True
            if key == Qt.Key_Up: self.worker.send_raw("\x1b[A"); return True
            if key == Qt.Key_Down: self.worker.send_raw("\x1b[B"); return True
            if key == Qt.Key_Left: self.worker.send_raw("\x1b[D"); return True
            if key == Qt.Key_Right: self.worker.send_raw("\x1b[C"); return True
            if key == Qt.Key_Home: self.worker.send_raw("\x01"); return True
            if key == Qt.Key_End: self.worker.send_raw("\x05"); return True
            t = event.text()
            if t and t.isprintable(): self.worker.send_raw(t); return True
            return False
        return super().eventFilter(obj, event)

    def start_terminal(self, command=None, clear_screen=False):
        if self.is_running: return
        password = self.password_getter()
        if not password:
            password, ok = QInputDialog.getText(self, "Пароль",
                "Введите пароль sudo:", QLineEdit.Password)
            if not ok: return
            if not password: return
            if self.parent() and hasattr(self.parent(), "settings"):
                self.parent().settings.set_password(password)
        self.stop_terminal()
        if clear_screen:
            self.lines = []; self.screen_row = 0; self.screen_col = 0
            self._pending_raw = ""; self.fg = None; self.bg = None
            self.bold = False; self.reverse = False; self._ensure_line(0)
            self._needs_redraw = False; self._redraw_timer.stop(); self.update_display()
        self.worker = InteractiveWorker(password, command=command)
        self.thread = QThread(); self.worker.moveToThread(self.thread)
        self.worker.output_signal.connect(self.append_output)
        self.worker.finished_signal.connect(self.on_terminal_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.started.connect(self.worker.run)
        self.is_running = True; self.thread.start()

    def append_output(self, text): self._feed_terminal_stream(text)

    def stop_terminal(self):
        if self.worker: self.worker.stop()
        if self.thread and self.thread.isRunning():
            self.thread.quit()
            if not self.thread.wait(2000):
                self.thread.terminate(); self.thread.wait(0)
        self.worker = None; self.thread = None; self.is_running = False

    def on_terminal_finished(self):
        self._feed_terminal_stream("\r\n--- Терминал завершён ---\r\n")
        self.stop_terminal()

    def close_terminal(self): self.stop_terminal()


class CommandTerminalWidget(InteractiveTerminal):
    progress_started = Signal(); progress_finished = Signal()

    def __init__(self, button_name, command, password_getter, parent=None):
        self.button_name = button_name; self.command = command
        self._auto_start_allowed = False
        super().__init__(password_getter, parent)
        self._auto_start_allowed = True
        self.label = QLabel(f"Терминал: {button_name}")
        self.label.setAlignment(Qt.AlignCenter)
        self.layout().insertWidget(0, self.label)
        ctl = QHBoxLayout()
        self.stop_btn = QPushButton("⏹ Остановить")
        self.stop_btn.clicked.connect(self.stop_terminal)
        ctl.addWidget(self.stop_btn); self.layout().addLayout(ctl)

    def start_terminal(self, command=None, clear_screen=False):
        if not self._auto_start_allowed: return
        was = self.is_running
        super().start_terminal(command=self.command, clear_screen=False)
        if not was and self.is_running: self.progress_started.emit()

    def activate(self):
        if not self.is_running:
            if self.lines and any(line for line in self.lines):
                self._feed_terminal_stream(
                    "\r\n\r\n================ НОВЫЙ ЗАПУСК ================\r\n")
            self.start_terminal()
        self.terminal_text.setFocus()

    def stop_terminal(self):
        was = self.is_running; super().stop_terminal()
        if was: self.progress_finished.emit()

    def stop_and_cleanup(self): self.stop_terminal()
    def update_button_name(self, n): self.button_name = n; self.label.setText(f"Терминал: {n}")
    def update_command(self, c):
        self.command = c
        if self.is_running: self.stop_terminal()



# --- CreateButtonDialog / CustomThemeDialog ---
class CreateButtonDialog(QDialog):
    def __init__(self, parent=None, name="", command="", color=None):
        super().__init__(parent)
        self.setWindowTitle("Создать кнопку" if not name else "Редактировать кнопку")
        self.setModal(True)
        # Диалог можно тянуть за края мышью. Минимальный размер НЕ задаём
        # вручную числом — раньше это позволяло сжать окно сильнее, чем
        # реально влезает содержимое, из-за чего подпись "Команда:"
        # наезжала на поле ввода. Вместо этого минимальный размер вычислит
        # сама раскладка (см. конец __init__), поэтому сжать диалог ниже
        # того, что реально помещается, физически нельзя — растягивать
        # вверх при этом по-прежнему можно без ограничений.
        self.resize(460, 520)
        self.selected_color = color

        layout = QVBoxLayout(self); layout.setSpacing(14)
        layout.setContentsMargins(18, 18, 18, 18)

        # --- Подписи-заголовки: фиксированная высота, чтобы при
        # растягивании панели они НИКОГДА не меняли размер. Растягиваются
        # только сами поля ввода/иконка/предпросмотр, идущие под ними. ---
        def _static_label(text):
            lab = QLabel(text)
            lab.setStyleSheet("font-weight: 600;")
            lab.setFixedHeight(lab.sizeHint().height())
            lab.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            return lab

        name_label = _static_label(f"Название (≤ {MAX_BUTTON_NAME_LENGTH}):")
        layout.addWidget(name_label)
        self.name_edit = QLineEdit(); self.name_edit.setMaxLength(MAX_BUTTON_NAME_LENGTH)
        self.name_edit.setMinimumHeight(32)
        self.name_edit.setText(name)
        self.name_edit.textChanged.connect(self._update_preview)
        layout.addWidget(self.name_edit)

        cmd_label = _static_label("Команда:")
        layout.addWidget(cmd_label)
        # Поле ввода команды — именно тот "input", который должен расти
        # вместе с панелью: минимальная высота задана, но не максимальная,
        # плюс растягивающий фактор 1, чтобы забирать всё свободное место
        # при увеличении диалога.
        self.command_edit = QTextEdit(); self.command_edit.setMinimumHeight(90)
        self.command_edit.setText(command)
        layout.addWidget(self.command_edit, 1)

        # --- Цвет: крупная кликабельная "иконка"-превью + кнопка сброса.
        # Иконка растягивается вместе с шириной панели (растягивающий
        # фактор 1 у неё, а не у пустого места после кнопок). ---
        color_block = _static_label("Цвет кнопки:")
        layout.addWidget(color_block)
        cl = QHBoxLayout(); cl.setSpacing(10)
        self.color_preview = QPushButton()
        self.color_preview.setMinimumSize(56, 42)
        self.color_preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.color_preview.setCursor(Qt.PointingHandCursor)
        self.color_preview.setToolTip("Выбрать цвет")
        self.color_preview.clicked.connect(self.choose_color)
        cl.addWidget(self.color_preview, 1)
        reset_btn = QPushButton("↺ Сбросить")
        reset_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        reset_btn.clicked.connect(self.clear_color)
        cl.addWidget(reset_btn)
        layout.addLayout(cl)

        # --- Живой предпросмотр итоговой кнопки ---
        preview_label = _static_label("Предпросмотр:")
        layout.addWidget(preview_label)
        self.preview_btn = QPushButton(name or "Название кнопки")
        self.preview_btn.setEnabled(False)
        self.preview_btn.setMinimumHeight(42)
        layout.addWidget(self.preview_btn)

        self._refresh_color_ui()

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept); btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        # Реальный минимум по содержимому (посчитан раскладкой сейчас, когда
        # все виджеты уже добавлены) — ниже этого диалог сжать нельзя,
        # поэтому наложение строк друг на друга больше невозможно.
        self.setMinimumSize(layout.minimumSize())

    def _update_preview(self):
        self.preview_btn.setText(self.name_edit.text().strip() or "Название кнопки")

    def _refresh_color_ui(self):
        self._update_preview()
        if self.selected_color:
            self.color_preview.setStyleSheet(
                f"QPushButton {{ background-color: {self.selected_color}; "
                "border: 2px solid rgba(0,0,0,70); border-radius: 8px; }")
            self.preview_btn.setStyleSheet(
                f"QPushButton:disabled {{ background-color: {self.selected_color}; "
                "color: white; border-radius: 6px; font-weight: 600; }")
        else:
            self.color_preview.setStyleSheet(
                "QPushButton { background-color: transparent; "
                "border: 2px dashed #888888; border-radius: 8px; }")
            self.preview_btn.setStyleSheet(
                "QPushButton:disabled { border-radius: 6px; font-weight: 600; }")

    def choose_color(self):
        c = QColorDialog.getColor(QColor(self.selected_color or "#0078d7"), self, "Цвет кнопки")
        if c.isValid():
            self.selected_color = c.name()
            self._refresh_color_ui()

    def clear_color(self):
        self.selected_color = None
        self._refresh_color_ui()

    def get_data(self):
        return (self.name_edit.text().strip(),
                self.command_edit.toPlainText().strip(),
                self.selected_color)


class CustomThemeDialog(QDialog):
    def __init__(self, custom_theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Пользовательская тема"); self.setModal(True)
        self.setMinimumWidth(400)
        self.custom = custom_theme.copy(); self.color_widgets = {}
        layout = QVBoxLayout(self)
        for key, label in [
            ("main_color","Основной цвет"),("background","Фон окна"),
            ("foreground","Цвет текста"),("button_fg","Цвет текста кнопок"),
            ("frame_bg","Фон рамок"),("input_bg","Фон полей"),
            ("input_border","Цвет границ")]:
            h = QHBoxLayout(); h.addWidget(QLabel(label+":"))
            p = QFrame(); p.setFixedSize(30, 30)
            c = self.custom.get(key, "#ffffff")
            p.setStyleSheet(f"background-color: {c}; border: 1px solid #555;")
            h.addWidget(p)
            b = QPushButton("Выбрать")
            b.clicked.connect(lambda _, k=key, pw=p: self.choose_color(k, pw))
            h.addWidget(b); layout.addLayout(h); self.color_widgets[key] = p
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        r = QPushButton("Сбросить"); r.clicked.connect(self.reset_to_default)
        bb.addButton(r, QDialogButtonBox.ActionRole); layout.addWidget(bb)

    def choose_color(self, key, pw):
        c = QColorDialog.getColor(QColor(self.custom.get(key, "#ffffff")), self, f"Цвет: {key}")
        if c.isValid():
            self.custom[key] = c.name()
            pw.setStyleSheet(f"background-color: {self.custom[key]}; border: 1px solid #555;")

    def reset_to_default(self):
        d = {"main_color":"#0078d7","background":"#f0f0f0","foreground":"#000000",
             "button_fg":"#ffffff","frame_bg":"#ffffff","input_bg":"#ffffff",
             "input_border":"#cccccc"}
        for k, v in d.items():
            self.custom[k] = v
            if k in self.color_widgets:
                self.color_widgets[k].setStyleSheet(
                    f"background-color: {v}; border: 1px solid #555;")

    def get_custom_theme(self): return self.custom


class SettingsDialog(QDialog):
    def __init__(self, settings_manager, proxy_manager, parent=None):
        super().__init__(parent)
        self.settings = settings_manager; self.pm = proxy_manager
        self.setWindowTitle("Настройки"); self.setMinimumWidth(620)
        self.setModal(False); self.setAttribute(Qt.WA_DeleteOnClose)
        self._proxy_dialog = None
        self._clearing = False; self._last_runtime_log_size = 0
        layout = QVBoxLayout(self)
        self.tab_widget = QTabWidget(); layout.addWidget(self.tab_widget)

        # Основные
        g = QWidget(); gl = QVBoxLayout(g)
        r = QHBoxLayout(); r.addWidget(QLabel("Тема:"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Тёмная", "dark")
        self.theme_combo.addItem("Светлая", "light")
        self.theme_combo.addItem("Своя тема…", "custom")
        idx = self.theme_combo.findData(self.settings.get_theme())
        self.theme_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.theme_combo.currentIndexChanged.connect(
            lambda _i: self.custom_theme_btn.setEnabled(self.theme_combo.currentData() == "custom"))
        r.addWidget(self.theme_combo); gl.addLayout(r)
        self.custom_theme_btn = QPushButton("Настроить тему")
        self.custom_theme_btn.clicked.connect(self.edit_custom_theme)
        self.custom_theme_btn.setEnabled(self.theme_combo.currentData() == "custom")
        gl.addWidget(self.custom_theme_btn)
        r = QHBoxLayout(); r.addWidget(QLabel("Пароль sudo:"))
        self.password_edit = QLineEdit(); self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setText(self.settings.get_password())
        r.addWidget(self.password_edit); gl.addLayout(r); gl.addStretch()
        self.tab_widget.addTab(g, "Основные")

        # VK
        vk = QWidget(); vkl = QVBoxLayout(vk)
        vg = QGroupBox("Бот ВКонтакте"); vgl = QVBoxLayout(vg)
        self.vk_enabled_check = QCheckBox("Включить")
        self.vk_enabled_check.setChecked(self.settings.get_vk_enabled())
        vgl.addWidget(self.vk_enabled_check)
        r = QHBoxLayout(); r.addWidget(QLabel("API токен:"))
        self.vk_token_edit = QLineEdit(); self.vk_token_edit.setEchoMode(QLineEdit.Password)
        self.vk_token_edit.setText(self.settings.get_vk_token())
        r.addWidget(self.vk_token_edit); vgl.addLayout(r)
        r = QHBoxLayout(); r.addWidget(QLabel("ID группы:"))
        self.vk_group_id_edit = QLineEdit()
        self.vk_group_id_edit.setText(self.settings.get_vk_group_id())
        r.addWidget(self.vk_group_id_edit); vgl.addLayout(r)
        self.vk_autostart_check = QCheckBox("Автозапуск демона")
        self.vk_autostart_check.setChecked(self.settings.get_vk_autostart())
        vgl.addWidget(self.vk_autostart_check)
        sb = QPushButton("Статус systemd")
        sb.clicked.connect(lambda: QMessageBox.information(self, "Статус",
                                                          get_service_status()))
        vgl.addWidget(sb)
        lg = QGroupBox("Логирование"); lgl = QVBoxLayout(lg)
        self.vk_log_enabled_check = QCheckBox("Записывать лог VK в файл (файл — отдельно от окна лога)")
        self.vk_log_enabled_check.setChecked(self.settings.get_vk_log_enabled())
        lgl.addWidget(self.vk_log_enabled_check)
        r = QHBoxLayout(); r.addWidget(QLabel("Файл:"))
        self.vk_log_file_edit = QLineEdit()
        self.vk_log_file_edit.setText(self.settings.get_vk_log_file())
        r.addWidget(self.vk_log_file_edit); lgl.addLayout(r)
        vgl.addWidget(lg); vkl.addWidget(vg)
        vkl.addWidget(QLabel("Лог VK:"))
        self.vk_log_text = QTextEdit(); self.vk_log_text.setReadOnly(True)
        self.vk_log_text.setFont(QFont("Monospace", 9)); self.vk_log_text.setMinimumHeight(140)
        vkl.addWidget(self.vk_log_text)
        clear_vk_btn = QPushButton("🗑 Очистить лог VK")
        clear_vk_btn.setToolTip("Очистить только лог бота ВКонтакте")
        clear_vk_btn.clicked.connect(lambda: self.clear_log("VK"))
        vkl.addWidget(clear_vk_btn)
        self.tab_widget.addTab(vk, "Бот ВК")

        # TG
        tg = QWidget(); tgl = QVBoxLayout(tg)
        tgg = QGroupBox("Telegram-бот"); tggl = QVBoxLayout(tgg)
        self.telegram_enabled_check = QCheckBox("Включить")
        self.telegram_enabled_check.setChecked(self.settings.get_telegram_enabled())
        tggl.addWidget(self.telegram_enabled_check)
        r = QHBoxLayout(); r.addWidget(QLabel("Bot token:"))
        self.telegram_token_edit = QLineEdit()
        self.telegram_token_edit.setEchoMode(QLineEdit.Password)
        self.telegram_token_edit.setText(self.settings.get_telegram_token())
        r.addWidget(self.telegram_token_edit); tggl.addLayout(r)
        self.telegram_autostart_check = QCheckBox("Автозапуск демона")
        self.telegram_autostart_check.setChecked(self.settings.get_telegram_autostart())
        tggl.addWidget(self.telegram_autostart_check)
        row = QHBoxLayout()
        open_pm = QPushButton("🌐 Прокси только для Telegram…")
        open_pm.clicked.connect(self.open_proxy_manager)
        row.addWidget(open_pm); row.addStretch(); tggl.addLayout(row)
        tg_log = QGroupBox("Логирование"); tl = QVBoxLayout(tg_log)
        self.telegram_log_enabled_check = QCheckBox("Записывать лог Telegram в файл (файл — отдельно от окна лога)")
        self.telegram_log_enabled_check.setChecked(self.settings.get_telegram_log_enabled())
        tl.addWidget(self.telegram_log_enabled_check)
        r = QHBoxLayout(); r.addWidget(QLabel("Файл:"))
        self.telegram_log_file_edit = QLineEdit()
        self.telegram_log_file_edit.setText(self.settings.get_telegram_log_file())
        r.addWidget(self.telegram_log_file_edit); tl.addLayout(r)
        tggl.addWidget(tg_log); tgl.addWidget(tgg)
        tgl.addWidget(QLabel("Лог Telegram:"))
        self.telegram_log_text = QTextEdit(); self.telegram_log_text.setReadOnly(True)
        self.telegram_log_text.setFont(QFont("Monospace", 9))
        self.telegram_log_text.setMinimumHeight(140)
        tgl.addWidget(self.telegram_log_text)
        clear_tg_btn = QPushButton("🗑 Очистить лог Telegram")
        clear_tg_btn.setToolTip("Очистить только лог бота Telegram")
        clear_tg_btn.clicked.connect(lambda: self.clear_log("TG"))
        tgl.addWidget(clear_tg_btn)
        self.tab_widget.addTab(tg, "Бот Telegram")

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.save_and_close); bb.rejected.connect(self.reject)
        layout.addWidget(bb)
        self.log_timer = QTimer(self); self.log_timer.timeout.connect(self.refresh_log_incremental)
        self.log_timer.start(3000)
        self.tab_widget.currentChanged.connect(
            lambda i: self.refresh_log_full() if i in (1,2) else None)
        self.refresh_log_full()

    def edit_custom_theme(self):
        d = CustomThemeDialog(self.settings.get_custom_theme(), self)
        if d.exec() == QDialog.Accepted:
            self.settings.set_custom_theme(d.get_custom_theme())

    def open_proxy_manager(self):
        """Открывает немодальный менеджер прокси отдельным окном.
        Основное окно и окно настроек остаются доступными."""
        if getattr(self, "_proxy_dialog", None) is not None:
            try:
                self._proxy_dialog.raise_()
                self._proxy_dialog.activateWindow()
                return
            except RuntimeError:
                self._proxy_dialog = None
        d = ProxyManagerDialog(self.settings, self.pm, self)
        self._proxy_dialog = d
        d.destroyed.connect(lambda: setattr(self, "_proxy_dialog", None))
        d.show()
        d.raise_()
        d.activateWindow()

    def _read_runtime_bot_log(self, tag):
        """Runtime GUI log. Always available independently of file logging."""
        try:
            if not DAEMON_LOG.exists():
                return "Лог пока пуст..."
            text = DAEMON_LOG.read_text(encoding="utf-8", errors="replace")
            lines = [line for line in text.splitlines() if f"[{tag}]" in line]
            return "\n".join(lines) if lines else "Лог пока пуст..."
        except Exception as e:
            return f"Ошибка чтения лога: {e}"

    def refresh_log_full(self):
        if self._clearing: return
        self.vk_log_text.setPlainText(self._read_runtime_bot_log("VK"))
        self.telegram_log_text.setPlainText(self._read_runtime_bot_log("TG"))
        for widget in (self.vk_log_text, self.telegram_log_text):
            c = widget.textCursor(); c.movePosition(QTextCursor.End); widget.setTextCursor(c)
        try:
            self._last_runtime_log_size = DAEMON_LOG.stat().st_size if DAEMON_LOG.exists() else 0
        except Exception:
            self._last_runtime_log_size = 0

    def refresh_log_incremental(self):
        if self._clearing: return
        try:
            size = DAEMON_LOG.stat().st_size if DAEMON_LOG.exists() else 0
            if size != getattr(self, "_last_runtime_log_size", 0):
                self.refresh_log_full()
        except Exception:
            self.refresh_log_full()

    def clear_log(self, tag):
        """Очищает в общем файле лога daemon.log только строки нужного
        бота (VK или TG), не трогая строки другого бота."""
        label = "VK" if tag == "VK" else "Telegram"
        if QMessageBox.question(
                self, "Очистить лог",
                f"Очистить лог {label}? Действие необратимо."
                ) != QMessageBox.Yes:
            return
        self._clearing = True
        try:
            if DAEMON_LOG.exists():
                text = DAEMON_LOG.read_text(encoding="utf-8", errors="replace")
                kept = [ln for ln in text.splitlines() if f"[{tag}]" not in ln]
                DAEMON_LOG.write_text(
                    ("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось очистить лог: {e}")
        finally:
            self._clearing = False
        if tag == "VK": self.vk_log_text.clear()
        else: self.telegram_log_text.clear()
        try:
            self._last_runtime_log_size = DAEMON_LOG.stat().st_size if DAEMON_LOG.exists() else 0
        except Exception:
            self._last_runtime_log_size = 0

    def save_and_close(self):
        self.settings.set_theme(self.theme_combo.currentData())
        self.settings.set_password(self.password_edit.text())
        self.settings.set_vk_token(self.vk_token_edit.text().strip())
        self.settings.set_vk_group_id(self.vk_group_id_edit.text().strip())
        self.settings.set_vk_enabled(self.vk_enabled_check.isChecked())
        self.settings.set_vk_log_enabled(self.vk_log_enabled_check.isChecked())
        self.settings.set_vk_log_file(self.vk_log_file_edit.text().strip())
        self.settings.set_vk_autostart(self.vk_autostart_check.isChecked())
        self.settings.set_telegram_token(self.telegram_token_edit.text().strip())
        self.settings.set_telegram_enabled(self.telegram_enabled_check.isChecked())
        self.settings.set_telegram_log_enabled(self.telegram_log_enabled_check.isChecked())
        self.settings.set_telegram_log_file(self.telegram_log_file_edit.text().strip())
        self.settings.set_telegram_autostart(self.telegram_autostart_check.isChecked())
        self.settings.save()
        ensure_service()
        any_on = self.vk_enabled_check.isChecked() or self.telegram_enabled_check.isChecked()
        if any_on:
            if not is_service_active(): start_service()
            else: restart_service()
        else:
            if is_service_active(): stop_service()
        any_auto = (self.vk_autostart_check.isChecked()
                    or self.telegram_autostart_check.isChecked())
        if any_auto:
            if not is_service_enabled(): enable_service()
        else:
            if is_service_enabled(): disable_service()
        self.accept()

    def closeEvent(self, event):
        self.log_timer.stop(); super().closeEvent(event)


# --- ButtonItemWidget / DragListWidget ---
class ButtonItemWidget(QWidget):
    def __init__(self, name, command, color, index, main_window, parent=None):
        super().__init__(parent)
        self.index = index; self.name = name; self.command = command
        self.color = color; self.main_window = main_window
        self._progress_value = 0
        hl = QHBoxLayout(self); hl.setContentsMargins(0,0,0,0); hl.setSpacing(5)
        col = QVBoxLayout(); col.setContentsMargins(0,0,0,0); col.setSpacing(2)
        self.btn = QPushButton(name); self.btn.setMinimumHeight(36)
        self._apply_color(color)
        self.btn.clicked.connect(lambda: self.main_window.on_button_clicked(self.index))
        col.addWidget(self.btn, 1)
        self.progress_bar = QProgressBar(); self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0); self.progress_bar.setFixedHeight(12)
        self.progress_bar.setTextVisible(True); self.progress_bar.setFormat("%p%")
        self.progress_bar.setAlignment(Qt.AlignCenter); self.progress_bar.setVisible(False)
        col.addWidget(self.progress_bar); hl.addLayout(col, 1)
        e = QPushButton("✎"); e.setFixedSize(30, 30)
        e.setStyleSheet("QPushButton { background-color: #4a90d9; color: white; border-radius: 15px; }")
        e.clicked.connect(lambda: self.main_window.edit_button(self.index))
        hl.addWidget(e)
        d = QPushButton("✕"); d.setFixedSize(30, 30)
        d.setStyleSheet("QPushButton { background-color: #ff4444; color: white; border-radius: 15px; }")
        d.clicked.connect(lambda: self.main_window.delete_button(self.index))
        hl.addWidget(d); self.setFixedHeight(68)
        self._pt = QTimer(self); self._pt.setInterval(150)
        self._pt.timeout.connect(self._tick_progress)

    def _apply_color(self, color):
        if color:
            self.btn.setStyleSheet(
                f"QPushButton {{ background-color: {color}; border-radius: 5px; "
                "color: white; white-space: normal; }}")
        else:
            self.btn.setStyleSheet("QPushButton { white-space: normal; }")

    def update_index(self, i): self.index = i
    def update_name(self, n): self.name = n; self.btn.setText(n)
    def update_command(self, c): self.command = c
    def update_color(self, c): self.color = c; self._apply_color(c)

    def start_progress(self):
        self._progress_value = 0
        self.progress_bar.setFormat("Выполняется... %p%")
        self.progress_bar.setValue(0); self.progress_bar.setVisible(True)
        self._pt.start()

    def _tick_progress(self):
        rem = 90 - self._progress_value
        if rem <= 0: return
        self._progress_value = min(90, self._progress_value + max(1, int(rem * 0.08)))
        self.progress_bar.setValue(self._progress_value)

    def finish_progress(self):
        self._pt.stop(); self._progress_value = 100
        self.progress_bar.setFormat("Готово 100%"); self.progress_bar.setValue(100)
        QTimer.singleShot(900, self._hide_progress)

    def _hide_progress(self):
        self.progress_bar.setVisible(False); self.progress_bar.setValue(0)
        self._progress_value = 0


class DragListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = getattr(parent, 'settings', None) if parent else None

    def startDrag(self, supportedActions):
        cur = self.currentItem()
        if not cur: return super().startDrag(supportedActions)
        w = self.itemWidget(cur)
        if not w: return super().startDrag(supportedActions)
        pix = w.grab()
        theme = self.settings.get_theme() if self.settings else "light"
        bc = QColor(200,200,200,180) if theme == "dark" else QColor(50,50,50,180)
        m = 4
        newp = QPixmap(pix.size() + QSize(m*2, m*2)); newp.fill(Qt.transparent)
        p = QPainter(newp); p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen); p.setBrush(QColor(0,0,0,60))
        p.drawRoundedRect(m+3, m+3, pix.width(), pix.height(), 4, 4)
        p.drawPixmap(m, m, pix)
        p.setPen(QPen(bc, 2)); p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(m, m, pix.width(), pix.height(), 4, 4); p.end()
        d = QDrag(self); d.setMimeData(self.mimeData([cur]))
        d.setPixmap(newp); d.setHotSpot(QPoint(m + pix.width()//2, m + pix.height()//2))
        d.exec(supportedActions)


# --- systemd ---
def create_systemd_service():
    sd = Path.home() / ".config" / "systemd" / "user"; sd.mkdir(parents=True, exist_ok=True)
    sf = sd / "commandlauncher.service"
    py = sys.executable
    script_dir = Path(__file__).resolve().parent
    script = Path(__file__).resolve()
    exec_cmd = f"{py} \"{script}\" --daemon"
    content = f"""[Unit]
Description=Command Launcher VK+Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory={script_dir}
ExecStart={exec_cmd}
Restart=always
RestartSec=10
StandardOutput=file:/tmp/commandlauncher_daemon.log
StandardError=file:/tmp/commandlauncher_daemon.err
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""
    try:
        sf.write_text(content, encoding="utf-8")
        subprocess.run(["systemctl","--user","daemon-reload"], check=True, capture_output=True)
        return True, "Создан"
    except Exception as e:
        return False, f"Ошибка: {e}"


def is_service_active():
    try: return subprocess.run(["systemctl","--user","is-active","commandlauncher.service"],
                               capture_output=True, text=True).returncode == 0
    except: return False
def is_service_enabled():
    try: return "enabled" in subprocess.run(
        ["systemctl","--user","is-enabled","commandlauncher.service"],
        capture_output=True, text=True).stdout
    except: return False
def start_service():
    try: subprocess.run(["systemctl","--user","start","commandlauncher.service"], check=True, capture_output=True); return True,"OK"
    except Exception as e: return False,str(e)
def stop_service():
    try: subprocess.run(["systemctl","--user","stop","commandlauncher.service"], check=True, capture_output=True); return True,"OK"
    except Exception as e: return False,str(e)
def restart_service():
    try: subprocess.run(["systemctl","--user","restart","commandlauncher.service"], check=True, capture_output=True); return True,"OK"
    except Exception as e: return False,str(e)
def enable_service():
    try: subprocess.run(["systemctl","--user","enable","commandlauncher.service"], check=True, capture_output=True); return True,"OK"
    except Exception as e: return False,str(e)
def disable_service():
    try: subprocess.run(["systemctl","--user","disable","commandlauncher.service"], check=True, capture_output=True); return True,"OK"
    except Exception as e: return False,str(e)
def get_service_status():
    try:
        r = subprocess.run(["systemctl","--user","status","commandlauncher.service"],
                           capture_output=True, text=True)
        return r.stdout + "\n" + r.stderr
    except Exception as e: return f"Ошибка: {e}"
def ensure_service():
    sf = Path.home() / ".config" / "systemd" / "user" / "commandlauncher.service"
    exp = str(Path(__file__).resolve())
    need = not sf.exists()
    if sf.exists():
        try: need = exp not in sf.read_text(encoding="utf-8")
        except Exception: need = True
    if need:
        ok, msg = create_systemd_service()
        if not ok: return False, msg
    return True, "OK"


# --- execute_command_sync ---
def execute_command_sync(command, password):
    try:
        sudo = shutil.which('sudo')
        cmd = (['sudo','-S','bash','-l','-c',command] if sudo
               else ['su','-c',f"bash -l -c '{command}'"])
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        data = password + '\n' if password else ''
        try: out, err = p.communicate(input=data, timeout=60)
        except subprocess.TimeoutExpired:
            p.kill(); out, err = p.communicate(); return out.strip(), err.strip(), -1
        return out.strip(), err.strip(), p.returncode
    except Exception as e:
        return "", str(e), -1


# --- VK ---
class VKBotCore:
    def __init__(self, token, group_id, buttons, password, log_file=None, daemon_log=None, file_logging_enabled=True):
        self.token = token; self.group_id = group_id; self.buttons = buttons
        self.password = password; self.log_file = log_file; self.daemon_log = daemon_log
        self.file_logging_enabled = bool(file_logging_enabled)
        self._stop_flag = False; self._vk = None; self.peer_ids = set()

    def stop(self): self._stop_flag = True
    def set_log_file(self, f): self.log_file = f
    def set_file_logging(self, enabled): self.file_logging_enabled = bool(enabled)

    def log(self, msg):
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        full = f"[{ts}] [VK] {msg}"; print(full)
        if self.daemon_log:
            try:
                p = Path(self.daemon_log); p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, 'a', encoding='utf-8') as f: f.write(full + "\n")
            except Exception: pass
        if self.file_logging_enabled and self.log_file:
            try:
                p = Path(self.log_file); p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, 'a', encoding='utf-8') as f: f.write(full + "\n")
            except Exception: pass

    def update_buttons(self, nb):
        old = self.buttons; self.buttons = list(nb)
        if old == self.buttons: return False
        self.log(f"🔄 Кнопки: было {len(old)}, стало {len(self.buttons)}")
        if self._vk is not None and self.peer_ids:
            for pid in list(self.peer_ids):
                try: self._send_keyboard(self._vk, pid, hint="🔄 Клавиатура обновлена.")
                except Exception: pass
        return True

    def run(self):
        if not vk_api:
            self.log("❌ vk-api не установлена"); return
        while not self._stop_flag:
            try:
                try: gid = int(self.group_id)
                except ValueError:
                    self.log(f"❌ ID группы должен быть числом"); time.sleep(10); continue
                sess = vk_api.VkApi(token=self.token)
                vk = sess.get_api(); self._vk = vk
                lp = VkBotLongPoll(sess, gid, wait=1)
                self.log("✅ VK бот запущен")
                while not self._stop_flag:
                    try:
                        for ev in lp.check():
                            if self._stop_flag: break
                            if ev.type == VkBotEventType.MESSAGE_NEW:
                                self._handle(ev, vk)
                    except Exception as e:
                        if not self._stop_flag: self.log(f"❌ longpoll: {e}"); break
                        else: break
                if self._stop_flag: break
                time.sleep(5)
            except Exception as e:
                self.log(f"❌ Запуск: {e}"); time.sleep(10)
        self.log("⛔ VK бот остановлен")

    def _handle(self, event, vk):
        obj = event.object
        if not obj: return
        msg = obj.get('message')
        if not msg: return
        text = msg.get('text','').strip(); pid = msg.get('peer_id')
        if pid is not None: self.peer_ids.add(pid)
        if not text or pid is None: return
        self.log(f"📩 '{text}' (peer_id={pid})")
        if text == "/list":
            if self.buttons:
                self._send(vk, pid, "Доступные команды:\n" +
                           "\n".join(b["name"] for b in self.buttons))
            else: self._send(vk, pid, "Нет команд")
            return
        if text in ("/start", "/menu"):
            self._send_keyboard(vk, pid); return
        for b in self.buttons:
            if b["name"] == text:
                self._execute(vk, pid, b["command"]); return
        self._send_keyboard(vk, pid, hint="Неизвестная команда.")

    def _send_keyboard(self, vk, pid, hint=None):
        if not VkKeyboard: return
        kb = VkKeyboard(one_time=False)
        for i, b in enumerate(self.buttons):
            name = b["name"]
            if len(name) > MAX_BUTTON_NAME_LENGTH:
                name = name[:MAX_BUTTON_NAME_LENGTH-1] + "…"
            kb.add_button(name, color=VkKeyboardColor.PRIMARY)
            if i != len(self.buttons)-1: kb.add_line()
        if not self.buttons: kb.add_button("Нет команд", color=VkKeyboardColor.SECONDARY)
        self._send(vk, pid, hint or "Выберите команду:", keyboard=kb)

    def _send(self, vk, pid, text, keyboard=None):
        try:
            params = {'peer_id': pid, 'message': text, 'random_id': 0}
            if keyboard is not None: params['keyboard'] = keyboard.get_keyboard()
            vk.messages.send(**params)
        except Exception as e: self.log(f"❌ Отправка: {e}")

    def _execute(self, vk, pid, command):
        self.log(f"🔄 {command}")
        out, err, rc = execute_command_sync(command, self.password)
        if rc == 0: self._send(vk, pid, out if out else "Готово")
        else: self._send(vk, pid, f"Ошибка ({rc}):\n{err or out}")


# --- Telegram ---
class TelegramBotCore:
    def __init__(self, token, buttons, password, log_file=None, daemon_log=None,
                 proxy_manager=None, file_logging_enabled=True):
        self.token = token; self.buttons = buttons; self.password = password
        self.log_file = log_file; self.daemon_log = daemon_log
        self.file_logging_enabled = bool(file_logging_enabled)
        self.pm = proxy_manager
        self._stop_flag = False; self._bot = None; self.chat_ids = set()
        self._last_err = None; self._last_err_time = 0.0
        self._proxy_pause = True
        self._wake_event = threading.Event()

    def stop(self):
        self._stop_flag = True
        self._wake_event.set()
        if self._bot is not None:
            try: self._bot.stop_polling()
            except Exception: pass

    def on_proxy_disabled(self):
        """Останавливает ТОЛЬКО Telegram-опрос. VK и другие потоки не трогает."""
        self._proxy_pause = True
        self._wake_event.set()
        if self._bot is not None:
            try: self._bot.stop_polling()
            except Exception: pass
        self.log("⛔ Прокси Telegram отключён. Бот Telegram приостановлен.")

    def on_proxy_enabled(self):
        self._proxy_pause = False
        self._wake_event.set()
        self.log("▶ Прокси Telegram включён. Бот Telegram будет переподключён.")

    def on_proxy_config_changed(self):
        self._wake_event.set()
        if self._bot is not None:
            try: self._bot.stop_polling()
            except Exception: pass

    def set_log_file(self, f): self.log_file = f
    def set_file_logging(self, enabled): self.file_logging_enabled = bool(enabled)

    def log(self, msg):
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        full = f"[{ts}] [TG] {msg}"; print(full)
        if self.daemon_log:
            try:
                p = Path(self.daemon_log); p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, 'a', encoding='utf-8') as f: f.write(full + "\n")
            except Exception: pass
        if self.file_logging_enabled and self.log_file:
            try:
                p = Path(self.log_file); p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, 'a', encoding='utf-8') as f: f.write(full + "\n")
            except Exception: pass

    def log_throttled(self, msg):
        now = time.time()
        if msg == self._last_err and (now - self._last_err_time) < 60: return
        self._last_err = msg; self._last_err_time = now; self.log(msg)

    def _get_proxy_state(self):
        if not self.pm:
            return None, False, "менеджер прокси не задан"
        try:
            return self.pm.telegram_proxy_state()
        except Exception as e:
            self.log(f"⚠️ Ошибка определения Telegram-прокси: {type(e).__name__}: {e}")
            return None, True, "ошибка определения прокси"

    def _apply_proxy(self):
        if not telebot:
            return None, False, "telebot не установлен"
        url, required, reason = self._get_proxy_state()
        try:
            telebot.apihelper.session = None
        except Exception:
            pass
        if url:
            telebot.apihelper.proxy = {'http': url, 'https': url}
            self.log(f"🌐 Telegram → прокси: {url} ({reason})")
        else:
            telebot.apihelper.proxy = {}
            if required:
                self.log(f"⛔ Telegram → работа запрещена: {reason}")
            else:
                self.log("🌐 Telegram → прямое соединение (прокси не выбран)")
        return url, required, reason

    def _test_telegram_connection(self, proxy_url=None, timeout=5):
        if not requests:
            return False, "requests не установлен"
        if proxy_url and proxy_url.startswith("socks") and not _HAVE_SOCKS:
            return False, "Для SOCKS5 нужен PySocks: pip install -U requests[socks] PySocks"
        proxies = {'http': proxy_url, 'https': proxy_url} if proxy_url else None
        session = requests.Session()
        session.trust_env = False
        try:
            t0 = time.perf_counter()
            r = session.get("https://api.telegram.org", proxies=proxies, timeout=timeout,
                            allow_redirects=False)
            ms = int((time.perf_counter() - t0) * 1000)
            return True, f"HTTP {r.status_code}, {ms} мс"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
        finally:
            session.close()

    def update_buttons(self, nb):
        old = self.buttons; self.buttons = list(nb)
        if old == self.buttons: return False
        self.log(f"🔄 Кнопки: было {len(old)}, стало {len(self.buttons)}")
        if self._bot is not None and self.chat_ids:
            for cid in list(self.chat_ids):
                try: self._send_keyboard(cid, hint="🔄 Клавиатура обновлена.")
                except Exception: pass
        return True

    def run(self):
        if not telebot:
            self.log("❌ pyTelegramBotAPI не установлена")
            return

        while not self._stop_flag:
            try:
                try:
                    enabled = self.pm.settings.get_telegram_proxy_enabled() if self.pm else False
                except Exception:
                    enabled = False
                self._proxy_pause = not enabled

                if self._proxy_pause:
                    self._wake_event.clear()
                    self._wake_event.wait(1.0)
                    continue

                proxy_url, required, reason = self._apply_proxy()
                if required and not proxy_url:
                    self.log_throttled(f"⚠️ Telegram ожидает рабочий прокси: {reason}")
                    self._wake_event.clear(); self._wake_event.wait(1.0)
                    continue

                ok, info = self._test_telegram_connection(proxy_url, timeout=5)
                if not ok:
                    self.log_throttled(
                        f"⚠️ Telegram API недоступен через {proxy_url}: {info}"
                    )
                    self._wake_event.clear(); self._wake_event.wait(2.0)
                    continue

                self.log(f"✅ Telegram API доступен: {info}")
                bot = telebot.TeleBot(self.token)
                self._bot = bot
                bot.register_message_handler(self._handle_start, commands=['start', 'menu'])
                bot.register_message_handler(self._handle_list, commands=['list'])
                bot.register_message_handler(self._handle_text, func=lambda m: True)
                self.log("✅ TG бот запущен и подключён к Telegram")

                try:
                    bot.polling(
                        non_stop=True, interval=1, timeout=10,
                        long_polling_timeout=10, skip_pending=True
                    )
                except Exception as e:
                    if not self._stop_flag:
                        self.log_throttled(f"❌ polling: {type(e).__name__}: {str(e)[:300]}")
                finally:
                    self._bot = None

                if self._stop_flag:
                    break
                time.sleep(0.2)
            except Exception as e:
                if self._stop_flag:
                    break
                self.log_throttled(f"❌ Запуск TG: {type(e).__name__}: {str(e)[:300]}")
                self._bot = None
                time.sleep(1)

        self.log("⛔ TG бот остановлен")

    def _handle_start(self, message):
        try:
            self.chat_ids.add(message.chat.id)
            self._send_keyboard(message.chat.id)
        except Exception as e: self.log(f"Ошибка /start: {e}")

    def _handle_list(self, message):
        try:
            self.chat_ids.add(message.chat.id)
            if self.buttons:
                self._send(message.chat.id, "Доступные команды:\n" +
                           "\n".join(b["name"] for b in self.buttons))
            else: self._send(message.chat.id, "Нет команд")
        except Exception as e: self.log(f"Ошибка /list: {e}")

    def _handle_text(self, message):
        try:
            text = message.text.strip() if message.text else ""
            if not text: return
            cid = message.chat.id; self.chat_ids.add(cid)
            self.log(f"📩 '{text}' (chat_id={cid})")
            for b in self.buttons:
                if b["name"] == text:
                    self._execute(cid, b["command"]); return
            self._send_keyboard(cid, hint="Неизвестная команда.")
        except Exception as e: self.log(f"Ошибка обработки: {e}")

    def _send_keyboard(self, cid, hint=None):
        if not telebot_types or self._bot is None: return
        kb = telebot_types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        rows = []
        current = []
        current_len = 0
        for b in self.buttons:
            name = str(b.get("name", ""))
            btn = telebot_types.KeyboardButton(name)
            if len(name) >= 18:
                if current:
                    rows.append(current); current = []; current_len = 0
                rows.append([btn])
                continue
            if current and (len(current) >= 2 or current_len + len(name) + 2 > 28):
                rows.append(current); current = []; current_len = 0
            current.append(btn)
            current_len += len(name) + 2
        if current:
            rows.append(current)
        if not rows:
            rows = [[telebot_types.KeyboardButton("Нет команд")]]
        for row in rows:
            kb.row(*row)
        try: self._bot.send_message(cid, hint or "Выберите команду:", reply_markup=kb)
        except Exception as e: self.log(f"❌ Отправка клавиатуры: {e}")

    def _send(self, cid, text):
        if self._bot is None: return
        try:
            if text is None: text = ""
            if len(text) > 4000: text = text[:4000] + "\n… (обрезано)"
            self._bot.send_message(cid, text)
        except Exception as e: self.log(f"❌ Отправка: {e}")

    def _execute(self, cid, command):
        self.log(f"🔄 {command}")
        out, err, rc = execute_command_sync(command, self.password)
        if rc == 0: self._send(cid, out if out else "Готово")
        else: self._send(cid, f"Ошибка ({rc}):\n{err or out}")


# --- daemon ---
def run_daemon():
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(DAEMON_LOG, 'a', encoding='utf-8') as f:
            f.write(f"[{datetime.datetime.now()}] 🚀 Демон запущен\n")
    except: pass
    stop_event = threading.Event()
    def sigh(sig, frame): stop_event.set()
    signal.signal(signal.SIGINT, sigh); signal.signal(signal.SIGTERM, sigh)

    settings = SettingsManager()
    pm = ProxyManager(settings)

    if settings.get_proxy_configs():
        pm.start_auto_ping()

    vk_bot = None; vk_thread = None
    if settings.get_vk_enabled():
        if not vk_api:
            with open(DAEMON_LOG,'a',encoding='utf-8') as f:
                f.write(f"[{datetime.datetime.now()}] ❌ VK: vk-api не установлена\n")
        elif not settings.get_vk_token() or not settings.get_vk_group_id():
            with open(DAEMON_LOG,'a',encoding='utf-8') as f:
                f.write(f"[{datetime.datetime.now()}] ⚠️ VK: нет токена/ID\n")
        else:
            vk_bot = VKBotCore(settings.get_vk_token(), settings.get_vk_group_id(),
                               settings.get_buttons(), settings.get_password(),
                               settings.get_vk_log_file(),
                               str(DAEMON_LOG),
                               settings.get_vk_log_enabled())
            vk_thread = threading.Thread(target=vk_bot.run, daemon=True)
            vk_thread.start()

    tg_bot = None; tg_thread = None
    if settings.get_telegram_enabled():
        if not telebot:
            with open(DAEMON_LOG,'a',encoding='utf-8') as f:
                f.write(f"[{datetime.datetime.now()}] ❌ TG: pyTelegramBotAPI не установлена\n")
        elif not settings.get_telegram_token():
            with open(DAEMON_LOG,'a',encoding='utf-8') as f:
                f.write(f"[{datetime.datetime.now()}] ⚠️ TG: нет токена\n")
        else:
            tg_bot = TelegramBotCore(
                settings.get_telegram_token(), settings.get_buttons(),
                settings.get_password(),
                settings.get_telegram_log_file(),
                str(DAEMON_LOG), proxy_manager=pm,
                file_logging_enabled=settings.get_telegram_log_enabled())
            tg_bot._proxy_pause = not settings.get_telegram_proxy_enabled()
            tg_thread = threading.Thread(target=tg_bot.run, daemon=True)
            tg_thread.start()

    def reload_loop():
        """При изменении config.json обновляем только то, что реально изменилось.
        Изменения, касающиеся прокси Telegram, влияют ИСКЛЮЧИТЕЛЬНО на TG-поток —
        VK-бот продолжает работать без перезапуска."""
        last = 0
        last_proxy_state = None
        last_vk_state = None
        while not stop_event.is_set():
            try:
                if CONFIG_FILE.exists():
                    m = CONFIG_FILE.stat().st_mtime
                    if m != last:
                        last = m; settings.load()
                        btns = settings.get_buttons()
                        if vk_bot:
                            vk_bot.update_buttons(btns)
                            vk_bot.set_log_file(settings.get_vk_log_file())
                            vk_bot.set_file_logging(settings.get_vk_log_enabled())
                        if tg_bot:
                            tg_bot.update_buttons(btns)
                            tg_bot.set_log_file(settings.get_telegram_log_file())
                            tg_bot.set_file_logging(settings.get_telegram_log_enabled())

                            proxy_state = (
                                settings.get_telegram_proxy_enabled(),
                                settings.get_active_proxy_id(),
                                settings.get_auto_select_proxy(),
                            )
                            if last_proxy_state is None:
                                last_proxy_state = proxy_state
                            elif proxy_state != last_proxy_state:
                                was_enabled = last_proxy_state[0]
                                is_enabled = proxy_state[0]
                                if is_enabled and not was_enabled:
                                    tg_bot.on_proxy_enabled()
                                elif was_enabled and not is_enabled:
                                    tg_bot.on_proxy_disabled()
                                else:
                                    tg_bot.on_proxy_config_changed()
                                last_proxy_state = proxy_state
                        # VK-поток здесь сознательно НЕ перезапускается и НЕ
                        # останавливается — только обновляются кнопки/лог-настройки.
            except Exception as e:
                if tg_bot:
                    tg_bot.log_throttled(f"⚠️ reload config: {type(e).__name__}: {str(e)[:200]}")
            stop_event.wait(0.5)
    threading.Thread(target=reload_loop, daemon=True).start()
    stop_event.wait()
    pm.stop_auto_ping(); pm.stop_telegram_xray()
    if vk_bot: vk_bot.stop()
    if tg_bot: tg_bot.stop()
    if vk_thread: vk_thread.join(timeout=3)
    if tg_thread: tg_thread.join(timeout=3)
    with open(DAEMON_LOG,'a',encoding='utf-8') as f:
        f.write(f"[{datetime.datetime.now()}] ⛔ Демон остановлен\n")


# --- MainWindow ---
class MainWindow(QMainWindow):
    def __init__(self, minimized=False):
        super().__init__()
        self.settings = SettingsManager()
        self.proxy_manager = ProxyManager(self.settings)

        try:
            if self.settings.get_proxy_configs():
                self.proxy_manager.start_auto_ping()
        except Exception:
            pass

        self.setWindowTitle("Command Launcher")
        self.resize(900, 600)
        self.settings_dialog = None
        self.terminal_widget = None
        self._command_widgets = []
        self.init_ui(); self.load_buttons()
        self.apply_theme(self.settings.get_theme())
        self.sync_service_state()
        if minimized: self.hide()
        else: self.show()
        self.create_tray_icon()

    def init_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        ml = QVBoxLayout(central); ml.setContentsMargins(5,5,5,5); ml.setSpacing(5)
        tl = QHBoxLayout()
        self.settings_btn = QPushButton("⚙ Настройки"); self.settings_btn.clicked.connect(self.open_settings)
        self.create_btn = QPushButton("➕ Создать кнопку"); self.create_btn.clicked.connect(self.create_button)
        self.terminal_btn = QPushButton("🖥 Терминал"); self.terminal_btn.clicked.connect(self.open_terminal)
        for b in (self.settings_btn, self.create_btn, self.terminal_btn):
            tl.addWidget(b)
        tl.addStretch(); ml.addLayout(tl)
        w = max(b.sizeHint().width() for b in (self.settings_btn, self.create_btn,
                                                self.terminal_btn))
        h = max(b.sizeHint().height() for b in (self.settings_btn, self.create_btn,
                                                 self.terminal_btn))
        for b in (self.settings_btn, self.create_btn, self.terminal_btn):
            b.setFixedSize(w, h)
        self.splitter = QSplitter(Qt.Horizontal)
        self.left_widget = QWidget()
        ll = QVBoxLayout(self.left_widget); ll.setContentsMargins(5,5,5,5); ll.setSpacing(5)
        lab = QLabel("Команды"); lab.setAlignment(Qt.AlignCenter); ll.addWidget(lab)
        self.list_widget = DragListWidget(self)
        self.list_widget.setDragDropMode(QListWidget.InternalMove)
        self.list_widget.setResizeMode(QListWidget.Adjust)
        self.list_widget.setSpacing(5); self.list_widget.setMinimumWidth(200)
        self.list_widget.setStyleSheet("QListWidget::item { height: 65px; }")
        self.list_widget.model().rowsMoved.connect(self.on_rows_moved)
        ll.addWidget(self.list_widget)
        self.left_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.splitter.addWidget(self.left_widget)
        self.stacked_widget = QStackedWidget()
        self.splitter.addWidget(self.stacked_widget)
        self.splitter.setSizes(self.settings.get_splitter_sizes())
        ml.addWidget(self.splitter)
        self.splitter.splitterMoved.connect(
            lambda p,i: self.settings.set_splitter_sizes(self.splitter.sizes()))
        self.update_minimum_width()

    def create_tray_icon(self):
        if not QSystemTrayIcon.isSystemTrayAvailable(): return
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.style().standardIcon(QStyle.SP_ComputerIcon))
        m = QMenu()
        a = QAction("Показать", self); a.triggered.connect(self.show_window); m.addAction(a)
        a = QAction("Скрыть", self); a.triggered.connect(self.hide); m.addAction(a)
        m.addSeparator()
        a = QAction("Выйти", self); a.triggered.connect(self.quit_application); m.addAction(a)
        self.tray_icon.setContextMenu(m)
        self.tray_icon.activated.connect(
            lambda r: self.show_window() if r == QSystemTrayIcon.DoubleClick else None)
        self.tray_icon.show()

    def show_window(self):
        self.show(); self.raise_(); self.activateWindow()

    def quit_application(self):
        if self.terminal_widget: self.terminal_widget.close_terminal()
        self.stop_all_terminals()
        if self.proxy_manager: self.proxy_manager.stop_auto_ping()
        if hasattr(self, 'tray_icon'): self.tray_icon.hide()
        QApplication.quit()

    def sync_service_state(self):
        ensure_service()
        on = self.settings.get_vk_enabled() or self.settings.get_telegram_enabled()
        if on:
            if is_service_active(): restart_service()
            else: start_service()
        elif is_service_active():
            stop_service()
        auto = self.settings.get_vk_autostart() or self.settings.get_telegram_autostart()
        if auto and not is_service_enabled(): enable_service()
        elif not auto and is_service_enabled(): disable_service()

    def update_minimum_width(self):
        if not self.settings.get_buttons():
            self.left_widget.setMinimumWidth(200); return
        f = QFont()
        for i in range(self.list_widget.count()):
            w = self.list_widget.itemWidget(self.list_widget.item(i))
            if isinstance(w, ButtonItemWidget): f = w.btn.font(); break
        m = QFontMetrics(f); mx = 0
        for i in range(self.list_widget.count()):
            w = self.list_widget.itemWidget(self.list_widget.item(i))
            if isinstance(w, ButtonItemWidget):
                mx = max(mx, m.horizontalAdvance(w.btn.text()))
        total = min(mx + 130, 500)
        self.left_widget.setMinimumWidth(total)
        self.list_widget.setMinimumWidth(total - 10)

    def open_terminal(self):
        if self.terminal_widget is None:
            self.terminal_widget = InteractiveTerminal(
                lambda: self.settings.get_password(), self)
            self.terminal_widget.create_button_requested.connect(
                self.create_button_from_terminal_selection)
            self.stacked_widget.addWidget(self.terminal_widget)
        else:
            if not self.terminal_widget.is_running: self.terminal_widget.start_terminal()
            self.stacked_widget.setCurrentWidget(self.terminal_widget)
        self.terminal_widget.terminal_text.setFocus()

    def stop_all_terminals(self):
        for i in range(self.stacked_widget.count()):
            w = self.stacked_widget.widget(i)
            if isinstance(w, CommandTerminalWidget): w.stop_and_cleanup()

    def load_buttons(self):
        data = self.settings.get_buttons()
        avail = {}
        for t in self._command_widgets:
            key = (t.button_name, t.command)
            avail.setdefault(key, []).append(t)
            self.stacked_widget.removeWidget(t)
        old = list(self._command_widgets); new = []
        self.list_widget.clear()
        for idx, d in enumerate(data):
            w = self.add_button_item(idx, d["name"], d["command"], d.get("color"))
            key = (d["name"], d["command"]); cand = avail.get(key, [])
            if cand:
                t = cand.pop(0); t.update_button_name(d["name"]); t.command = d["command"]
                self.stacked_widget.addWidget(t)
            else:
                t = self.create_command_terminal(d["name"], d["command"])
                self.stacked_widget.addWidget(t)
            t.progress_started.connect(w.start_progress)
            t.progress_finished.connect(w.finish_progress)
            if t.is_running: w.start_progress()
            new.append(t)
        keep = {id(w) for w in new}
        for t in old:
            if id(t) not in keep: t.stop_and_cleanup(); t.deleteLater()
        self._command_widgets = new
        if self.terminal_widget is not None:
            self.stacked_widget.addWidget(self.terminal_widget)
        self.update_minimum_width()

    def add_button_item(self, index, name, command, color):
        it = QListWidgetItem(self.list_widget); it.setSizeHint(QSize(0, 74))
        w = ButtonItemWidget(name, command, color, index, self)
        self.list_widget.setItemWidget(it, w); it.setData(Qt.UserRole, index)
        return w

    def create_command_terminal(self, name, command):
        t = CommandTerminalWidget(name, command,
                                  lambda: self.settings.get_password(), self)
        t.create_button_requested.connect(self.create_button_from_terminal_selection)
        return t

    def on_rows_moved(self, parent, start, end, dest, row):
        # ВАЖНО: Qt для QListWidget не делает "настоящий" moveRows при
        # внутреннем drag&drop — старые QListWidgetItem удаляются и
        # создаются заново на новых позициях, а виджеты, назначенные через
        # setItemWidget(), с ними не переезжают и могут "слипнуться" не с
        # тем рядом (отсюда баг: после переноса кнопки наверх срабатывает
        # кнопка, которая физически осталась под ней). Поэтому здесь мы
        # НЕ доверяем текущим itemWidget-ам, а определяем новый порядок по
        # data(Qt.UserRole) — эти данные копируются вместе с элементом
        # штатным механизмом Qt — и затем полностью пересобираем список.
        # Пересборку откладываем на следующий цикл событий, т.к. в момент
        # сигнала rowsMoved Qt ещё завершает внутреннюю операцию drop.
        QTimer.singleShot(0, self._commit_reorder)

    def _commit_reorder(self):
        self.list_widget.clearSelection()
        btns = self.settings.get_buttons()
        if not btns: return
        new = []
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if it is None: continue
            data_idx = it.data(Qt.UserRole)
            if isinstance(data_idx, int) and 0 <= data_idx < len(btns):
                new.append(btns[data_idx])
        if len(new) == len(btns):
            self.settings.set_buttons(new)
            self.load_buttons()

    def on_button_clicked(self, index):
        if index < len(self._command_widgets):
            t = self._command_widgets[index]
            if isinstance(t, CommandTerminalWidget):
                self.stacked_widget.setCurrentWidget(t); t.activate()

    def delete_button(self, index):
        if QMessageBox.question(self, "Удаление", "Удалить кнопку?") != QMessageBox.Yes: return
        if index < len(self._command_widgets):
            t = self._command_widgets[index]
            if isinstance(t, CommandTerminalWidget): t.stop_and_cleanup()
            self._command_widgets.pop(index)
        self.settings.remove_button(index); self.load_buttons()

    def edit_button(self, index):
        btns = self.settings.get_buttons()
        if index >= len(btns): return
        d = CreateButtonDialog(self, name=btns[index]["name"],
                               command=btns[index]["command"],
                               color=btns[index].get("color"))
        if d.exec() == QDialog.Accepted:
            name, cmd, col = d.get_data()
            if not name or not cmd: return
            if len(name) > MAX_BUTTON_NAME_LENGTH: return
            self.settings.update_button(index, name, cmd, col)
            if index < len(self._command_widgets):
                t = self._command_widgets[index]
                if isinstance(t, CommandTerminalWidget):
                    t.update_button_name(name); t.update_command(cmd)
            it = self.list_widget.item(index)
            if it:
                w = self.list_widget.itemWidget(it)
                if isinstance(w, ButtonItemWidget):
                    w.update_name(name); w.update_command(cmd); w.update_color(col)
            self.update_minimum_width()

    def create_button_from_terminal_selection(self, command):
        command = command.strip()
        if not command: return
        dn = command.replace('\n',' ; ')
        if len(dn) > MAX_BUTTON_NAME_LENGTH:
            dn = dn[:MAX_BUTTON_NAME_LENGTH-1] + "…"
        d = CreateButtonDialog(self, name=dn, command=command)
        if d.exec() == QDialog.Accepted:
            name, cmd, col = d.get_data()
            if not name or not cmd: return
            self.settings.add_button(name, cmd, col); self.load_buttons()

    def create_button(self):
        d = CreateButtonDialog(self)
        if d.exec() == QDialog.Accepted:
            name, cmd, col = d.get_data()
            if not name or not cmd: return
            self.settings.add_button(name, cmd, col); self.load_buttons()

    def open_settings(self):
        if self.settings_dialog is not None:
            self.settings_dialog.raise_(); self.settings_dialog.activateWindow(); return
        d = SettingsDialog(self.settings, self.proxy_manager, self)
        self.settings_dialog = d
        d.accepted.connect(lambda: self.apply_theme(self.settings.get_theme()))
        d.destroyed.connect(lambda: setattr(self, "settings_dialog", None))
        d.show()

    def apply_theme(self, theme):
        if theme == "dark": self.setStyleSheet(self.dark_style())
        elif theme == "custom":
            self.setStyleSheet(self.custom_style(self.settings.get_custom_theme()))
        else: self.setStyleSheet(self.light_style())

    @staticmethod
    def _scrollbar_qss(track, handle, hover):
        # Скроллбар слегка шире стандартного системного и с чуть более
        # крупным "пальцем" (min-height 20px) — этого достаточно, чтобы
        # было легко попасть мышью и потащить вверх/вниз, но при этом он
        # не выглядит громоздким.
        return f"""
        QScrollBar:vertical {{
            background: {track}; width: 9px; margin: 0px; border-radius: 4px;
        }}
        QScrollBar::handle:vertical {{
            background: {handle}; min-height: 20px; border-radius: 4px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {hover}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
        QScrollBar:horizontal {{
            background: {track}; height: 9px; margin: 0px; border-radius: 4px;
        }}
        QScrollBar::handle:horizontal {{
            background: {handle}; min-width: 20px; border-radius: 4px;
        }}
        QScrollBar::handle:horizontal:hover {{ background: {hover}; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}
        """

    def light_style(self):
        return """
        QWidget { background-color: #f0f0f0; color: #000000; }
        QLineEdit, QTextEdit, QComboBox, QPushButton, QPlainTextEdit, QSpinBox {
            background-color: #ffffff; border: 1px solid #cccccc; border-radius: 4px;
            padding: 5px; }
        QPushButton { background-color: #e0e0e0; color: #000000; white-space: normal; }
        QPushButton:hover { background-color: #d0d0d0; }
        QFrame { background-color: #ffffff; border: 1px solid #cccccc; }
        QDialog { background-color: #f0f0f0; }
        QLabel { color: #000000; }
        QTextEdit, QPlainTextEdit { background-color: #ffffff; color: #000000;
            border: 1px solid #cccccc; }
        QListWidget { background-color: #ffffff; border: 1px solid #cccccc; }
        QListWidget::item:selected { background-color: #d0d0d0; }
        QStackedWidget { background-color: #f0f0f0; }
        QGroupBox { background-color: #f0f0f0; border: 1px solid #cccccc; margin-top: 1ex; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
        QProgressBar { background-color: #ffffff; border: 1px solid #cccccc;
            border-radius: 4px; text-align: center; color: #000000; font-size: 9px; }
        QProgressBar::chunk { background-color: #0078d7; border-radius: 3px; }
        QTableWidget { background-color: #ffffff; border: 1px solid #cccccc; }
        """ + self._scrollbar_qss("#f0f0f0", "#c0c0c0", "#0078d7")
    def dark_style(self):
        return """
        QWidget { background-color: #2b2b2b; color: #ffffff; }
        QLineEdit, QTextEdit, QComboBox, QPushButton, QPlainTextEdit, QSpinBox {
            background-color: #3c3c3c; border: 1px solid #555555; border-radius: 4px;
            padding: 5px; color: #ffffff; }
        QPushButton { background-color: #4a4a4a; color: #ffffff; white-space: normal; }
        QPushButton:hover { background-color: #5a5a5a; }
        QFrame { background-color: #3c3c3c; border: 1px solid #555555; }
        QDialog { background-color: #2b2b2b; }
        QLabel { color: #ffffff; }
        QTextEdit, QPlainTextEdit { background-color: #3c3c3c; color: #ffffff;
            border: 1px solid #555555; }
        QListWidget { background-color: #3c3c3c; border: 1px solid #555555; }
        QListWidget::item:selected { background-color: #4a4a4a; }
        QStackedWidget { background-color: #2b2b2b; }
        QGroupBox { background-color: #2b2b2b; border: 1px solid #555555; margin-top: 1ex; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
        QProgressBar { background-color: #3c3c3c; border: 1px solid #555555;
            border-radius: 4px; text-align: center; color: #ffffff; font-size: 9px; }
        QProgressBar::chunk { background-color: #4a90d9; border-radius: 3px; }
        QTableWidget { background-color: #3c3c3c; border: 1px solid #555555; color: #ffffff; }
        """ + self._scrollbar_qss("#2b2b2b", "#5a5a5a", "#4a90d9")
    def custom_style(self, c):
        main = c.get("main_color","#0078d7"); bg = c.get("background","#f0f0f0")
        fg = c.get("foreground","#000000"); bfg = c.get("button_fg","#ffffff")
        fbg = c.get("frame_bg","#ffffff"); ibg = c.get("input_bg","#ffffff")
        ib = c.get("input_border","#cccccc")
        return f"""
        QWidget {{ background-color: {bg}; color: {fg}; }}
        QLineEdit, QTextEdit, QComboBox, QPushButton, QPlainTextEdit, QSpinBox {{
            background-color: {ibg}; border: 1px solid {ib}; border-radius: 4px;
            padding: 5px; color: {fg}; }}
        QPushButton {{ background-color: {main}; color: {bfg}; white-space: normal; }}
        QPushButton:hover {{ background-color: {ib}; }}
        QFrame {{ background-color: {fbg}; border: 1px solid {ib}; }}
        QDialog {{ background-color: {bg}; }}
        QLabel {{ color: {fg}; }}
        QTextEdit, QPlainTextEdit {{ background-color: {ibg}; color: {fg};
            border: 1px solid {ib}; }}
        QListWidget {{ background-color: {ibg}; border: 1px solid {ib}; }}
        QListWidget::item:selected {{ background-color: {ib}; }}
        QStackedWidget {{ background-color: {bg}; }}
        QGroupBox {{ background-color: {bg}; border: 1px solid {ib}; margin-top: 1ex; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; }}
        QProgressBar {{ background-color: {ibg}; border: 1px solid {ib};
            border-radius: 4px; text-align: center; color: {fg}; font-size: 9px; }}
        QProgressBar::chunk {{ background-color: {main}; border-radius: 3px; }}
        QTableWidget {{ background-color: {ibg}; border: 1px solid {ib}; color: {fg}; }}
        """ + self._scrollbar_qss(bg, ib, main)

    def closeEvent(self, event):
        event.ignore(); self.hide()
        if hasattr(self, 'tray_icon') and self.tray_icon.isVisible():
            self.tray_icon.showMessage("Command Launcher",
                "Программа работает в фоне.", QSystemTrayIcon.Information, 2000)


def main():
    if '--daemon' in sys.argv:
        try:
            with open(DAEMON_LOG,'a',encoding='utf-8') as f:
                f.write(f"[{datetime.datetime.now()}] Запуск демона: {sys.argv}\n")
        except: pass
        run_daemon(); return
    app = QApplication(sys.argv)
    app.setApplicationName("Command Launcher")
    app.setOrganizationName("MyApp")
    MainWindow(minimized="--minimized" in sys.argv)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()