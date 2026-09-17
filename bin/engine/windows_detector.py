import sys
import time
import win32gui
import win32api
import win32con
import win32process
import ctypes
import psutil
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtWidgets import QApplication, QWidget
from ctypes import wintypes
import win32con
import time

from engine.enums import SurfaceNormal
from engine.data_classes import AllSurfacesData, SegmentData, PetTransformData

from engine.logger import app_logger as log
from engine.logger import debug_logger as debug_log

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
dwmapi = ctypes.windll.dwmapi

_has_getdpiforwindow: bool

# Debug toggle: set True to print windows / segments info
DEBUG = False

#region HOOKS IF WINDOW APPEAR/DISAPPEAR
user32 = ctypes.windll.user32

needs_window_list_update = False

# Callback for window events
WinEventProcType = ctypes.WINFUNCTYPE(
    None,
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.HWND,
    wintypes.LONG,
    wintypes.LONG,
    wintypes.DWORD,
    wintypes.DWORD
)

def win_event_callback(hWinEventHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
    global needs_window_list_update

    # print("event", event, hwnd)

    # if event == win32con.EVENT_OBJECT_LOCATIONCHANGE:
    #     print("LOCATIONCHANGE:", hwnd, idObject, idChild)
    # Only top-level windows
    if idObject != win32con.OBJID_WINDOW or windows_detector == None:
        return
    
    if event != win32con.EVENT_OBJECT_LOCATIONCHANGE:
        needs_window_list_update = True
    
    # print("Hook went off")
    schedule_update()

def schedule_update(update_window_list = False):
    if update_window_list:
        global needs_window_list_update
        needs_window_list_update = True
    # print("schedule_update")
    if not update_timer.isActive():
        update_timer.start()

def run_update():
    # print("---------- window list update ----------------")
    global needs_window_list_update

    if needs_window_list_update:
        windows_detector.update_window_list()
        needs_window_list_update = False

    windows_detector.update_frame()

update_timer = QTimer()
update_timer.setSingleShot(True)
update_timer.setInterval(16)  # ~60Hz max
update_timer.timeout.connect(run_update)

# Convert the Python function into a callback
WinEventProc = WinEventProcType(win_event_callback)

# Hook events that indicate window changes
UPDATE_EVENTS = [
    win32con.EVENT_OBJECT_SHOW,
    win32con.EVENT_OBJECT_HIDE,
    win32con.EVENT_OBJECT_LOCATIONCHANGE,
    win32con.EVENT_SYSTEM_FOREGROUND,
    win32con.EVENT_SYSTEM_MINIMIZESTART,
    win32con.EVENT_SYSTEM_MINIMIZEEND,
]

# Register hooks
def install_hooks():
    hooks = []
    for ev in UPDATE_EVENTS:
        hook = user32.SetWinEventHook(
            ev,
            ev,
            0,
            WinEventProc,
            0, 0,
            win32con.WINEVENT_OUTOFCONTEXT | win32con.WINEVENT_SKIPOWNPROCESS
        )
        hooks.append(hook)

    print("Window event hooks installed — listening for changes...")

#endregion

try:
    user32.GetDpiForWindow.restype = ctypes.c_uint
    _has_getdpiforwindow = True
except Exception:
    pass

class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG)
    ]

def get_extended_frame_bounds(hwnd):
    rect = RECT()
    result = dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(hwnd),
        DWMWA_EXTENDED_FRAME_BOUNDS,
        ctypes.byref(rect),
        ctypes.sizeof(rect)
    )
    if result != 0:
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def is_window_cloaked(hwnd):
    cloaked = wintypes.DWORD()
    result = dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(hwnd),
        DWMWA_CLOAKED,
        ctypes.byref(cloaked),
        ctypes.sizeof(cloaked)
    )
    if result != 0:
        return False
    return cloaked.value != 0

def is_fullscreen(hwnd):
    rect = get_extended_frame_bounds(hwnd)
    if not rect:
        return False

    monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
    info = win32api.GetMonitorInfo(monitor)
    mx1, my1, mx2, my2 = info["Monitor"]
    # print(" rect ==", (mx1, my1, mx2, my2))

    return rect == (mx1, my1, mx2, my2)

def is_maximized(hwnd):
    try:
        placement = win32gui.GetWindowPlacement(hwnd)
        return placement[1] == win32con.SW_MAXIMIZE
    except:
        return False

def is_window_real(hwnd):
    """
    Returns True if this hwnd is a "real" user window pet can interact with.
    Filters out:
        - Tool windows
        - Taskbar, desktop, worker windows
        - Flyouts / IME / popups
        - Cloaked or zero-size windows
    """
    try:
        # Must exist
        if not win32gui.IsWindow(hwnd):
            return False

        # Get styles
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)

        # Skip windows without overlapped style
        if not (style & win32con.WS_OVERLAPPEDWINDOW):
            return False
        
        exstyle = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)

        # Skip tool windows
        if exstyle & win32con.WS_EX_TOOLWINDOW:
            return False
        
        if not win32gui.IsWindowVisible(hwnd):
            return False
        
        # Ignore cloaked windows (UWP / modern app hidden windows)
        if is_window_cloaked(hwnd):
            return False
        
        # Skip system tray / taskbar / desktop
        cls = win32gui.GetClassName(hwnd)
        if cls in {
            "Progman",
            "WorkerW",
            "Shell_TrayWnd",
            "Shell_SecondaryTrayWnd",
            "SystemTray_Main",
        }:
            return False

        
        # Skip windows that are topmost flyouts (like Network / Volume / Emoji)
        if style & win32con.WS_POPUP and not style & win32con.WS_OVERLAPPEDWINDOW:
            return False

        # Skip tiny or zero-size windows
        rect = get_extended_frame_bounds(hwnd)
        if not rect:
            return False
        L, T, R, B = rect
        if R - L < 50 or B - T < 50:
            return False
        
        # If it passed all checks, it's probably a real window
        return True

    except Exception:
        return False

def get_windows_in_zorder(excluded_hwnd):
    """
    Return list of top-level window HWNDs in Z-order (top-first),
    using DWM visual bounds and ignoring cloaked windows.
    """

    try:
        fg = win32gui.GetForegroundWindow()
        start = win32gui.GetWindow(fg, win32con.GW_HWNDFIRST)
    except Exception:
        start = win32gui.GetWindow(
            win32gui.GetDesktopWindow(),
            win32con.GW_HWNDFIRST
        )

    windows = []

    windows.append(TASKBAR_HWND) # appending taskbar as a top window

    hwnd = start

    visible_apps = set()
    pid_cache = {}

    fullscreen_apps = set()
    maximised_apps = set()

    while hwnd:
        window = hwnd
        hwnd = win32gui.GetWindow(hwnd, win32con.GW_HWNDNEXT) # immediately setting up the next iteration (window - current hwnd, and hwnd = next hwnd)
        try:
            if window not in excluded_hwnd and is_window_real(window):
                if is_real_app(window):
                    try:
                        _, pid = win32process.GetWindowThreadProcessId(window)
                        if pid not in pid_cache:
                            pid_cache[pid] = psutil.Process(pid).name()
                        visible_apps.add(pid_cache[pid])
                    except Exception:
                        pass
                if is_fullscreen(window):
                    title = win32gui.GetWindowText(window)
                    # print("FULLSCREEN:", title)
                    if is_real_app(window):
                        _, pid = win32process.GetWindowThreadProcessId(window)
                        fullscreen_apps.add(psutil.Process(pid).name())
                        # print("fullscreen:", fullscreen_app)
                    windows.append(window)
                    break
                elif is_maximized(window):
                    title = win32gui.GetWindowText(window)
                    # print("MAXIMISED: ", title)
                    if is_real_app(window):
                        _, pid = win32process.GetWindowThreadProcessId(window)
                        maximised_apps.add(psutil.Process(pid).name())
                        # print("maximised:", maximised_app)
                    windows.append(window)
                    break

                # cls = win32gui.GetClassName(window)
                # style = win32gui.GetWindowLong(window, win32con.GWL_STYLE)
                # exstyle = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)

                # print(f"Name: {win32gui.GetWindowText(window)}\nWindow: {window}\nClass: {cls}\nStyle: {style}")

                windows.append(window)

        except Exception:
            pass

    # print("visible_apps:", visible_apps)

    return windows, visible_apps, maximised_apps, fullscreen_apps

def is_real_app(hwnd):
    try:
        if not win32gui.IsWindow(hwnd):
            # print("not IsWindow")
            return False

        # Top-level only
        if win32gui.GetParent(hwnd) != 0:
            # print("no GetParent")
            return False
        
        # Styles
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)

        # Must be normal overlapped window, not tool window or pure popup
        if not (style & win32con.WS_OVERLAPPEDWINDOW):
            # print("style is weird")
            return False
    

        exstyle = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        
        if exstyle & win32con.WS_EX_TOOLWINDOW:
            # print("exstyle is weird")
            return False
        
        if exstyle & win32con.WS_EX_APPWINDOW:
            # print("second exstyle is weird")
            return False

        # Title
        # title = win32gui.GetWindowText(hwnd).strip()
        # if not title:
        #     return False
        
        # Ignore cloaked windows (UWP / modern app hidden windows)
        if is_window_cloaked(hwnd):
            # print("is cloaked")
            return False
        
        # Check process name to filter shell / system processes
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc_name = psutil.Process(pid).name().lower()
        if proc_name in {"taskhostw.exe", "ctfmon.exe", 'iusb3mon.exe'}:
            # print("name is dumb")
            return False
        
        if win32gui.GetWindow(hwnd, win32con.GW_OWNER) != 0:
            # print("no GW OWNER")
            return False
        
        # System classes to ignore
        cls = win32gui.GetClassName(hwnd)
        if cls in {
            "Progman",
            "WorkerW",
            "Shell_TrayWnd",
            "Shell_SecondaryTrayWnd",
            "SystemTray_Main",
        }:
            # print("weird classname")
            return False

        # Size
        rect = get_extended_frame_bounds(hwnd)
        if not rect:
            # print("no rect")
            return False
        L, T, R, B = rect
        if R - L < 50 or B - T < 50:
            # print("too small")
            return False
        
        return True

    except Exception:
        return False


def compute_visible_segments(windows: list[int], rects: dict[int, tuple]):
    """
    :param windows: list of HWND top-first (topmost first)
    :param rects: dictionary of rect tuple(L, T, R, B)

    Returns:
        SegmentData hwnd -> {
            rect: (L,T,R,B),
            top: [(x1,x2)],
            bottom: [(x1,x2)],
            left: [(y1,y2)],
            right: [(y1,y2)],
        }
    """

    segs: dict[int, SegmentData] = {}

    # bottom-first for occlusion logic
    windows_bottom_first = list(reversed(windows))

    for idx, hwnd in enumerate(windows_bottom_first):

        rect = rects.get(hwnd)
        if not rect:
            continue

        L, T, R, B = rect

        top_seg = (L, R)
        bottom_seg = (L, R)
        left_seg = (T, B)
        right_seg = (T, B)

        cuts_top = []
        cuts_bottom = []
        cuts_left = []
        cuts_right = []

        # windows above this one
        for j in range(idx + 1, len(windows_bottom_first)):
            above = windows_bottom_first[j]
            ar = rects.get(above)
            if not ar:
                continue

            inter = intersect_rect(rect, ar)
            if not inter:
                continue

            x1, y1, x2, y2 = inter

            if y1 <= T <= y2:
                cuts_top.append((x1, x2))

            if y1 <= B <= y2:
                cuts_bottom.append((x1, x2))

            if x1 <= L <= x2:
                cuts_left.append((y1, y2))

            if x1 <= R <= x2:
                cuts_right.append((y1, y2))

        vis_top = subtract_many(top_seg, cuts_top)
        vis_bottom = subtract_many(bottom_seg, cuts_bottom)
        vis_left = subtract_many(left_seg, cuts_left)
        vis_right = subtract_many(right_seg, cuts_right)

        segs[hwnd] = SegmentData(
            rect=rect,
            top=vis_top,
            bottom=vis_bottom,
            right=vis_right,
            left=vis_left
        )

    return segs

# -----------------------
# Geometry helpers
# -----------------------
def intersect_rect(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    if ax2 <= bx1: return None  # left case
    if ax1 >= bx2: return None  # right case
    if ay1 >= by2: return None  # top case
    if ay2 <= by1: return None  # bottom case
    cx1 = max(ax1, bx1)
    cy1 = max(ay1, by1)
    cx2 = min(ax2, bx2)
    cy2 = min(ay2, by2)
    return (cx1, cy1, cx2, cy2)

def subtract_segment(seg, cut):
    s1, s2 = seg
    c1, c2 = cut
    if c2 <= s1 or c1 >= s2:
        return [seg]
    pieces = []
    if c1 > s1:
        pieces.append((s1, c1))
    if c2 < s2:
        pieces.append((c2, s2))
    return pieces

def subtract_many(seg, cuts):
    if not cuts:
        return [seg]
    
    s1, s2 = seg

    # Keep only cuts that actually overlap the segment
    cuts = [(max(c1, s1), min(c2, s2))
            for c1, c2 in cuts
            if c2 > s1 and c1 < s2]

    if not cuts:
        return [seg]

    cuts.sort()

    result = []
    current = s1

    for c1, c2 in cuts:
        if c1 > current:
            result.append((current, c1))

        if c2 > current:
            current = c2

        if current >= s2:
            return result

    if current < s2:
        result.append((current, s2))

    return result

def ranges_overlap(a1, a2, b1, b2):
    return a1 <= b2 and b1 <= a2

def update_active_apps():
    try:
        fg = win32gui.GetForegroundWindow()
        start = win32gui.GetWindow(fg, win32con.GW_HWNDFIRST)
    except Exception:
        start = win32gui.GetWindow(win32gui.GetDesktopWindow(), win32con.GW_HWNDFIRST)

    hwnd = start
    apps = set()
    pid_cache = {}

    while hwnd:
        if is_real_app(hwnd):
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid not in pid_cache:
                    pid_cache[pid] = psutil.Process(pid).name()
                apps.add(pid_cache[pid])
            except Exception:
                pass

        hwnd = win32gui.GetWindow(hwnd, win32con.GW_HWNDNEXT)

    # print("Active apps:", apps)
    return apps
    
def get_window_dpi_scale(hwnd):
    """Return DPI scale for HWND (physical -> logical)."""
    # Try modern API
    try:
        if _has_getdpiforwindow:
            dpi = user32.GetDpiForWindow(hwnd)
            if dpi and dpi != 0:
                return dpi / 96.0
    except Exception:
        pass
    # Fallback: system DPI via device caps
    try:
        hdc = win32gui.GetDC(0)
        LOGPIXELSX = 88
        dpi = win32gui.GetDeviceCaps(hdc, LOGPIXELSX)  # type: ignore
        if dpi and dpi != 0:
            return dpi / 96.0
    except Exception:
        pass
    return 1.0



class WindowsOverlay(QWidget):
    def __init__(self, pet):
        super().__init__()

        self.pet = pet

        self.pet_parent_window_rect = None
        try:
            install_hooks()
        except Exception as e:
            log.warning(f"Windows hooks could not be installed.\n{e}")

        self.update_hitbox(pet.transform.hitbox_width, pet.transform.hitbox_height)

        screen = QApplication.primaryScreen()
        self.screen_geom = screen.geometry()
        self.screen_avail_geom = screen.availableGeometry()

        global windows_detector
        windows_detector = self

        # Transparent click-through fullscreen overlay
        self.setWindowFlags(
            Qt.FramelessWindowHint # type: ignore
            | Qt.WindowStaysOnTopHint # type: ignore
            | Qt.Tool # type: ignore
            | Qt.WindowTransparentForInput # type: ignore
        )
        self.setAttribute(Qt.WA_TranslucentBackground) # type: ignore
        self.showFullScreen()

        # store overlay hwnd to skip it in enumeration
        my_hwnd = int(self.winId())
        pet_hwnd = int(self.pet.winId())
        self.excluded_hwnd = {my_hwnd, pet_hwnd} # set of excluded from search hwnd 

        global TASKBAR_HWND
        TASKBAR_HWND = -1
        self.TASKBAR_HWND = TASKBAR_HWND

        self.taskbar_rect = (
            self.screen_geom.left(),
            self.screen_avail_geom.bottom(),
            self.screen_geom.right(),
            self.screen_geom.bottom()
        )

        self.windows_hwnd: list[int]  = []   # top-first
        self.active_apps = set()
        self.visible_apps = set()
        self.fullscreen_apps = set()
        self.maximised_apps = set()
        self.focused_app_title = ""
        self.focused_app = set()

        self.segments = {}  # hwnd -> clipped segments computed in physical pixels

        self.surfaces = AllSurfacesData(
            left=[],
            top=[],
            right=[],
            bottom=[],
        )

        # Hook all top-level window events
        user32 = ctypes.windll.user32

        
    def update_hitbox(self, new_hitbox_w, new_hitbox_h):
        self.hitbox_w = new_hitbox_w
        self.hitbox_h = new_hitbox_h

    def update_window_list(self):
        self.active_apps = update_active_apps()
        excluded = self.excluded_hwnd
        self.windows_hwnd, self.visible_apps, self.maximised_apps, self.fullscreen_apps = get_windows_in_zorder(excluded_hwnd=excluded)

        # print("update_window_list VISIBLE", self.visible_apps)
        # print("update_window_list VISIBLE", self.visible_apps)

        focused_hwnd = win32gui.GetForegroundWindow()
        self.focused_app_title = win32gui.GetWindowText(focused_hwnd)

        self.focused_app.clear()
        tid, pid = win32process.GetWindowThreadProcessId(focused_hwnd)
        # print(f"hwnd={focused_hwnd}, tid={tid}, pid={pid}")
        if pid >= 0:
            try:
                self.focused_app.add(psutil.Process(pid).name())
                # print("update_window_list FOCUSED", self.focused_app)
                # print("update_window_list FOCUSED HWND", focused_hwnd)
                # print("update_window_list BOUNDS", get_extended_frame_bounds(focused_hwnd))
                
            except Exception: pass

        self.update_apps()

        # print("rects:", self.rects)
        # print("segmesnts:", self.segments)
        # print("surfaces:", self.surfaces)

        if DEBUG:
            print(f"[enum] found {len(self.windows_hwnd)} windows")

    def update_apps(self):
        app_state = (self.active_apps, self.visible_apps, self.maximised_apps, self.fullscreen_apps, self.focused_app_title, self.focused_app)
        self.pet.update_apps(app_state)

    def update_frame(self):
        # update rects for current cached windows
        rects: dict[int, tuple] = {} # hwnd -> rect physical

        t1 = time.perf_counter()

        parent_hwnd = self.pet.parent_window_hwnd

        # appending taskbar as a rect for collisions
        rects[TASKBAR_HWND] = self.taskbar_rect

        for hwnd in self.windows_hwnd:
            if hwnd == TASKBAR_HWND: continue
            if hwnd == parent_hwnd: 
                rects[parent_hwnd] = self.pet.parent_window_rect
                continue
            try:
                # print("UPDATING GETTING EXTENDED FRAME BOUNDS", hwnd)
                rect = get_extended_frame_bounds(hwnd)
                if not rect: return
                
                scale = get_window_dpi_scale(hwnd=hwnd) # this is probably to remove or move up, its getting dpi per window
                if scale <= 0:
                    scale = 1.0

                rects[hwnd] = (rect[0]/scale, rect[1]/scale, rect[2]/scale, rect[3]/scale) # getting real scaled values for positions
            except Exception:
                pass
    
        # t2 = time.perf_counter()
        # print(f"Time for getting rects: {t2 - t1}")

        # recompute clipped border segments in physical pixels
        segs = compute_visible_segments(self.windows_hwnd, rects)
        self.segments = segs
        
        # t3 = time.perf_counter()
        # print(f"Time for computing visible segments: {t3 - t2}")

        self.rebuild_surfaces(segs)
                
        # print summary for the top few windows
        # if DEBUG:
        #     topn = min(6, len(self.windows_hwnd))
        #     print(f"[frame] rects={len(rects)}, segs={len(segs)} (top {topn}):")
        #     for i, hwnd in enumerate(self.windows_hwnd[:topn]):
        #         title = win32gui.GetWindowText(hwnd)
        #         rect = rects.get(hwnd)
        #         seg = segs.get(hwnd)
        #         print(f"  {i}: hwnd={hwnd} title={repr(title)} rect={rect} segs_top={len(seg['top']) if seg else 0}")


        self.update() # repaint


    def rebuild_surfaces(self, segs: dict[int, SegmentData]):
        """
        Populates self.surfaces with SegmentData
        """
        # clearing surfaces before appending
        self.surfaces.top.clear()
        self.surfaces.right.clear()
        self.surfaces.bottom.clear()
        self.surfaces.left.clear()

        # appending found surfaces
        for hwnd, data in segs.items():
            L, T, R, B = data.rect

            for x1, x2 in data.top:
                self.surfaces.top.append((T, x1, x2, hwnd))

            for x1, x2 in data.bottom:
                self.surfaces.bottom.append((B, x1, x2, hwnd))

            for y1, y2 in data.left:
                self.surfaces.left.append((L, y1, y2, hwnd))

            for y1, y2 in data.right:
                self.surfaces.right.append((R, y1, y2, hwnd))

    # --- Get rect of a window by hwnd ---
    def update_parent_window(self, hwnd) -> tuple|None:
        """
        Returns the rect of the window with provided hwnd or None if it does not exist
        """
        if not hwnd in self.windows_hwnd or not is_window_real(hwnd): return

        rect = None

        try:
            rect = get_extended_frame_bounds(hwnd)
            # print("rect:", rect)
        except Exception:
            return
        
        if not rect: return

        scale = get_window_dpi_scale(hwnd=hwnd)
        if scale <= 0:
            scale = 1.0

        # print("updating window:", hwnd)
        L, T, R, B = rect
        return (L / scale, T / scale, R / scale, B / scale)
        
    def check_parent_window_segment(self, pet: PetTransformData, hwnd, surface_type) -> bool:
        """
        Returns True if pet is on any of the parent_windows' segments
        """
        buffer = 2

        data = self.segments.get(hwnd)
        if not data: return False

        surface_type = pet.parent_surface_type
        if not surface_type: return False

        pos_x, pos_y = pet.anchor

        # L, T, R, B = data.rect


        # x must be inside one of the visible top segments
        if surface_type == SurfaceNormal.UP:
            if any(x1-buffer <= pos_x <= x2+buffer for x1, x2 in data.top):
                return True

        return False

    # --- Movement collision stuff ---
    def bounds(self, pos_x, pos_y):    
        return (
            pos_x - self.hitbox_w/2,
            pos_y - self.hitbox_h,
            pos_x + self.hitbox_w/2,
            pos_y
        )

    def collide_vertical(self, hitbox: PetTransformData, dy, collision_mask: set[SurfaceNormal]):
        L,T,R,B = hitbox.get_rect()
        pos_x, pos_y = hitbox.center

        best: float = dy
        surface_data = None
        collision: SurfaceNormal | None = None

        surfaces = self.surfaces

        if SurfaceNormal.UP in collision_mask:  # moving down
            for y, x1, x2, hwnd in surfaces.top:

                # print(f"For surface y {y} and x1, x2 {x1, x2} our rect is {pos_x, T, B}")

                if pos_x < x1 or pos_x > x2:   # i replaced R and L with pos.x because we care only about the center point
                    continue

                dist = y - B

                if 0 <= dist < best:
                    best = dist
                    collision = SurfaceNormal.UP
                    surface_data = (hwnd, y, x1, x2)
                    # print(y, x1, x2)

        if SurfaceNormal.DOWN in collision_mask:  # moving up
            for y, x1, x2, hwnd in surfaces.bottom:

                if pos_x < x1 or pos_x > x2:
                    continue

                dist = y - T

                if best < dist < 0:
                    best = dist
                    collision = SurfaceNormal.DOWN
                    surface_data = (hwnd, y, x1, x2)
                    # print(y, x1, x2)

        # print(dy, best, collision)
        return best, collision, surface_data

    def collide_horizontal(self, hitbox: PetTransformData, dx, collision_mask: set[SurfaceNormal]):
        L,T,R,B = hitbox.get_rect()

        best: float = dx
        surface_data = None
        collision: SurfaceNormal | None = None

        surfaces = self.surfaces

        if SurfaceNormal.LEFT in collision_mask:  # moving right
            for x, y1, y2, hwnd in surfaces.left:

                if B < y1 or T > y2:
                    continue

                dist = x - R

                if 0 <= dist < best:  # here it should be    if best < dist <= 0   for it to work inside-out, this is what should do next
                    best = dist
                    collision = SurfaceNormal.LEFT
                    surface_data = (hwnd, x, y1, y2)

        if SurfaceNormal.RIGHT in collision_mask:  # moving left
            for x, y1, y2, hwnd in surfaces.right:

                if B < y1 or T > y2:
                    continue

                dist = x - L

                if best < dist <= 0:
                    best = dist
                    collision = SurfaceNormal.RIGHT
                    surface_data = (hwnd, x, y1, y2)

        return best, collision, surface_data

    # --- Find nearest surface in a given direction ---
    def get_nearest_surface(self, direction, hitbox_w, hitbox_h, collision_mask):
        px, py = self.pet.anchor.x, self.pet.anchor.y

        pet_left  = px - hitbox_w / 4
        pet_right = px + hitbox_w / 4
        pet_top   = py - hitbox_h / 2
        pet_bot   = py + hitbox_h / 2

        best_dist = float("inf")
        best_surface = None

        segments = self.segments

        for hwnd, data in segments.items():
            L, T, R, B = data.rect

            if direction == "down":

                for x1, x2 in data.top:
                    if ranges_overlap(pet_left, pet_right, x1, x2):

                        dist = T - pet_bot
                        if 0 <= dist < best_dist:
                            best_dist = dist
                            best_surface = T

            elif direction == "up":

                for x1, x2 in data.bottom:
                    if ranges_overlap(pet_left, pet_right, x1, x2):

                        dist = pet_top - B
                        if 0 <= dist < best_dist:
                            best_dist = dist
                            best_surface = B

            elif direction == "right":

                for y1, y2 in data.left:
                    if ranges_overlap(pet_top, pet_bot, y1, y2):

                        dist = L - pet_right
                        if 0 <= dist < best_dist:
                            best_dist = dist
                            best_surface = L

            elif direction == "left":

                for y1, y2 in data.right:
                    if ranges_overlap(pet_top, pet_bot, y1, y2):

                        dist = pet_left - R
                        if 0 <= dist < best_dist:
                            best_dist = dist
                            best_surface = R

        return best_surface
    

    def paintEvent(self, event):
        # return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing) # type: ignore

        # pen width is in logical pixels; color alpha for nice appearance
        pen = QPen(QColor(0, 200, 60, 255), 25)
        painter.setPen(pen)

        # draw every cached segment using per-window DPI scale conversion
        for hwnd, data in list(self.segments.items()):
            rect = data.rect
            if not rect:
                continue
            L, T, R, B = rect

            # scale = get_window_dpi_scale(hwnd=hwnd)
            # if scale <= 0:
            #     scale = 1.0

            # scaled coords (logical)
            dL = int(round(L))
            dT = int(round(T))
            dR = int(round(R))
            dB = int(round(B))

            # top
            for x1, x2 in data.top:
                sx1 = int(round(x1))
                sx2 = int(round(x2))
                painter.setPen(QColor(0, 200, 60))
                painter.drawLine(sx1, dT, sx2, dT)

            # bottom
            for x1, x2 in data.bottom:
                sx1 = int(round(x1))
                sx2 = int(round(x2))
                painter.setPen(QColor(220, 0, 220))
                painter.drawLine(sx1, dB, sx2, dB)

            # left
            for y1, y2 in data.left:
                sy1 = int(round(y1))
                sy2 = int(round(y2))
                painter.setPen(QColor(20, 150, 255))
                painter.drawLine(dL, sy1, dL, sy2)

            # right
            for y1, y2 in data.right:
                sy1 = int(round(y1))
                sy2 = int(round(y2))
                painter.setPen(QColor(210, 100, 100))
                painter.drawLine(dR, sy1, dR, sy2)

        painter.end()