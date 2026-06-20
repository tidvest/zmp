import os, re, logging, random, json, time
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

BASE_URL    = "https://dash.zampto.net"
AUTH_URL    = "https://auth.zampto.net/sign-in"
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
    """
    截图前用 JS 将页面上的敏感信息替换为 ***，
    避免账号、邮箱、服务器地址等出现在截图文件中。
    覆盖范围：
      - 首页 info-card 里的 username / user_id / email 文本
      - Console 页的服务器地址（#addressValue）
    """
    try:
        page.evaluate("""() => {
            // ── 1. 首页 user-info-grid 里的三张 info-card ──────────────────
            // 结构: div.user-info-grid > div.info-card > div.info-content > p
            var cards = document.querySelectorAll('.user-info-grid .info-card .info-content');
            cards.forEach(function(card) {
                // <p> 直接子节点（username / user_id 值）
                var p = card.querySelector('p');
                if (p) p.textContent = '***';
                // email 用了 <p style="font-size:...">
                var pStyle = card.querySelector('p[style]');
                if (pStyle) pStyle.textContent = '***';
            });

            // ── 2. Console 页服务器地址 ──────────────────────────────────
            // <div class="info-card-value" id="addressValue">node11.zampto.net:40114</div>
            var addrEl = document.getElementById('addressValue');
            if (addrEl) addrEl.textContent = '***';

            // 同时覆盖所有可能含地址的 .info-card-value（防止多个地址字段）
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
        redact_sensitive_info(page)   # 截图前先脱敏
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
    """尝试 TCP 连接，返回是否成功。"""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False

def wait_for_port(host: str, port: int, max_wait: int = 120, interval: int = 10) -> bool:
    """轮询等待端口真正可连接，最多等 max_wait 秒。"""
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

# ---------- 解析 expiry 字符串为总分钟数（用于比较）----------
def parse_expiry_minutes(expiry_str: str) -> int:
    """
    将 "1 day 22h 47m" 或 "2 days 1h 5m" 之类解析为总分钟数。
    解析失败返回 -1。
    """
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

# ---------- 关闭所有弹窗（广告 + GDPR + Google Vignette）----------
def dismiss_all_popups(page):
    """
    关闭页面上所有可能出现的弹窗：
    1. Google Vignette 广告（iframe 形式，需 JS 隐藏）
    2. 普通广告弹窗（有 Close 按钮）
    3. GDPR Cookie 同意弹窗
    每次调用最多循环 4 轮，每轮间隔 0.8 秒。
    """
    for round_idx in range(4):
        closed_any = False

        # ── Step A：用 JS 强制隐藏 Google Vignette iframe 及全屏遮罩 ──────
        hidden = page.evaluate("""() => {
            var count = 0;

            // Google Vignette iframe（id 含 google_vignette 或 aswift）
            document.querySelectorAll('iframe').forEach(function(f) {
                if ((f.id && (f.id.includes('google_vignette') || f.id.includes('aswift'))) ||
                    (f.name && f.name.includes('google_vignette'))) {
                    // 隐藏 iframe 自身
                    f.style.setProperty('display', 'none', 'important');
                    // 隐藏其父容器（通常是 ins 或 div 包装）
                    if (f.parentElement) {
                        f.parentElement.style.setProperty('display', 'none', 'important');
                        if (f.parentElement.parentElement) {
                            f.parentElement.parentElement.style.setProperty('display', 'none', 'important');
                        }
                    }
                    count++;
                }
            });

            // 全屏固定遮罩（z-index 高的 fixed div，排除 renewModal 等正常弹窗）
            document.querySelectorAll('div[style*="position: fixed"], div[style*="position:fixed"]').forEach(function(ov) {
                if (!ov.offsetParent && ov.style.display === 'none') return;
                var z = parseInt(window.getComputedStyle(ov).zIndex) || 0;
                if (z >= 9000 && !ov.id.includes('renew') && !ov.id.includes('modal')) {
                    ov.style.setProperty('display', 'none', 'important');
                    count++;
                }
            });

            // ins.adsbygoogle 广告容器
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
            var closeTexts = ['Close', 'close', 'Schließen', '×', 'X', 'CLOSE'];
            for (var t of closeTexts) {
                var btns = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                for (var b of btns) {
                    if (b.innerText && b.innerText.trim() === t) {
                        var parent = b.closest('[class*="modal"],[class*="popup"],[class*="overlay"],[class*="dialog"],[class*="ad-"],[class*="vignette"]');
                        if (parent && parent.offsetParent !== null) { b.click(); count++; break; }
                    }
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
            // 排除 renewModal 和隐藏元素
            var selectors = [
                '[class*="modal"]:not([id*="renew"]):not([style*="display: none"])',
                '[class*="popup"]:not([style*="display: none"])',
                '[class*="vignette"]:not([style*="display: none"])',
            ];
            for (var s of selectors) {
                var el = document.querySelector(s);
                if (el && el.offsetParent !== null) return true;
            }
            // 检查是否还有可见的 Google 广告 iframe
            var iframes = document.querySelectorAll('iframe');
            for (var f of iframes) {
                if ((f.id && f.id.includes('google_vignette')) && f.style.display !== 'none') return true;
            }
            return false;
        }""")

        if not has_popup:
            break

        if not closed_any:
            break

        time.sleep(0.8)

# ---------- CF Turnstile 检测 + 主动点击（参考 Zytrano 项目逻辑）----------
# 模块级状态：记录本次续期流程中是否已经观察到过 CF iframe。
# 用于区分"iframe 还没异步加载出来"（此时不能判 done）
# 和"iframe 出现过、后来真的消失了"（此时才是真正的 done）。
_cf_frame_seen_ts = {"seen": False, "first_check_ts": None}


def _reset_cf_frame_seen():
    _cf_frame_seen_ts["seen"] = False
    _cf_frame_seen_ts["first_check_ts"] = None


def _cf_frame_exists(page) -> bool:
    """用 page.frames（CDP 协议层）判断 Turnstile iframe 是否存在，不受 shadow-root 限制。"""
    try:
        found = any("challenges.cloudflare.com" in f.url for f in page.frames)
        if found:
            _cf_frame_seen_ts["seen"] = True
        return found
    except Exception:
        return False


def turnstile_state(page, debug: bool = False) -> str:
    """
    返回 Turnstile 当前状态：
      'done'      - 续期弹窗已关闭，或 token 已写入，或 iframe 曾出现过且已消失 → 验证流程结束
      'verifying' - 正在验证中（iframe 存在但 token 还没写入，且仍在初始加载宽限期/无可点击勾选框）
      'unchecked' - iframe 存在、未在验证、也没有 token —— 需要主动点击

    修复1：原代码用 document.querySelectorAll('iframe') 判断 iframe 是否存在，
    但 Turnstile iframe 在 closed shadow-root 内，JS 层查不到 → 误判 'done'。
    改为 Python 层 page.frames 枚举（不受 shadow-root 限制）。

    修复2：弹窗刚打开瞬间 iframe 还没异步加载出来，引入 2.5s 初始宽限期，
    避免"还没加载"被误判成"已经消失（done）"。

    修复3（本次，根本问题）：之前用 document.body.innerText 检测
    "Verifiziere..."/"Verifying" 文案来判断 verifying 状态，但 Turnstile
    的整个 widget UI（包括这段转圈文案）本身就渲染在 shadow-root 内部，
    document.body.innerText 不会穿透 shadow DOM，永远读不到这段文字 ——
    所以 verifying 分支从一开始就没生效，每次都直接落到 check_frame 分支，
    完全依赖 _cf_frame_exists 的宽限期兜底。也就是说之前两轮修复的宽限期
    逻辑本身没错，但日志显示宽限期也没生效，说明 modal_closed 判断本身
    在某些时刻被误触发（modal 的 offsetParent/display 检测不够可靠）。
    现改为：
      - 不再依赖 modal 的 offsetParent/display 这类容易受 CSS 影响的检测；
        弹窗是否存在直接看 renewModal 节点是否在 DOM 里且不是 display:none。
      - 不再用 innerText 判断 verifying 文案（穿不透 shadow DOM，不可靠）。
      - 统一通过 page.frames 判断 iframe 存在性，再结合 token 是否写入，
        二者组合推断真实状态，不再依赖任何文案字符串匹配。
    """
    modal_state = page.evaluate("""() => {
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

    # token 是否已写入（穿透 shadow DOM）
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
        # frame 之前真的出现过，现在又没了 —— 验证流程结束
        if debug:
            log.info("[诊断/turnstile_state] frame 曾出现过现已消失 → done")
        return 'done'

    # frame 从未出现过：还在初始加载宽限期内就别急着判 done
    elapsed = time.time() - _cf_frame_seen_ts["first_check_ts"]
    if elapsed < 2.5:
        return 'verifying'

    if debug:
        log.info(f"[诊断/turnstile_state] 宽限期已过({elapsed:.1f}s)仍未见过 frame → done")
    return 'done'


def click_turnstile_checkbox(page, timeout=10) -> bool:
    """
    Turnstile managed 模式的勾选框 iframe 在 closed shadow-root 里，
    常规选择器/wait_for_selector 都打不进去。
    用 page.frames 在 CDP 协议层枚举所有 frame（不受 shadow DOM 限制），
    找到 URL 含 challenges.cloudflare.com 的那个，再用它的 bounding_box
    做真实鼠标坐标点击（点击框体左侧勾选框的位置）。

    参考 Zytrano 项目同名函数补充了诊断日志和 frame 初始化等待：
    - 找到 frame 后先 sleep(1) 给 iframe 内部 JS 初始化，再点击
      （直接点容易打在还没渲染完的位置上）
    - 找不到 frame / bounding_box 为 None 时打印完整 frame 列表，
      方便排查是 frame 真不存在还是只是还没渲染出来
    """
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
        time.sleep(1)  # 给 iframe 内部 JS 初始化，避免点早了打空
        try:
            box = cf_frame.frame_element().bounding_box()
            log.info(f"[诊断] frame bounding_box={box}")
        except Exception as e:
            log.warning(f"获取 Turnstile frame bounding_box 失败: {e}")
    else:
        log.warning("枚举 frames 未找到 Turnstile frame")
        dump_frames("frame未找到")

    if not box:
        # 降级：枚举失败时直接用 iframe 选择器定位（对外层 DOM 仍然有效）
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
    """
    等待 Cloudflare Turnstile 验证完成。
    Managed 模式不会自动通过 —— 截图证实它会从 spinner 状态落回
    "Bestätigen Sie, dass Sie ein Mensch sind" 的未勾选 checkbox，
    必须主动点击才能继续。流程：
      阶段1：静默等待最多 20s（managed 模式静默通过常需要 10-20s，8s 太短）
      阶段1.5：若静默等待结束时仍处于 verifying（转圈中），额外等它跑完，
               避免在 spinner 还没结束、勾选框还没渲染出来时就去点击
      阶段2：主动点击勾选框，最多重试 3 次
      阶段3：用剩余时间继续被动等待（点击生效但渲染慢的情况）
    """
    log.info("等待 Cloudflare Turnstile 验证...")
    _reset_cf_frame_seen()

    # 先确认续期弹窗真的出现了
    renew_modal_visible = page.evaluate("""() => {
        var m = document.getElementById('renewModal');
        if (!m) return false;
        var cs = window.getComputedStyle(m);
        return cs.display !== 'none' && cs.visibility !== 'hidden';
    }""")
    if not renew_modal_visible:
        log.warning("⚠️ 续期弹窗未检测到（可能被广告弹窗遮挡或未弹出）")
        return False

    start = time.time()
    deadline = start + timeout

    # ── 阶段1：静默等待 ──────────────────────────────────────
    # managed 模式有两种典型情况：
    #   (a) 真能自动过 —— 这种通常几秒内就会变成 done，不需要等满 20s
    #   (b) 根本不会自动过，会稳定停在 unchecked（iframe 已加载、未在
    #       验证、没有 token）—— 这种死等到 20s 纯属浪费时间，
    #       此次实测就是这种情况：第一次检测就是 unchecked，
    #       后续 39 次检测全部不变，白白等了 20 秒才进入阶段2点击。
    # 因此一旦连续 3 次（间隔 0.5s，即确认状态稳定而非加载中的抖动）
    # 检测到 unchecked，就提前结束静默等待，直接进入阶段2主动点击，
    # 不必死等到 20s 超时。
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

    # ── 阶段1.5：若仍在 verifying（转圈中），额外宽限等它跑完 ──────
    # 此时勾选框很可能还没渲染出来，直接进阶段2点击大概率打空。
    # 给最多 12s 宽限，期间一旦变成 done 就直接返回，
    # 变成 unchecked 就立即跳出去点击，不浪费时间空等。
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
        # 仍在转圈时再等一下，避免点击落空
        if state == "verifying":
            wait_until = min(time.time() + 5, deadline)
            while time.time() < wait_until and turnstile_state(page) == "verifying":
                time.sleep(0.5)
            if turnstile_state(page) == "done":
                return True

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
def login(page, max_retries=3) -> bool:
    login_url = "https://auth.zampto.net/sign-in?app_id=YOUR_APP_ID"

    for attempt in range(1, max_retries + 1):
        log.info(f"登录 {attempt}/{max_retries}")
        try:
            page.goto(login_url, timeout=30000, wait_until="domcontentloaded")
        except Exception as e:
            log.warning(f"goto 异常: {e}")

        try:
            page.wait_for_selector(
                'input[name="identifier"], input[autocomplete="username email"]',
                timeout=15000
            )
        except:
            log.warning("找不到用户名输入框，重试")
            take_screenshot(page, f"login_no_input_{attempt}")
            time.sleep(2)
            continue

        try:
            user_el = page.locator('input[name="identifier"]').first
            user_el.click()
            user_el.fill("")
            user_el.type(USERNAME, delay=random.randint(60, 130))
            log.info("已填写用户名")
        except Exception as e:
            log.warning(f"填写用户名失败: {e}")
            continue

        human_delay()

        try:
            page.locator('button[name="submit"], button[type="submit"]').first.click()
            log.info("已点击登录按钮（第一步）")
        except Exception as e:
            log.warning(f"点击登录失败: {e}")
            continue

        try:
            page.wait_for_selector(
                'input[name="password"], input[autocomplete="current-password"]',
                timeout=15000
            )
            log.info("已进入密码输入页")
        except:
            log.warning("未出现密码输入框，重试")
            take_screenshot(page, f"login_no_password_{attempt}")
            continue

        try:
            pass_el = page.locator('input[name="password"]').first
            pass_el.click()
            pass_el.fill("")
            pass_el.type(PASSWORD, delay=random.randint(60, 130))
            log.info("已填写密码")
        except Exception as e:
            log.warning(f"填写密码失败: {e}")
            continue

        human_delay()

        try:
            page.locator('button[name="submit"], button[type="submit"]').first.click()
            log.info("已点击继续按钮（第二步）")
        except Exception as e:
            log.warning(f"点击继续失败: {e}")
            continue

        if wait_for_url_contains(page, "dash.zampto.net", 20):
            log.info("✅ 登录成功，已跳转到 dashboard")
            take_screenshot(page, "01_login_success")
            return True

        time.sleep(3)
        if "dash.zampto.net" in page.url or "zampto.net/server" in page.url:
            log.info("✅ 登录成功")
            take_screenshot(page, "01_login_success")
            return True

        log.warning(f"登录后未跳转，请检查账号密码")
        take_screenshot(page, f"login_fail_{attempt}")
        time.sleep(2)

    return False

# ---------- 获取服务器信息（expiry + 状态）----------
def get_server_info(page, server_id: str) -> dict:
    """
    访问服务器详情页读取 expiry / lastRenewed / address，
    然后访问 console 页读取真实运行状态（Running / Stopped）。
    """
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

    # 真实运行状态在 Console 页（server 详情页显示的是账号 Active，不是运行状态）
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

# ---------- 启动服务器（含多次重试）----------
def start_server(page) -> bool:
    console_url = f"{BASE_URL}/server-console?id={SERVER_ID}"
    MAX_START_ATTEMPTS = 3  # 最多尝试点 Start 3 次

    for attempt in range(1, MAX_START_ATTEMPTS + 1):
        log.info(f"直接导航到 Console 页（第 {attempt}/{MAX_START_ATTEMPTS} 次尝试）")
        try:
            page.goto(console_url, timeout=30000, wait_until="domcontentloaded")
        except Exception as e:
            log.warning(f"导航 Console 页超时: {e}")
        time.sleep(3)

        if attempt == 1:
            take_screenshot(page, "03_console_page")

        # ── 先清弹窗，再点 Start ──────────────────────────────────────
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
                    # 直接进入等待确认
                else:
                    log.warning(f"Start 按钮不可见且状态不是 Running，第 {attempt} 次跳过")
                    continue
        except Exception as e:
            log.warning(f"点击 Start 失败（第 {attempt} 次）: {e}")
            continue

        # ── 轮询等待服务器真正变为 Running ──────────────────────────────
        log.info("⏳ 等待服务器变为 Running（最多 5 分钟）...")
        wait_total = 300
        poll_interval = 10
        elapsed = 0
        final_status = "Unknown"
        offline_streak = 0  # 连续读到 Offline 的次数，避免瞬间误判

        while elapsed < wait_total:
            time.sleep(poll_interval)
            elapsed += poll_interval
            try:
                page.reload(timeout=20000, wait_until="domcontentloaded")
                time.sleep(4)  # 多等 1 秒让状态渲染完
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
                        # 连续 3 次（30s）都是 Offline，才真正确认失败
                        final_status = "Offline"
                        take_screenshot(page, f"05_start_failed_attempt{attempt}_{elapsed}s")
                        break
                    # 否则继续等，可能只是页面还没渲染完
                else:
                    offline_streak = 0
                    log.info(f"  [{elapsed}s] 状态未知，继续等待...")
            except Exception as e:
                log.warning(f"  [{elapsed}s] 刷新页面异常: {e}")
        else:
            log.warning(f"⚠️ 第 {attempt} 次等待超时（{wait_total}s），最后状态: {final_status}")
            take_screenshot(page, f"05_start_timeout_attempt{attempt}")

        if final_status == "Running":
            break  # 成功，退出重试循环

        if attempt < MAX_START_ATTEMPTS:
            log.info(f"⏳ 第 {attempt} 次失败，{5}s 后重试...")
            time.sleep(5)

    if final_status != "Running":
        return False

    # ── 面板显示 Running，进一步用 TCP 验证端口真的开了 ─────────────────
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

                    # ── 点击 Restart 按钮 ────────────────────────────────
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

                    # ── 等待面板再次变为 Running ──────────────────────────
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

                    # ── 再次验证端口 ──────────────────────────────────────
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

    return True  # 读不到地址时降级为只看面板状态

# ---------- 续期 ----------
def renew_server(page, server_id: str, expiry_before: str) -> bool:
    """
    访问服务器详情页，关掉所有弹窗后点击 Renew Server，
    等待 CF Turnstile 自动通过，最后用 expiry 是否增加来验证续期成功。

    expiry_before: 续期前的 expiry 字符串，用于对比判断是否真正续期成功。
    """
    server_url = f"{BASE_URL}/server?id={server_id}"
    log.info(f"准备续期，访问服务器详情页")
    try:
        page.goto(server_url, timeout=30000, wait_until="domcontentloaded")
    except Exception as e:
        log.warning(f"访问续期页超时: {e}")

    time.sleep(3)

    # ✅ 关掉所有弹窗（广告/GDPR）再操作，避免广告弹窗拦截点击
    log.info("关闭页面上所有弹窗...")
    dismiss_all_popups(page)
    time.sleep(1)

    # 点击 Renew Server（<a> 标签带 onclick，需滚动到底部才可见）
    try:
        # 先滚动到底部确保按钮进入视口
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1)

        # 用 JS 直接触发按钮的 onclick（绕过遮挡问题）
        clicked = page.evaluate("""() => {
            // 优先找 action-button 类的 Renew Server 链接
            var els = Array.from(document.querySelectorAll('a, button'));
            for (var el of els) {
                var txt = (el.innerText || el.textContent || '').trim();
                if (txt === 'Renew Server' || txt.includes('Renew Server')) {
                    // 滚动到元素位置
                    el.scrollIntoView({block: 'center'});
                    // 优先用 onclick 属性触发
                    if (el.onclick) { el.onclick(new MouseEvent('click')); return 'onclick'; }
                    el.click();
                    return 'click';
                }
            }
            return null;
        }""")

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

    # 如果续期弹窗出现前又跳出广告，再清一次
    dismiss_all_popups(page)
    time.sleep(1)

    take_screenshot(page, "06_renew_modal")

    # 等待 CF Turnstile 验证（会先检查 renewModal 是否真的出现）
    if not wait_cf_turnstile(page, timeout=90):
        log.warning("CF 验证超时或续期弹窗未出现，续期失败")
        take_screenshot(page, "06_cf_timeout")
        return False

    # 等待续期弹窗处理完成（CF 通过后弹窗会消失，页面可能刷新）
    time.sleep(8)
    take_screenshot(page, "07_after_renew")

    # 刷新页面重新读取最新 expiry（续期后页面不一定自动刷新）
    try:
        page.reload(timeout=20000, wait_until="domcontentloaded")
        time.sleep(3)
        dismiss_all_popups(page)
        time.sleep(1)
    except Exception as e:
        log.warning(f"续期后刷新页面失败: {e}，改用 goto 重新导航...")
        try:
            page.goto(f"{BASE_URL}/server?id={server_id}", timeout=30000, wait_until="domcontentloaded")
            time.sleep(3)
            dismiss_all_popups(page)
            time.sleep(1)
        except Exception as e2:
            log.warning(f"续期后重新导航也失败: {e2}")

    # ✅ 用 expiry 是否增加来判断是否真正续期成功，不依赖弹窗状态
    info_after = page.evaluate("""() => {
        var body = document.body.innerText || '';
        var m = body.match(/Expiry[^:]*:\\s*([^\\n]+)/i);
        return m ? m[1].trim() : null;
    }""")
    log.info(f"续期后 expiry（页面直读）: {info_after}")

    minutes_before = parse_expiry_minutes(expiry_before)
    minutes_after  = parse_expiry_minutes(info_after)

    log.info(f"续期前 expiry 分钟数: {minutes_before}, 续期后: {minutes_after}")

    # 成功条件：
    # 1. 续期后分钟数 > 续期前（普通情况，时间增加）
    # 2. 续期前是 Expired（-1），续期后读到有效时间（>0）
    if minutes_after > 0 and (minutes_after > minutes_before or minutes_before <= 0):
        log.info(f"✅ 续期成功！expiry: {expiry_before} → {info_after}（增加了 {minutes_after - minutes_before} 分钟）")
        return True

    log.warning(f"⚠️ 续期后 expiry 未增加（{expiry_before} → {info_after}），续期失败！"
                f"（前={minutes_before}min，后={minutes_after}min）")
    return False

# ---------- 主流程 ----------
def main():
    from cloakbrowser import launch

    if not SERVER_ID:
        log.error("❌ 未配置 ZAMPTO_SERVER_ID 环境变量")
        wxpush("❌ 未配置 ZAMPTO_SERVER_ID，任务中止")
        return

    PROXY_SERVER = "socks5://127.0.0.1:1080"

    log.info("启动 CloakBrowser...")
    browser = launch(
        headless=False,
        humanize=True,
        proxy=PROXY_SERVER,
        geoip=True,
    )
    page = browser.new_page()

    try:
        # 1. 登录
        if not login(page):
            wxpush("❌ Zampto 登录失败，请检查账号密码")
            return

        # 2. 关闭 GDPR 同意弹窗
        dismiss_all_popups(page)

        # 3. 获取服务器信息
        info = get_server_info(page, SERVER_ID)
        status     = info.get("status", "Unknown")
        expiry     = info.get("expiry", "未知")
        address    = info.get("address", "未知")
        last_renew = info.get("lastRenewed", "未知")

        log.info(f"服务器状态: {status} | 到期: {expiry}")

        # 4. 续期（SKIP_RENEW=true 时跳过，只做启动）
        if SKIP_RENEW:
            log.info("⏭️ SKIP_RENEW=true，跳过续期步骤（Uptime Kuma 紧急启动模式）")
            renewed = False
        else:
            renewed = renew_server(page, SERVER_ID, expiry_before=expiry)

        # 5. 续期后重新读取最新 expiry 和 lastRenewed
        new_expiry = expiry
        if renewed:
            time.sleep(3)
            info2 = get_server_info(page, SERVER_ID)
            new_expiry = info2.get("expiry") or expiry
            last_renew = info2.get("lastRenewed") or last_renew
            log.info(f"续期后到期信息: {new_expiry}")

        # 6. 如果服务器已停止，再启动
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

        # 7. 推送
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
