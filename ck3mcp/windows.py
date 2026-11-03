from __future__ import annotations

import ctypes
from ctypes import wintypes as W
import threading
import time
import uuid

from .core import Stopped, Replan


class WindowsGame:
    """Foreground-only client coordinates. No focus stealing or process injection."""
    KEYS = {"space": 0x20, "escape": 0x1B, "enter": 0x0D,
            "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34, "5": 0x35}

    def __init__(self, session):
        self.s = session
        self.mutex = None
        self.u = None
        self.closed = threading.Event()
        self.worker = None
        self.input_lock = threading.RLock()
        self.held, self.mouse_held = set(), set()
        try:
            self._initialize(session)
        except BaseException:
            self.close()
            raise

    def _initialize(self, session):
        import psutil
        self.s = session
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k.CreateMutexW.argtypes = [ctypes.c_void_p, W.BOOL, W.LPCWSTR]
        self.k.CreateMutexW.restype = W.HANDLE
        self.k.CloseHandle.argtypes = [W.HANDLE]
        ctypes.set_last_error(0)
        self.mutex = self.k.CreateMutexW(None, False, "Local\\CK3MCPInputOwnerV1")
        existed = ctypes.get_last_error() == 183
        if not self.mutex:
            raise RuntimeError("无法创建输入独占锁")
        if existed:
            self.k.CloseHandle(self.mutex)
            self.mutex = None
            raise RuntimeError("另一个 CK3 Agent/MCP 进程已持有输入控制权；请先退出它")
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.u.GetForegroundWindow.restype = W.HWND
        self.u.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
        self.u.GetClientRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
        self.u.ClientToScreen.argtypes = [W.HWND, ctypes.POINTER(W.POINT)]
        self.u.IsIconic.argtypes = [W.HWND]
        self.u.IsWindow.argtypes = [W.HWND]
        self.u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.u.GetAsyncKeyState.restype = ctypes.c_short
        self.u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        self.u.SetCursorPos.restype = W.BOOL
        self.u.keybd_event.argtypes = [W.BYTE, W.BYTE, W.DWORD, ctypes.c_size_t]
        self.u.mouse_event.argtypes = [W.DWORD, W.DWORD, W.DWORD, W.DWORD, ctypes.c_size_t]
        try:
            self.u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except AttributeError:
            self.u.SetProcessDPIAware()
        self.hwnd = self.u.GetForegroundWindow()
        pid = W.DWORD()
        self.u.GetWindowThreadProcessId(self.hwnd, ctypes.byref(pid))
        process = psutil.Process(pid.value)
        if process.name().lower() != self.s.cfg.process_name.lower():
            raise RuntimeError("请先让已载入存档的 CK3 保持前台；程序不会自动抢焦点")
        self.pid = pid.value
        self.created = process.create_time()
        self.observations = {}
        self.held = set()
        self.mouse_held = set()
        self.input_lock = threading.RLock()
        self.guard()
        self.worker = threading.Thread(target=self._watch, daemon=True)
        self.worker.start()

    def rect(self):
        r, p = W.RECT(), W.POINT()
        if not self.u.GetClientRect(self.hwnd, ctypes.byref(r)) or not self.u.ClientToScreen(self.hwnd, ctypes.byref(p)):
            raise RuntimeError("无法读取游戏客户区")
        if r.right <= 0 or r.bottom <= 0:
            raise RuntimeError("游戏客户区为空")
        return (p.x, p.y, r.right, r.bottom)

    def guard(self):
        import psutil
        self.s.check()
        if not self.u.IsWindow(self.hwnd) or self.u.IsIconic(self.hwnd) or self.u.GetForegroundWindow() != self.hwnd:
            self.s.stop("window_lost_focus")
            raise Stopped(self.s.reason)
        p = psutil.Process(self.pid)
        if p.create_time() != self.created or p.name().lower() != "ck3.exe":
            self.s.stop("process_changed")
            raise Stopped(self.s.reason)

    def _watch(self):
        try:
            while not self.closed.is_set() and not self.s.stop_event.wait(0.025):
                if self.u.GetAsyncKeyState(0x7B) & 0x8000:  # F12
                    self.s.stop("F12")
                self.guard()
        except Exception as exc:
            self.s.stop(type(exc).__name__)
        finally:
            self.release()

    def release(self):
        with self.input_lock:
            if self.u is None:
                return
            for key in list(self.held):
                self.u.keybd_event(key, 0, 2, 0)
            self.held.clear()
            for up in list(self.mouse_held):
                self.u.mouse_event(up, 0, 0, 0, 0)
            self.mouse_held.clear()

    def close(self):
        self.closed.set()
        try:
            self.release()
        finally:
            try:
                if self.worker and self.worker is not threading.current_thread():
                    self.worker.join(timeout=1)
            finally:
                with self.input_lock:
                    if self.mutex:
                        self.k.CloseHandle(self.mutex)
                        self.mutex = None

    def refresh_for_action(self, oid, action):
        """Old coordinates are reused only after conservative whole-scene AND target checks."""
        from PIL import Image, ImageChops, ImageStat
        self.guard()
        if oid not in self.observations:
            raise Replan("观察已被替代")
        old = self.observations[oid][1]
        fresh = self.capture()
        if tuple(old["rect"]) != tuple(fresh["rect"]):
            raise Replan("窗口变化")
        def difference(a, b):
            stats = ImageStat.Stat(ImageChops.difference(a, b))
            return sum(stats.mean) / (len(stats.mean) * 255)
        with Image.open(old["screenshot"]) as a, Image.open(fresh["screenshot"]) as b:
            a, b = a.convert("RGB"), b.convert("RGB")
            limit = self.s.cfg.scene_difference_limit
            # Check tiles as well as the global average, so a small popup cannot be
            # diluted by a mostly unchanged map. Animation can conservatively reject.
            w, h = a.size
            for row in range(6):
                for col in range(8):
                    box = (col*w//8, row*h//6, (col+1)*w//8, (row+1)*h//6)
                    if difference(a.crop(box), b.crop(box)) > limit:
                        raise Replan("界面区域改变，需要重新规划")
            if action.get("type") in ("click", "hover", "scroll"):
                x, y = action.get("x"), action.get("y")
                if type(x) is not int or type(y) is not int or not (0 <= x < w and 0 <= y < h):
                    raise ValueError("坐标无效")
                box = (max(0,x-40), max(0,y-24), min(w,x+41), min(h,y+25))
                if difference(a.crop(box), b.crop(box)) > limit / 2:
                    raise Replan("操作目标改变，需要重新规划")
        self.s.log("scene_revalidated", planned=oid, current=fresh["observation_id"])
        return fresh["observation_id"]

    def capture(self):
        import mss
        import mss.tools
        self.guard()
        rect = self.rect()
        left, top, width, height = rect
        oid = uuid.uuid4().hex
        path = self.s.path / f"{oid}.png"
        with mss.mss() as capture:
            shot = capture.grab({"left": left, "top": top, "width": width, "height": height})
            mss.tools.to_png(shot.rgb, shot.size, output=str(path))
        self.guard()
        if rect != self.rect():
            raise RuntimeError("截图期间窗口变化，请重新观察")
        obs = {"observation_id": oid, "timestamp": time.time(), "width": width,
               "height": height, "rect": rect, "screenshot": str(path),
               "paused": "unknown", "game_date": None, "source": "screenshot"}
        self.observations = {oid: (time.monotonic(), obs)}
        self.s.log("observation", **obs)
        return obs

    def validate(self, oid):
        self.guard()
        if oid not in self.observations:
            raise ValueError("未知或已被替代的 observation_id")
        created, obs = self.observations[oid]
        if time.monotonic() - created > self.s.cfg.observation_ttl_seconds or tuple(obs["rect"]) != self.rect():
            raise ValueError("观察过期或窗口变化，必须重新截图")
        return obs

    def point(self, oid, x, y):
        obs = self.validate(oid)
        if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, int) or not isinstance(y, int):
            raise ValueError("坐标必须是整数")
        if not (0 <= x < obs["width"] and 0 <= y < obs["height"]):
            raise ValueError("坐标超出游戏客户区")
        if not self.u.SetCursorPos(obs["rect"][0] + x, obs["rect"][1] + y):
            raise RuntimeError("鼠标定位失败，未发送点击")

    def execute(self, oid, action):
        with self.input_lock:
            self.s.ensure_reconciled()
            kind = action.get("type")
            if kind not in ("click", "hover", "scroll", "key", "wait"):
                raise ValueError("不支持的动作")
            if kind in ("click", "hover") and action.get("button", "left") not in ("left", "right"):
                raise ValueError("不支持的鼠标按钮")
            if kind == "scroll" and (type(action.get("amount", 1)) is not int or
                not 1 <= action.get("amount", 1) <= 5 or action.get("direction") not in ("up", "down")):
                raise ValueError("滚动参数无效")
            if kind == "wait" and (type(action.get("seconds", 0.5)) not in (int, float) or
                not 0 < action.get("seconds", 0.5) <= self.s.cfg.max_wait_seconds):
                raise ValueError("等待参数无效")
            contract = action.get("contract", {})
            if contract.get("paused") is not True or not contract.get("pause_evidence") or not contract.get("expected"):
                raise ValueError("动作必须携带已暂停的证据和可核验结果")
            if action.get("type") == "key" and action.get("key") != "escape":
                raise ValueError("v0.2 暂停内政模式只允许 Escape；请手工暂停")
            oid = self.refresh_for_action(oid, action)
            execution_before = dict(self.validate(oid))
            kind = action.get("type")
            aid = uuid.uuid4().hex
            result = {"action_id": aid, "status": "unknown", "input_dispatched": False,
                      "action": action, "observation_id": oid, "execution_before": execution_before}
            self.s.journal.write(aid, self.s.id, "prepared", result)
            # A crash after this durable marker is treated as possibly dispatched.
            self.s.journal.write(aid, self.s.id, "dispatching", result)
            if kind in ("click", "hover"):
                button = action.get("button", "left")
                if button not in ("left", "right"):
                    raise ValueError("不支持的鼠标按钮")
                self.point(oid, action.get("x"), action.get("y"))
                if kind == "click":
                    down, up = (2, 4) if button == "left" else (8, 16)
                    self.guard()
                    self.mouse_held.add(up)
                    try:
                        self.u.mouse_event(down, 0, 0, 0, 0)
                    finally:
                        self.u.mouse_event(up, 0, 0, 0, 0)
                        self.mouse_held.discard(up)
            elif kind == "key":
                key = self.KEYS.get(action.get("key"))
                if key is None:
                    raise ValueError("按键不在允许清单")
                self.guard()
                self.held.add(key)
                try:
                    self.u.keybd_event(key, 0, 0, 0)
                finally:
                    self.u.keybd_event(key, 0, 2, 0)
                    self.held.discard(key)
            elif kind == "scroll":
                amount = action.get("amount", 1)
                if type(amount) is not int or not 1 <= amount <= 5 or action.get("direction") not in ("up", "down"):
                    raise ValueError("滚动参数无效")
                self.point(oid, action.get("x"), action.get("y"))
                self.guard()
                delta = 120 * amount * (1 if action["direction"] == "up" else -1)
                self.u.mouse_event(0x800, 0, 0, delta & 0xFFFFFFFF, 0)
            elif kind == "wait":
                seconds = action.get("seconds", 0.5)
                if type(seconds) not in (int, float) or not 0 < seconds <= self.s.cfg.max_wait_seconds:
                    raise ValueError("等待超过上限")
                self.s.wait(seconds)
            else:
                raise ValueError("不支持的动作")
            self.observations.clear()
            result["input_dispatched"] = True
            self.s.journal.write(aid, self.s.id, "dispatched", result)
            self.s.actions[aid] = result
            self.s.log("action", **result)
            return result
