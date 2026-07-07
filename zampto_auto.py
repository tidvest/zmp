import os, re, logging, random, json, time
import imaplib, email, urllib.request, urllib.error, base64
from email.header import decode_header
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ---------- 环境变量 ----------
USERNAME  = os.environ["ZAMPTO_USERNAME"]
PASSWORD  = os.environ["ZAMPTO_PASSWORD"]
SERVER_ID = os.environ.get("ZAMPTO_SERVER_ID", "")

WXPUSHER_TOKEN = os.environ.get("WXPUSHER_TOKEN", "")
WXPUSHER_UID   = os.environ.get("WXPUSHER_UID", "")
SKIP_RENEW     = os.environ.get("SKIP_RENEW", "false").lower() == "true"

# 126邮箱 IMAP（用于读取 Zampto 登录验证码）
IMAP_HOST     = "imap.126.com"
IMAP_PORT     = 993
IMAP_USER     = os.environ.get("ZAMPTO_IMAP_USER", USERNAME)   # 默认和登录邮箱相同
IMAP_PASSWORD = os.environ.get("ZAMPTO_IMAP_PASSWORD", "")     # 126邮箱授权码（非登录密码）

BASE_URL    = "https://dash.zampto.net"
AUTH_URL    = "https://dash.zampto.net/auth/login"
SERVERS_URL = f"{BASE_URL}/servers"

SCREENSHOT_DIR = Path("./screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)

# ---------- WxPusher ----------
def wxpush(content: str):
    if not WXPUSHER_TOKEN or not WXPUSHER_UID:
        log.warning("📨 WXPUSHER_TOKEN 或 WXPUSHER_UID 未配置，跳过推送")
        return
    import urllib.request
    payload = json.dumps({
        "appToken": WXPUSHER_TOKEN,
        "content":  content,
        "contentType": 1,
        "uids": [WXPUSHER_UID],
    }).encode()
    try:
        req = urllib.request.Request(
            "https://wxpusher.zjiecode.com/api/send/message",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("success"):
                log.info("📨 WxPusher 推送成功")
            else:
                log.warning(f"📨 WxPusher 推送失败: {result}")
    except Exception as e:
        log.warning(f"📨 WxPusher 推送异常: {e}")

# ---------- 工具函数 ----------
def redact_sensitive_info(page):
    try:
        page.evaluate("""() => {
            var cards = document.querySelectorAll('.user-info-grid .info-card .info-content');
            cards.forEach(function(card) {
                var p = card.querySelector('p');
                if (p) p.textContent = '***';
                var pStyle = card.querySelector('p[style]');
                if (pStyle) pStyle.textContent = '***';
            });

            var addrEl = document.getElementById('addressValue');
            if (addrEl) addrEl.textContent = '***';

            document.querySelectorAll('.info-card-value').forEach(function(el) {
                if (/\\.zampto\\.net/.test(el.textContent)) {
                    el.textContent = '***';
                }
            });
        }""")
    except Exception as e:
        log.warning(f"脱敏 JS 执行失败（不影响截图）: {e}")


def take_screenshot(page, name):
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str(SCREENSHOT_DIR / f"{ts}_{name}.png")
        redact_sensitive_info(page)
        page.screenshot(path=path, full_page=False)
        log.info(f"📸 截图: {path}")
    except Exception as e:
        log.warning(f"截图失败: {e}")

def get_text(page) -> str:
    try:
        return page.inner_text("body") or ""
    except:
        return ""

def human_delay(min_s=0.5, max_s=1.2):
    time.sleep(random.uniform(min_s, max_s))

def tcp_check(host: str, port: int, timeout: int = 5) -> bool:
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False

def wait_for_port(host: str, port: int, max_wait: int = 120, interval: int = 10) -> bool:
    log.info(f"🔌 等待端口可连接（最多 {max_wait}s）...")
    elapsed = 0
    while elapsed < max_wait:
        if tcp_check(host, port):
            log.info(f"✅ 端口已可连接（等待了 {elapsed}s）")
            return True
        time.sleep(interval)
        elapsed += interval
        log.info(f"  [{elapsed}s] 端口还未开放，继续等待...")
    log.warning(f"⚠️ 端口等待超时（{max_wait}s）")
    return False

def wait_for_url_contains(page, keyword, timeout=15) -> bool:
    try:
        page.wait_for_url(f"**{keyword}**", timeout=timeout * 1000)
        return True
    except:
        return keyword in page.url

# ---------- 解析 expiry 字符串为总分钟数 ----------
def parse_expiry_minutes(expiry_str: str) -> int:
    if not expiry_str:
        return -1
    total = 0
    m = re.search(r'(\d+)\s*day', expiry_str)
    if m:
        total += int(m.group(1)) * 24 * 60
    m = re.search(r'(\d+)\s*h', expiry_str)
    if m:
        total += int(m.group(1)) * 60
    m = re.search(r'(\d+)\s*m', expiry_str)
    if m:
        total += int(m.group(1))
    return total if total > 0 else -1

# ---------- 关闭所有弹窗 ----------
def dismiss_all_popups(page):
    for round_idx in range(4):
        closed_any = False

        # ── Step A：JS 强制隐藏 Google Vignette iframe 及全屏遮罩 ──────
        hidden = page.evaluate("""() => {
            var count = 0;

            document.querySelectorAll('iframe').forEach(function(f) {
                if ((f.id && (f.id.includes('google_vignette') || f.id.includes('aswift'))) ||
                    (f.name && f.name.includes('google_vignette'))) {
                    f.style.setProperty('display', 'none', 'important');
                    if (f.parentElement) {
                        f.parentElement.style.setProperty('display', 'none', 'important');
                        if (f.parentElement.parentElement) {
                            f.parentElement.parentElement.style.setProperty('display', 'none', 'important');
                        }
                    }
                    count++;
                }
            });

            document.querySelectorAll('div[style*="position: fixed"], div[style*="position:fixed"]').forEach(function(ov) {
                if (!ov.offsetParent && ov.style.display === 'none') return;
                var z = parseInt(window.getComputedStyle(ov).zIndex) || 0;
                if (z >= 9000 && !ov.id.includes('renew') && !ov.id.includes('modal')) {
                    ov.style.setProperty('display', 'none', 'important');
                    count++;
                }
            });

            document.querySelectorAll('ins.adsbygoogle').forEach(function(ins) {
                ins.style.setProperty('display', 'none', 'important');
                count++;
            });

            return count;
        }""")
        if hidden and hidden > 0:
            log.info(f"  [轮{round_idx+1}] JS 隐藏 {hidden} 个广告/遮罩元素")
            closed_any = True

        # ── Step B：点击页面内关闭按钮 ──────────────────────────────────
        closed = page.evaluate("""() => {
            var count = 0;

            // ① 带明确文字的关闭按钮（在弹窗容器内）
            var closeTexts = ['Close', 'close', 'Schließen', 'CLOSE'];
            for (var t of closeTexts) {
                var btns = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                for (var b of btns) {
                    if (b.innerText && b.innerText.trim() === t) {
                        var parent = b.closest('[class*="modal"],[class*="popup"],[class*="overlay"],[class*="dialog"],[class*="ad-"],[class*="vignette"]');
                        if (parent && parent.offsetParent !== null) { b.click(); count++; break; }
                    }
                }
            }

            // ① bis：× / X 关闭按钮 —— 不限父容器，任何可见的都点（排除续期弹窗）
            var xTexts = ['×', 'X'];
            for (var xt of xTexts) {
                var xBtns = Array.from(document.querySelectorAll('button, a, [role="button"], span'));
                for (var xb of xBtns) {
                    if (xb.innerText && xb.innerText.trim() === xt && xb.offsetParent !== null) {
                        var inRenew = xb.closest('#renewModal, [id*="renew"]');
                        if (!inRenew) { xb.click(); count++; break; }
                    }
                }
            }

            // ① ter：CCPA / 隐私提示弹窗（"Do Not Sell or Share My Personal Information"）
            var privacyPopup = Array.from(document.querySelectorAll('div, section, aside')).find(function(el) {
                return el.offsetParent !== null &&
                       (el.innerText || '').includes('Do Not Sell') &&
                       !(el.id || '').includes('renew');
            });
            if (privacyPopup) {
                // 找弹窗内的关闭按钮（× 或任意按钮）
                var pClose = privacyPopup.querySelector(
                    'button, [role="button"], a[class*="close"], button[class*="close"]'
                );
                if (pClose && pClose.offsetParent !== null) {
                    pClose.click(); count++;
                } else {
                    // 找不到按钮就直接隐藏整个弹窗
                    privacyPopup.style.setProperty('display', 'none', 'important');
                    count++;
                }
            }

            // ② aria-label="Close" / "Dismiss"
            var ariaClose = document.querySelector(
                'button[aria-label="Close"], button[aria-label="close"], ' +
                '[aria-label="Dismiss"], button[aria-label="CLOSE"]'
            );
            if (ariaClose && ariaClose.offsetParent !== null) { ariaClose.click(); count++; }

            // ③ GDPR：Nicht einwilligen / Decline / Reject / Do not consent
            var gdprTexts = ['Nicht einwilligen', 'Decline', 'Reject', 'Do not consent'];
            for (var gt of gdprTexts) {
                var gb = Array.from(document.querySelectorAll('button')).find(b => b.innerText.trim() === gt);
                if (gb && gb.offsetParent !== null) { gb.click(); count++; break; }
            }

            // ④ Zampto continue-prompt / close-button-protector
            var cpClose = document.querySelector(
                '.close-button-protector, .dismiss-button, .dismiss-button-protector, ' +
                '[class*="continue-prompt"] button, [class*="close-button-protector"]'
            );
            if (cpClose && cpClose.offsetParent !== null) { cpClose.click(); count++; }

            // ⑤ 可见固定遮罩内的关闭按钮
            var overlays = Array.from(document.querySelectorAll('div[style*="position: fixed"], div[style*="position:fixed"]'));
            for (var ov of overlays) {
                if (ov.offsetParent === null) continue;
                if (ov.id && (ov.id.includes('renew') || ov.id.includes('modal'))) continue;
                var closeBtn = ov.querySelector('button[class*="close"], button[aria-label*="lose"], a[class*="close"]');
                if (closeBtn && closeBtn.offsetParent !== null) { closeBtn.click(); count++; break; }
            }

            return count;
        }""")

        if closed and closed > 0:
            log.info(f"  [轮{round_idx+1}] 已点击关闭 {closed} 个弹窗")
            closed_any = True
            time.sleep(1)

        # ── Step C：检查是否还有可见弹窗 ────────────────────────────────
        has_popup = page.evaluate("""() => {
            var selectors = [
                '[class*="modal"]:not([id*="renew"]):not([style*="display: none"])',
                '[class*="popup"]:not([style*="display: none"])',
                '[class*="vignette"]:not([style*="display: none"])',
            ];
            for (var s of selectors) {
                var el = document.querySelector(s);
                if (el && el.offsetParent !== null) return true;
            }
            var iframes = document.querySelectorAll('iframe');
            for (var f of iframes) {
                if ((f.id && f.id.includes('google_vignette')) && f.style.display !== 'none') return true;
            }
            // 检查 CCPA 弹窗是否还在
            var stillPrivacy = Array.from(document.querySelectorAll('div, section, aside')).find(function(el) {
                return el.offsetParent !== null && (el.innerText || '').includes('Do Not Sell');
            });
            if (stillPrivacy) return true;
            return false;
        }""")

        if not has_popup:
            break

        if not closed_any:
            break

        time.sleep(0.8)

# ---------- CF Turnstile ----------
_cf_frame_seen_ts = {"seen": False, "first_check_ts": None}


def _reset_cf_frame_seen():
    _cf_frame_seen_ts["seen"] = False
    _cf_frame_seen_ts["first_check_ts"] = None


def _cf_frame_exists(page) -> bool:
    try:
        found = any("challenges.cloudflare.com" in f.url for f in page.frames)
        if found:
            _cf_frame_seen_ts["seen"] = True
        return found
    except Exception:
        return False


def turnstile_state(page, debug: bool = False) -> str:
    modal_state = page.evaluate("""() => {
        // 兼容两种弹窗实现：旧版 #renewModal 与新版 shadcn Dialog(role=dialog, data-state=open)
        var shadcnOpen = document.querySelector('[role="dialog"][data-state="open"]');
        if (shadcnOpen) return 'modal_open';
        var modal = document.getElementById('renewModal');
        if (!modal) return 'no_modal';
        var cs = window.getComputedStyle(modal);
        if (cs.display === 'none' || cs.visibility === 'hidden') return 'modal_hidden';
        return 'modal_open';
    }""")

    if modal_state != 'modal_open':
        if debug:
            log.info(f"[诊断/turnstile_state] modal_state={modal_state} → done")
        return 'done'

    token_ready = page.evaluate("""() => {
        function deepQuery(root, sel) {
            let el = root.querySelector(sel);
            if (el) return el;
            for (const host of root.querySelectorAll('*')) {
                if (host.shadowRoot) {
                    el = deepQuery(host.shadowRoot, sel);
                    if (el) return el;
                }
            }
            return null;
        }
        var tokenEl = deepQuery(document, 'input[name="cf-turnstile-response"]');
        return !!(tokenEl && (tokenEl.value || '').length > 10);
    }""")
    if token_ready:
        if debug:
            log.info("[诊断/turnstile_state] token_ready=True → done")
        return 'done'

    if _cf_frame_seen_ts["first_check_ts"] is None:
        _cf_frame_seen_ts["first_check_ts"] = time.time()

    frame_exists_now = _cf_frame_exists(page)
    if debug:
        log.info(f"[诊断/turnstile_state] modal_open=True, token_ready=False, "
                  f"frame_exists_now={frame_exists_now}, seen_before={_cf_frame_seen_ts['seen']}")

    if frame_exists_now:
        return 'unchecked'

    if _cf_frame_seen_ts["seen"]:
        if debug:
            log.info("[诊断/turnstile_state] frame 曾出现过现已消失 → done")
        return 'done'

    elapsed = time.time() - _cf_frame_seen_ts["first_check_ts"]
    if elapsed < 2.5:
        return 'verifying'

    if debug:
        log.info(f"[诊断/turnstile_state] 宽限期已过({elapsed:.1f}s)仍未见过 frame → done")
    return 'done'


def click_turnstile_checkbox(page, timeout=10) -> bool:
    def dump_frames(label: str):
        try:
            frames = page.frames
            log.info(f"[诊断/{label}] 当前共 {len(frames)} 个 frame：")
            for i, f in enumerate(frames):
                url = (f.url or "about:blank")[:120]
                log.info(f"  [{i}] {url}")
        except Exception as e:
            log.warning(f"[诊断/{label}] dump_frames 失败: {e}")

    cf_frame = None
    for _ in range(timeout * 2):
        for f in page.frames:
            if "challenges.cloudflare.com" in (f.url or ""):
                cf_frame = f
                break
        if cf_frame:
            break
        time.sleep(0.5)

    box = None
    if cf_frame:
        log.info(f"找到 Turnstile frame: {cf_frame.url[:120]}")
        time.sleep(1)
        try:
            box = cf_frame.frame_element().bounding_box()
            log.info(f"[诊断] frame bounding_box={box}")
        except Exception as e:
            log.warning(f"获取 Turnstile frame bounding_box 失败: {e}")
    else:
        log.warning("枚举 frames 未找到 Turnstile frame")
        dump_frames("frame未找到")

    if not box:
        try:
            iframe_el = page.locator('iframe[src*="challenges.cloudflare.com"]').first
            box = iframe_el.bounding_box()
            log.info(f"[诊断] 降级 iframe bounding_box={box}")
        except Exception as e:
            log.warning(f"降级定位 Turnstile iframe 失败: {e}")

    if not box:
        log.warning("未能定位 Turnstile checkbox，跳过点击")
        dump_frames("定位失败")
        return False

    # 坐标合理性校验：x/y 应在视口范围内
    if not (0 < box["x"] < 1200 and 0 < box["y"] < 800):
        log.warning(f"[诊断] bounding_box 坐标异常（{box}），跳过点击")
        return False

    x = box["x"] + 25
    y = box["y"] + box["height"] / 2
    try:
        page.mouse.move(x, y)
        time.sleep(random.uniform(0.2, 0.4))
        page.mouse.click(x, y)
        log.info(f"✅ 已点击 Turnstile checkbox ({x:.0f}, {y:.0f})")
        return True
    except Exception as e:
        log.warning(f"点击 Turnstile checkbox 失败: {e}")
        return False


def wait_cf_turnstile(page, timeout=60) -> bool:
    log.info("等待 Cloudflare Turnstile 验证...")
    _reset_cf_frame_seen()

    # 单次 evaluate 容易撞上页面局部重渲染（倒计时/状态刷新）的瞬时空档，
    # 导致弹窗明明存在却查不到 → 改为短轮询，最多查 6 次（约 3s）
    renew_modal_visible = False
    for _retry in range(6):
        renew_modal_visible = page.evaluate("""() => {
            var shadcnOpen = document.querySelector('[role="dialog"][data-state="open"]');
            if (shadcnOpen) return true;
            var m = document.getElementById('renewModal');
            if (!m) return false;
            var cs = window.getComputedStyle(m);
            return cs.display !== 'none' && cs.visibility !== 'hidden';
        }""")
        if renew_modal_visible:
            break
        time.sleep(0.5)

    if not renew_modal_visible:
        log.warning("⚠️ 续期弹窗未检测到（可能已静默通过并自动关闭，也可能被广告弹窗遮挡，已重试6次）")
        # 弹窗可能一闪而过已经静默通过，不直接判失败，交给外层 expiry 复核兜底
        return True

    start = time.time()
    deadline = start + timeout

    # ── 阶段1：静默等待 ──────────────────────────────────────
    log.info("【Turnstile】阶段1：静默等待自动通过（最多 20s，稳定 unchecked 后提前进入点击）...")
    silent_deadline = min(time.time() + 20, deadline)
    last_state = None
    stable_unchecked_count = 0
    while time.time() < silent_deadline:
        last_state = turnstile_state(page, debug=True)
        if last_state == "done":
            log.info("✅ CF Turnstile 静默通过")
            return True
        if last_state == "unchecked":
            stable_unchecked_count += 1
            if stable_unchecked_count >= 3:
                log.info("【Turnstile】连续观察到稳定 unchecked，提前结束静默等待")
                break
        else:
            stable_unchecked_count = 0
        time.sleep(0.5)

    # ── 阶段1.5：若仍在 verifying，额外宽限 ──────
    if last_state == "verifying":
        log.info("【Turnstile】阶段1.5：仍在验证中（转圈），额外等待最多 12s...")
        grace_deadline = min(time.time() + 12, deadline)
        while time.time() < grace_deadline:
            state = turnstile_state(page)
            if state == "done":
                log.info("✅ CF Turnstile 静默通过（宽限期内）")
                return True
            if state == "unchecked":
                log.info("【Turnstile】宽限期内 spinner 结束，转为未勾选状态")
                break
            time.sleep(0.5)

    # ── 阶段2：主动点击，最多 3 次 ─────────────────────────────
    log.info("【Turnstile】阶段2：未自动通过，主动点击勾选框...")
    for attempt in range(1, 4):
        if time.time() >= deadline:
            break
        state = turnstile_state(page)
        if state == "done":
            return True
        if state == "verifying":
            wait_until = min(time.time() + 5, deadline)
            while time.time() < wait_until and turnstile_state(page) == "verifying":
                time.sleep(0.5)
            if turnstile_state(page) == "done":
                return True

        # ✅ 点击前先清广告弹窗，防止弹窗拦截鼠标事件或干扰坐标
        log.info(f"  [第{attempt}次] 点击前清除弹窗...")
        dismiss_all_popups(page)
        time.sleep(0.5)

        take_screenshot(page, f"06b_before_turnstile_click_{attempt}")
        clicked = click_turnstile_checkbox(page, timeout=min(8, max(1, int(deadline - time.time()))))
        take_screenshot(page, f"06c_after_turnstile_click_{attempt}")

        if not clicked:
            log.warning(f"第 {attempt} 次点击 Turnstile checkbox 失败")
            time.sleep(1)
            continue

        click_wait_deadline = min(time.time() + 8, deadline)
        while time.time() < click_wait_deadline:
            if turnstile_state(page) == "done":
                log.info(f"✅ CF Turnstile 验证完成（第 {attempt} 次点击后）")
                return True
            time.sleep(0.5)

        log.warning(f"第 {attempt} 次点击后仍未验证通过，{'重试...' if attempt < 3 else '放弃重试'}")

    # ── 阶段3：剩余时间继续被动等待 ──────────────────────────────
    log.info("【Turnstile】阶段3：继续等待剩余时间...")
    while time.time() < deadline:
        if turnstile_state(page) == "done":
            log.info("✅ CF Turnstile 验证完成")
            return True
        elapsed = int(time.time() - start)
        if elapsed % 5 == 0:
            log.info(f"  CF 等待中... {elapsed}s")
        time.sleep(1)

    log.error(f"CF Turnstile 验证超时（{timeout}s）")
    take_screenshot(page, "06d_turnstile_timeout")
    return False

# ---------- 登录 ----------
ZAMPTO_COOKIE_FILE = os.environ.get("ZAMPTO_COOKIE_FILE", "/tmp/zampto_cookies.json")


def save_cookies_to_file(page) -> bool:
    """
    将当前浏览器 cookies 保存到本地文件（由 GitHub Actions Cache 跨 run 持久化），
    使下次运行可以直接用 cookie 登录，跳过验证码。
    只有确认已登录成功（不在登录页）时才会保存，避免把未登录/失效状态的 cookie 缓存下来。
    """
    try:
        # 再次确认当前处于已登录状态，防止调用方误判导致保存无效 cookie
        current_url = page.url
        if "/auth/login" in current_url:
            log.warning(f"当前仍在登录页（{current_url}），判定为未登录，跳过 cookie 保存")
            return False

        cookies = page.context.cookies()
        # 只保留 zampto 相关域的 cookie，过滤广告追踪类
        zampto_cookies = [
            c for c in cookies
            if "zampto" in c.get("domain", "")
        ]
        if not zampto_cookies:
            log.warning("未找到 zampto 域的 cookie，跳过保存")
            return False

        os.makedirs(os.path.dirname(ZAMPTO_COOKIE_FILE), exist_ok=True)
        with open(ZAMPTO_COOKIE_FILE, "w", encoding="utf-8") as f:
            json.dump({"cookies": zampto_cookies}, f, ensure_ascii=False, indent=2)

        log.info(f"✅ 已确认登录成功，Cookie 已保存到文件: {ZAMPTO_COOKIE_FILE}（{len(zampto_cookies)} 条）")
        return True

    except Exception as e:
        log.warning(f"保存 cookie 到文件失败: {e}")
        return False

# ---------- Cookie 登录（优先方式，跳过表单+验证码） ----------
def try_cookie_login(page) -> bool:
    if not os.path.exists(ZAMPTO_COOKIE_FILE):
        log.info(f"Cookie 文件不存在（{ZAMPTO_COOKIE_FILE}），跳过 cookie 登录")
        return False

    try:
        with open(ZAMPTO_COOKIE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log.warning(f"读取 cookie 文件失败: {e}")
        return False

    # 支持两种格式：Playwright storage_state 完整结构 {"cookies":[...]} 或纯 cookie 数组 [...]
    cookies = data.get("cookies", data) if isinstance(data, dict) else data
    if not cookies:
        log.warning("Cookie 文件中没有 cookies 字段")
        return False

    # 过滤 Playwright add_cookies 不支持的字段（如 Chrome DevTools 导出的 partitionKey 等）
    ALLOWED_KEYS = {"name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite", "url"}
    cleaned_cookies = []
    for c in cookies:
        cc = {k: v for k, v in c.items() if k in ALLOWED_KEYS}
        if cc.get("sameSite") not in ("Strict", "Lax", "None", None):
            cc.pop("sameSite", None)
        cleaned_cookies.append(cc)
    cookies = cleaned_cookies

    try:
        page.context.add_cookies(cookies)
        log.info(f"✅ 已注入 {len(cookies)} 条 cookie")
    except Exception as e:
        log.warning(f"注入 cookie 失败: {e}")
        return False

    try:
        page.goto(f"{BASE_URL}/", timeout=30000, wait_until="domcontentloaded")
    except Exception as e:
        log.warning(f"cookie 登录后访问首页失败: {e}")
        return False

    time.sleep(3)

    if "/auth/login" not in page.url:
        log.info("✅ Cookie 登录成功，已处于已登录状态")
        take_screenshot(page, "00_cookie_login_success")
        return True

    log.warning("⚠️ Cookie 已失效（被重定向回登录页），fallback 到表单登录")
    return False


# ---------- IMAP 读取 126 邮箱验证码 ----------
def _mask_code(code: str) -> str:
    """日志中隐藏验证码，只保留首尾各1位，例如 093051 -> 0****1"""
    if not code:
        return ""
    if len(code) <= 2:
        return "*" * len(code)
    return code[0] + "*" * (len(code) - 2) + code[-1]


def fetch_otp_from_imap(wait_seconds=60, after_ts=None) -> str | None:
    """
    等待并读取 Zampto 发来的登录验证码邮件，返回 6 位数字字符串，超时返回 None。
    """
    if not IMAP_PASSWORD:
        log.warning("未配置 ZAMPTO_IMAP_PASSWORD，无法读取验证码邮件")
        return None

    log.info(f"连接 IMAP {IMAP_HOST}，等待 Zampto 验证码邮件（最多 {wait_seconds}s）...")
    # 等待 10 秒让 Zampto 把新邮件发出来，避免过早轮询读到上一封旧验证码
    log.info("等待 10s 让验证码邮件到达...")
    time.sleep(10)
    deadline = time.time() + wait_seconds
    poll_interval = 5

    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(IMAP_USER, IMAP_PASSWORD)
        log.info("✅ IMAP 登录成功")
    except Exception as e:
        log.warning(f"IMAP 连接/登录失败: {e}")
        return None

    try:
        # 126/网易邮箱要求登录后先发 ID 命令，否则 SELECT 返回 NO
        try:
            mail.xatom('ID', '("name" "Foxmail" "version" "7.2" "vendor" "Tencent")')
            log.info("✅ IMAP ID 命令已发送")
        except Exception as e:
            log.warning(f"IMAP ID 命令发送失败（可忽略）: {e}")

        # 必须先 SELECT 进入 SELECTED 状态，才能执行 SEARCH
        status, _ = mail.select("INBOX")
        if status != "OK":
            log.warning(f"IMAP SELECT INBOX 失败: {status}")
            return None
        log.info("✅ INBOX 已选中")

        # 只接受 Login 按钮点击之后发出的邮件，避免读到上次 run 遗留的旧验证码
        start_ts = after_ts if after_ts else time.time()

        while time.time() < deadline:
            try:
                # 每轮重新 SELECT，刷新邮件列表
                mail.select("INBOX")

                # 搜索所有来自 zampto 的邮件（不区分已读/未读）
                _, data = mail.search(None, 'FROM "zampto"')
                ids = data[0].split() if data[0] else []

                if ids:
                    from email.utils import parsedate_to_datetime as _parse_dt
                    # 取最近 10 封，收集所有满足时间条件的候选，最后取最新一封
                    # 容差 3700 秒（1小时+100s）兼容 Zampto 邮件头时区比北京时间少 1 小时的问题
                    candidates = []
                    for uid in ids[-10:]:
                        try:
                            _, msg_data = mail.fetch(uid, "(RFC822)")
                            raw = msg_data[0][1]
                            msg = email.message_from_bytes(raw)
                            date_str = msg.get("Date", "")
                            try:
                                mail_time = _parse_dt(date_str).timestamp()
                            except Exception:
                                mail_time = 0
                            log.info(f"📧 邮件时间: {mail_time:.0f}，登录触发: {start_ts:.0f}，差值: {mail_time - start_ts:.0f}s")
                            # 过滤：邮件时间不能比 login_click 早超过 3700 秒
                            if mail_time < start_ts - 3700:
                                log.info("⏭️ 过早，跳过")
                                continue
                            candidates.append((mail_time, uid, msg))
                        except Exception as e:
                            log.warning(f"读取邮件异常: {e}")

                    # 取时间最新的一封
                    if candidates:
                        candidates.sort(key=lambda x: x[0], reverse=True)
                        mail_time, uid, msg = candidates[0]
                        log.info(f"📬 选用最新邮件，时间差值: {mail_time - start_ts:.0f}s")

                        # 解码主题
                        subject = ""
                        for part, enc in decode_header(msg.get("Subject", "")):
                            if isinstance(part, bytes):
                                subject += part.decode(enc or "utf-8", errors="ignore")
                            else:
                                subject += str(part)

                        # 提取正文
                        body = ""
                        if msg.is_multipart():
                            for p in msg.walk():
                                if p.get_content_type() == "text/plain":
                                    charset = p.get_content_charset() or "utf-8"
                                    body += p.get_payload(decode=True).decode(charset, errors="ignore")
                        else:
                            charset = msg.get_content_charset() or "utf-8"
                            body = msg.get_payload(decode=True).decode(charset, errors="ignore")

                        log.info(f"检查邮件主题: {subject}")

                        # 提取 6 位纯数字验证码
                        otp_match = re.search(r"(?<![\d])(\d{6})(?![\d])", subject + " " + body)
                        if otp_match:
                            otp = otp_match.group(1)
                            log.info(f"✅ 获取验证码: {_mask_code(otp)}")
                            try:
                                mail.store(uid, '+FLAGS', '\\Seen')
                            except Exception:
                                pass
                            return otp

            except Exception as e:
                log.warning(f"IMAP 轮询异常: {e}")
                try:
                    mail.select("INBOX")
                except Exception:
                    pass

            remaining = int(deadline - time.time())
            log.info(f"未找到验证码邮件，{poll_interval}s 后重试（剩余 {remaining}s）...")
            time.sleep(poll_interval)

        log.warning("等待验证码超时")
        return None
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def login(page, max_retries=1) -> bool:
    # 新版登录页：dash.zampto.net/auth/login，邮箱+密码同页提交
    # 点击 Login 后如果触发邮件验证码，自动通过 IMAP 读取并填入
    login_url = "https://dash.zampto.net/auth/login"

    for attempt in range(1, max_retries + 1):
        log.info(f"登录 {attempt}/{max_retries}")
        try:
            page.goto(login_url, timeout=30000, wait_until="domcontentloaded")
        except Exception as e:
            log.warning(f"goto 异常: {e}")

        # 等待邮箱输入框出现
        try:
            page.wait_for_selector(
                'input#email, input[type="email"], input[placeholder*="example"]',
                timeout=15000
            )
        except Exception:
            log.warning("找不到邮箱输入框，重试")
            take_screenshot(page, f"login_no_input_{attempt}")
            time.sleep(2)
            continue

        # 填写邮箱
        try:
            email_el = page.locator('input#email, input[type="email"]').first
            email_el.click()
            email_el.fill("")
            email_el.type(USERNAME, delay=random.randint(60, 130))
            log.info("已填写邮箱")
        except Exception as e:
            log.warning(f"填写邮箱失败: {e}")
            continue

        human_delay(0.3, 0.8)

        # 填写密码
        try:
            pass_el = page.locator('input#password, input[type="password"]').first
            pass_el.click()
            pass_el.fill("")
            pass_el.type(PASSWORD, delay=random.randint(60, 130))
            log.info("已填写密码")
        except Exception as e:
            log.warning(f"填写密码失败: {e}")
            continue

        human_delay()

        # 点击 Login 按钮，同时记录点击时间（用于过滤旧验证码邮件）
        login_click_ts = None
        try:
            page.locator('button[type="submit"]:has-text("Login"), button:has-text("Login")').first.click()
            login_click_ts = time.time()
            log.info("已点击 Login 按钮")
        except Exception as e:
            log.warning(f"点击 Login 按钮失败: {e}")
            continue

        # 等待页面响应：可能直接跳 dashboard，也可能出现验证码输入框
        time.sleep(3)
        take_screenshot(page, f"login_after_click_{attempt}")

        # 情况一：直接跳转成功
        if "/auth/login" not in page.url:
            log.info("✅ 登录成功（无需验证码）")
            take_screenshot(page, "01_login_success")
            save_cookies_to_file(page)
            return True

        # 情况二：出现邮件验证码输入框（Zampto 2FA）
        otp_selectors = (
            'input[placeholder*="code" i], input[placeholder*="Code"], '
            'input[name*="code" i], input[id*="code" i], '
            'input[maxlength="6"], input[inputmode="numeric"]'
        )
        otp_input = None
        try:
            page.wait_for_selector(otp_selectors, timeout=8000)
            otp_input = page.locator(otp_selectors).first
            log.info("检测到验证码输入框，开始读取邮件验证码...")
            take_screenshot(page, f"login_otp_page_{attempt}")
        except Exception:
            pass

        if otp_input:
            otp = fetch_otp_from_imap(wait_seconds=90, after_ts=login_click_ts)
            if not otp:
                log.warning("未能获取验证码，本次登录失败")
                take_screenshot(page, f"login_otp_fail_{attempt}")
                time.sleep(2)
                continue

            try:
                # 用 fill() 直接写入，避免 type() 逐键模拟时丢字符
                otp_input.click()
                otp_input.fill("")
                time.sleep(0.3)
                otp_input.fill(otp)
                time.sleep(0.3)
                # 校验实际填入内容
                actual = otp_input.input_value()
                if actual != otp:
                    log.warning(f"填写验证码后内容不符（期望 {_mask_code(otp)}，实际 {_mask_code(actual)}），重新填入")
                    otp_input.fill("")
                    time.sleep(0.2)
                    otp_input.fill(otp)
                    actual = otp_input.input_value()
                log.info(f"已填写验证码: {_mask_code(actual)}")
                human_delay()
            except Exception as e:
                log.warning(f"填写验证码失败: {e}")
                continue

            # 点击验证码确认按钮
            try:
                verify_btn = page.locator(
                    'button[type="submit"]:has-text("Verify"), '
                    'button:has-text("Verify Code"), '
                    'button:has-text("Confirm"), '
                    'button[type="submit"]'
                ).first
                verify_btn.click(timeout=5000)
                log.info("已点击验证码确认按钮")
            except Exception as e:
                log.warning(f"点击确认按钮失败: {e}，尝试回车")
                try:
                    otp_input.press("Enter")
                except Exception:
                    pass

            # 等待跳转
            time.sleep(5)
            take_screenshot(page, f"login_otp_submit_{attempt}")

            if "/auth/login" not in page.url:
                log.info("✅ 验证码登录成功")
                take_screenshot(page, "01_login_success")
                save_cookies_to_file(page)  # 登录成功后自动回写新 cookie
                return True
            else:
                log.warning("验证码提交后仍在登录页，可能验证码有误或已过期")
                time.sleep(2)
                continue
        else:
            log.warning(f"页面仍在登录页且未出现验证码框（可能账号密码有误）")
            take_screenshot(page, f"login_fail_{attempt}")
            time.sleep(2)

    return False

# ---------- 获取服务器信息 ----------
def get_server_info(page, server_id: str) -> dict:
    server_url = f"{BASE_URL}/server?id={server_id}"
    log.info(f"访问服务器详情页")
    try:
        page.goto(server_url, timeout=30000, wait_until="domcontentloaded")
    except Exception as e:
        log.warning(f"访问服务器详情超时: {e}")

    time.sleep(3)
    take_screenshot(page, "02_server_page")
    dismiss_all_popups(page)
    time.sleep(1)

    info = page.evaluate("""() => {
        var body = document.body.innerText || '';
        var expiryMatch  = body.match(/Expiry[^:]*:\\s*([^\\n]+)/i);
        var renewedMatch = body.match(/last renewed[^:]*:\\s*([^\\n]+)/i);
        var addrMatch    = body.match(/node\\d+\\.zampto\\.net:\\d+/i);
        return {
            expiry:      expiryMatch  ? expiryMatch[1].trim()  : null,
            lastRenewed: renewedMatch ? renewedMatch[1].trim() : null,
            address:     addrMatch    ? addrMatch[0]           : null,
        };
    }""")

    console_url = f"{BASE_URL}/server-console?id={server_id}"
    log.info(f"访问 Console 页读取运行状态")
    try:
        page.goto(console_url, timeout=30000, wait_until="domcontentloaded")
    except Exception as e:
        log.warning(f"访问 Console 页超时: {e}")

    time.sleep(3)
    dismiss_all_popups(page)
    time.sleep(1)

    status_text = page.evaluate("""() => {
        var statusEl = document.getElementById('serverStatus');
        if (statusEl) return statusEl.innerText.trim();
        var runEl = document.querySelector('.status-running,.status-stopped,.status-starting');
        if (runEl) return runEl.innerText.trim();
        var body = document.body.innerText || '';
        var sm = body.match(/Running(?:\\s*\\([^)]+\\))?|Stopped|Starting|Stopping/i);
        return sm ? sm[0] : 'Unknown';
    }""")

    info["status"] = status_text or "Unknown"
    log.info(f"服务器信息: expiry={info.get('expiry')}, status={info.get('status')}, address=<已隐藏>")
    return info

# ---------- 启动服务器 ----------
def start_server(page) -> bool:
    console_url = f"{BASE_URL}/server-console?id={SERVER_ID}"
    MAX_START_ATTEMPTS = 3

    for attempt in range(1, MAX_START_ATTEMPTS + 1):
        log.info(f"直接导航到 Console 页（第 {attempt}/{MAX_START_ATTEMPTS} 次尝试）")
        try:
            page.goto(console_url, timeout=30000, wait_until="domcontentloaded")
        except Exception as e:
            log.warning(f"导航 Console 页超时: {e}")
        time.sleep(3)

        if attempt == 1:
            take_screenshot(page, "03_console_page")

        dismiss_all_popups(page)
        time.sleep(1)

        try:
            start_btn = page.locator('button:has-text("Start")').first
            if start_btn.is_visible(timeout=5000):
                start_btn.click()
                log.info(f"✅ 已点击 Start 按钮（第 {attempt} 次）")
                time.sleep(5)
                take_screenshot(page, f"04_after_start_attempt{attempt}")
            else:
                body_now = get_text(page)
                if "Running" in body_now:
                    log.info("Start 按钮不可见，页面已显示 Running，跳过点击")
                else:
                    log.warning(f"Start 按钮不可见且状态不是 Running，第 {attempt} 次跳过")
                    continue
        except Exception as e:
            log.warning(f"点击 Start 失败（第 {attempt} 次）: {e}")
            continue

        log.info("⏳ 等待服务器变为 Running（最多 5 分钟）...")
        wait_total = 300
        poll_interval = 10
        elapsed = 0
        final_status = "Unknown"
        offline_streak = 0

        while elapsed < wait_total:
            time.sleep(poll_interval)
            elapsed += poll_interval
            try:
                page.reload(timeout=20000, wait_until="domcontentloaded")
                time.sleep(4)
                dismiss_all_popups(page)
                time.sleep(1)
                body = get_text(page)
                if "Running" in body:
                    final_status = "Running"
                    offline_streak = 0
                    log.info(f"✅ 服务器已变为 Running（第 {attempt} 次尝试，等待了 {elapsed}s）")
                    take_screenshot(page, f"05_running_confirmed_attempt{attempt}")
                    break
                elif "Starting" in body:
                    final_status = "Starting"
                    offline_streak = 0
                    log.info(f"  [{elapsed}s] 还在 Starting，继续等待...")
                elif "Offline" in body or "Stopped" in body:
                    offline_streak += 1
                    log.info(f"  [{elapsed}s] 读到 Offline（连续第 {offline_streak} 次），{'继续等待...' if offline_streak < 3 else '确认失败'}")
                    if offline_streak >= 3:
                        final_status = "Offline"
                        take_screenshot(page, f"05_start_failed_attempt{attempt}_{elapsed}s")
                        break
                else:
                    offline_streak = 0
                    log.info(f"  [{elapsed}s] 状态未知，继续等待...")
            except Exception as e:
                log.warning(f"  [{elapsed}s] 刷新页面异常: {e}")
        else:
            log.warning(f"⚠️ 第 {attempt} 次等待超时（{wait_total}s），最后状态: {final_status}")
            take_screenshot(page, f"05_start_timeout_attempt{attempt}")

        if final_status == "Running":
            break

        if attempt < MAX_START_ATTEMPTS:
            log.info(f"⏳ 第 {attempt} 次失败，{5}s 后重试...")
            time.sleep(5)

    if final_status != "Running":
        return False

    addr_raw = None
    try:
        addr_raw = page.evaluate("""() => {
            var body = document.body.innerText || '';
            var m = body.match(/node\\d+\\.zampto\\.net:\\d+/i);
            return m ? m[0] : null;
        }""")
    except Exception:
        pass

    if addr_raw:
        parts = addr_raw.rsplit(":", 1)
        if len(parts) == 2:
            host, port_str = parts[0], parts[1]
            try:
                port = int(port_str)
                port_ok = wait_for_port(host, port, max_wait=120, interval=10)
                if port_ok:
                    log.info(f"✅ TCP 端口验证通过，服务器真正可连接")
                    take_screenshot(page, "06_port_verified")
                    return True
                else:
                    log.warning(f"⚠️ 端口不可达，尝试 Restart 后再等一轮...")
                    take_screenshot(page, "06_port_unreachable_before_restart")

                    restarted = False
                    try:
                        restart_btn = page.locator('button:has-text("Restart")').first
                        if restart_btn.is_visible(timeout=5000):
                            restart_btn.click()
                            log.info("🔄 已点击 Restart 按钮")
                            time.sleep(5)
                            take_screenshot(page, "07_after_restart")
                            restarted = True
                        else:
                            log.warning("Restart 按钮不可见，跳过")
                    except Exception as e:
                        log.warning(f"点击 Restart 失败: {e}")

                    if not restarted:
                        return False

                    log.info("⏳ Restart 后等待面板变为 Running（最多 5 分钟）...")
                    elapsed2 = 0
                    running_again = False
                    while elapsed2 < 300:
                        time.sleep(10)
                        elapsed2 += 10
                        try:
                            page.reload(timeout=20000, wait_until="domcontentloaded")
                            time.sleep(3)
                            dismiss_all_popups(page)
                            time.sleep(1)
                            body2 = get_text(page)
                            if "Running" in body2:
                                log.info(f"✅ Restart 后面板已变为 Running（等待了 {elapsed2}s）")
                                take_screenshot(page, f"08_restart_running")
                                running_again = True
                                break
                            elif "Starting" in body2:
                                log.info(f"  [{elapsed2}s] 还在 Starting，继续等待...")
                            elif "Offline" in body2 or "Stopped" in body2:
                                log.warning(f"  [{elapsed2}s] Restart 后回到 Offline，放弃")
                                break
                        except Exception as e:
                            log.warning(f"  [{elapsed2}s] 刷新异常: {e}")

                    if not running_again:
                        log.warning("⚠️ Restart 后未能恢复 Running，放弃")
                        take_screenshot(page, "08_restart_failed")
                        return False

                    log.info(f"🔌 Restart 后再次验证端口...")
                    port_ok2 = wait_for_port(host, port, max_wait=120, interval=10)
                    if port_ok2:
                        log.info(f"✅ Restart 后端口验证通过")
                        take_screenshot(page, "09_port_verified_after_restart")
                        return True
                    else:
                        log.warning(f"⚠️ Restart 后端口仍不可达，请手动处理")
                        take_screenshot(page, "09_port_still_unreachable")
                        return False
            except ValueError:
                pass
    else:
        log.warning("⚠️ 未能从页面读取服务器地址，跳过端口验证，以面板状态为准")

    return True

# ---------- 续期 ----------
def _recheck_expiry_increased(page, server_id: str, expiry_before: str) -> bool:
    """重新刷新页面读取 expiry，跟续期前对比，判断续期是否（在后台静默）实际成功了"""
    try:
        page.reload(timeout=20000, wait_until="domcontentloaded")
        time.sleep(3)
        dismiss_all_popups(page)
        time.sleep(1)
    except Exception as e:
        log.warning(f"复核 expiry 时刷新页面失败: {e}，改用 goto 重新导航...")
        try:
            page.goto(f"{BASE_URL}/server?id={server_id}", timeout=30000, wait_until="domcontentloaded")
            time.sleep(3)
            dismiss_all_popups(page)
            time.sleep(1)
        except Exception as e2:
            log.warning(f"复核 expiry 时重新导航也失败: {e2}")
            return False

    info_after = page.evaluate("""() => {
        var body = document.body.innerText || '';
        var m = body.match(/Expiry[^:]*:\\s*([^\\n]+)/i);
        return m ? m[1].trim() : null;
    }""")
    log.info(f"复核 expiry（页面直读）: {info_after}")

    minutes_before = parse_expiry_minutes(expiry_before)
    minutes_after  = parse_expiry_minutes(info_after)

    log.info(f"续期前 expiry 分钟数: {minutes_before}, 复核后: {minutes_after}")

    if minutes_after > 0 and (minutes_after > minutes_before or minutes_before <= 0):
        log.info(f"✅ 复核确认续期已成功！expiry: {expiry_before} → {info_after}"
                 f"（增加了 {minutes_after - minutes_before} 分钟）")
        return True

    log.info(f"复核结果：expiry 未增加（{expiry_before} → {info_after}），确认未续期成功")
    return False


def renew_server(page, server_id: str, expiry_before: str) -> bool:
    server_url = f"{BASE_URL}/server?id={server_id}"
    log.info(f"准备续期，访问服务器详情页")
    try:
        page.goto(server_url, timeout=30000, wait_until="domcontentloaded")
    except Exception as e:
        log.warning(f"访问续期页超时: {e}")

    time.sleep(3)

    log.info("关闭页面上所有弹窗...")
    dismiss_all_popups(page)
    time.sleep(1)

    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1)

        clicked = None
        # 优先用 Playwright 原生点击（真实可信事件，React onClick 才能正常响应）
        try:
            renew_btn = page.locator('button:has-text("Renew Server"), a:has-text("Renew Server")').first
            renew_btn.scroll_into_view_if_needed()
            time.sleep(0.5)
            renew_btn.click(timeout=8000)
            clicked = "locator_click"
            log.info("已点击 Renew Server 按钮（Playwright 原生点击）")
        except Exception as e:
            log.warning(f"Playwright 原生点击失败: {e}，尝试 force click...")
            try:
                renew_btn = page.locator('button:has-text("Renew Server"), a:has-text("Renew Server")').first
                renew_btn.click(force=True, timeout=8000)
                clicked = "locator_force_click"
                log.info("已点击 Renew Server 按钮（force click）")
            except Exception as e2:
                log.warning(f"force click 也失败: {e2}，fallback 到 JS dispatchEvent...")

        # JS 兜底：用 dispatchEvent 而非直接调用 onclick，更接近真实事件
        if not clicked:
            clicked = page.evaluate("""() => {
                var els = Array.from(document.querySelectorAll('a, button'));
                for (var el of els) {
                    var txt = (el.innerText || el.textContent || '').trim();
                    if (txt === 'Renew Server' || txt.includes('Renew Server')) {
                        el.scrollIntoView({block: 'center'});
                        var rect = el.getBoundingClientRect();
                        var opts = {bubbles: true, cancelable: true, view: window,
                                    clientX: rect.x + rect.width/2, clientY: rect.y + rect.height/2};
                        el.dispatchEvent(new MouseEvent('pointerdown', opts));
                        el.dispatchEvent(new MouseEvent('mousedown', opts));
                        el.dispatchEvent(new MouseEvent('pointerup', opts));
                        el.dispatchEvent(new MouseEvent('mouseup', opts));
                        el.dispatchEvent(new MouseEvent('click', opts));
                        return 'dispatchEvent';
                    }
                }
                return null;
            }""")
            if clicked:
                log.info(f"已点击 Renew Server 按钮（JS {clicked}）")

        if not clicked:
            log.warning("JS 未找到 Renew Server 按钮，尝试 Playwright locator...")
            renew_btn = page.locator('a:has-text("Renew Server"), button:has-text("Renew Server")').first
            renew_btn.scroll_into_view_if_needed()
            time.sleep(0.5)
            renew_btn.click(force=True)
            log.info("已点击 Renew Server 按钮（locator force click）")
        else:
            log.info(f"已点击 Renew Server 按钮（JS {clicked}）")

        take_screenshot(page, "05_renew_clicked")
    except Exception as e:
        log.warning(f"点击 Renew Server 失败: {e}")
        return False

    time.sleep(2)

    dismiss_all_popups(page)
    time.sleep(1)

    take_screenshot(page, "06_renew_modal")

    # ── Zampto 的 Turnstile 大多数情况下无感静默通过，但偶尔（如本次）会出现
    # 需要真人点击的可见勾选框（"Bestätigen Sie, dass Sie ein Mensch sind"）。
    # 因此不能只被动等待弹窗消失/页面重载，必须用 wait_cf_turnstile 主动检测状态，
    # 并在检测到勾选框未通过时用真实鼠标事件（page.mouse.move + click）去点击它。
    log.info("处理续期验证码（Zampto Turnstile）...")
    nav_detected = False
    try:
        turnstile_ok = wait_cf_turnstile(page, timeout=40)
    except Exception as e:
        err = str(e).lower()
        if any(k in err for k in ("context", "destroyed", "navigation", "detached")):
            nav_detected = True
            turnstile_ok = True
            log.info(f"✅ 检测到页面自动重载（Turnstile 通过后自动续期完成）: {e}")
        else:
            log.warning(f"wait_cf_turnstile 异常: {e}")
            turnstile_ok = False

    take_screenshot(page, "07_after_renew")

    if turnstile_ok or nav_detected:
        # 页面可能刚发生导航，先等它加载完，再进入 expiry 复核
        time.sleep(3)
    else:
        log.warning("⚠️ Turnstile 验证未确认通过，进行 expiry 复核兜底判断...")

    return _recheck_expiry_increased(page, server_id, expiry_before)

# ---------- 主流程 ----------
def _get_or_create_fingerprint_seed() -> int:
    """从缓存文件读取固定 seed，不存在则生成并保存。
    缓存文件路径由 ZAMPTO_FINGERPRINT_SEED_FILE 环境变量指定，
    默认 /tmp/zampto_fingerprint_seed.txt（Actions Cache 会缓存此文件）。
    """
    import random as _random
    seed_file = os.environ.get("ZAMPTO_FINGERPRINT_SEED_FILE", "/tmp/zampto_fingerprint_seed.txt")
    if os.path.exists(seed_file):
        try:
            seed = int(open(seed_file).read().strip())
            log.info(f"🔒 使用固定指纹 seed: {seed}（来自缓存）")
            return seed
        except Exception:
            pass
    seed = _random.randint(10000, 99999)
    os.makedirs(os.path.dirname(os.path.abspath(seed_file)), exist_ok=True)
    open(seed_file, "w").write(str(seed))
    log.info(f"🆕 生成新指纹 seed: {seed}（已保存到 {seed_file}）")
    return seed


def main():
    from cloakbrowser import launch
    from cloakbrowser.browser import get_default_stealth_args

    if not SERVER_ID:
        log.error("❌ 未配置 ZAMPTO_SERVER_ID 环境变量")
        wxpush("❌ 未配置 ZAMPTO_SERVER_ID，任务中止")
        return

    PROXY_SERVER = "socks5://127.0.0.1:1080"

    # 固定指纹 seed：让 Zampto 把每次 run 当成同一台设备，避免重复触发验证码
    seed = _get_or_create_fingerprint_seed()
    fixed_args = [
        "--no-sandbox",
        f"--fingerprint={seed}",
        "--fingerprint-platform=windows",
    ]

    log.info("启动 CloakBrowser...")
    browser = launch(
        headless=False,
        humanize=True,
        proxy=PROXY_SERVER,
        geoip=True,
        stealth_args=False,   # 关掉随机 seed，改用上面固定的
        args=fixed_args,
    )
    page = browser.new_page()

    try:
        if not try_cookie_login(page):
            if not login(page):
                wxpush("❌ Zampto 登录失败（cookie 已失效，且账号密码登录也失败，请检查）")
                return

        dismiss_all_popups(page)

        info = get_server_info(page, SERVER_ID)
        status     = info.get("status", "Unknown")
        expiry     = info.get("expiry", "未知")
        address    = info.get("address", "未知")
        last_renew = info.get("lastRenewed", "未知")

        log.info(f"服务器状态: {status} | 到期: {expiry}")

        if SKIP_RENEW:
            log.info("⏭️ SKIP_RENEW=true，跳过续期步骤（Uptime Kuma 紧急启动模式）")
            renewed = False
        else:
            renewed = renew_server(page, SERVER_ID, expiry_before=expiry)

        new_expiry = expiry
        if renewed:
            time.sleep(3)
            info2 = get_server_info(page, SERVER_ID)
            new_expiry = info2.get("expiry") or expiry
            last_renew = info2.get("lastRenewed") or last_renew
            log.info(f"续期后到期信息: {new_expiry}")

        started = False
        if "stopped" in status.lower() or "offline" in status.lower():
            log.info("🔴 服务器已停止，尝试启动...")
            started = start_server(page)
            if started:
                status = "Running"
                log.info("✅ 服务器已确认 Running")
            else:
                status = "Start Failed / Timeout"
                log.warning("⚠️ 服务器启动失败或超时，未能确认 Running")

        lines = ["🚨 Zampto 紧急启动报告" if SKIP_RENEW else "🖥️ Zampto 服务器日报"]
        lines.append(f"服务器 ID: ***")
        lines.append(f"地址: ***")
        lines.append("")
        status_icon = "🟢" if "running" in status.lower() else ("🟡" if "starting" in status.lower() else "🔴")
        lines.append(f"状态: {status_icon} {status}")
        if started:
            lines.append("  → 已启动，面板 Running + 端口可连接 ✅")
        elif "stopped" in status.lower() or "offline" in status.lower() or "failed" in status.lower():
            lines.append("  ⚠️ 启动失败（含自动 Restart 重试），端口仍不可达，请手动处理")
        lines.append("")
        lines.append(f"Expiry (Next Renewal): {new_expiry}")
        if last_renew:
            lines.append(f"Last Renewed: {last_renew}")
        if SKIP_RENEW:
            lines.append("  （续期已跳过，仅紧急启动）")
        elif renewed:
            lines.append("  → 已自动续期 ✅")
        else:
            lines.append("  ⚠️ 续期失败，请手动检查")

        msg = "\n".join(lines)
        log.info(f"推送内容:\n{msg}")
        wxpush(msg)

    except Exception as e:
        log.exception(e)
        take_screenshot(page, "99_error")
        wxpush(f"❌ Zampto 任务异常: {e}")
    finally:
        time.sleep(3)
        browser.close()
        log.info("任务结束")

if __name__ == "__main__":
    main()
