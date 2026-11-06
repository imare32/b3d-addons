import sys
import ctypes


class WindowHelper:
    def __init__(self):
        self.is_win32 = sys.platform == "win32"
        self.is_linux = sys.platform.startswith("linux")
        self.x11 = None
        self._setup_platform()

    def _setup_platform(self):
        if self.is_win32:
            self._setup_win32()
        elif self.is_linux:
            self._setup_linux()

    def _setup_win32(self):
        try:
            user32 = ctypes.windll.user32
            self.GetForegroundWindow = user32.GetForegroundWindow
            self.GetForegroundWindow.argtypes = []
            self.GetForegroundWindow.restype = ctypes.c_void_p

            self.GetWindowThreadProcessId = user32.GetWindowThreadProcessId
            self.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            self.GetWindowThreadProcessId.restype = ctypes.c_uint32

            self.GetSystemMetrics = user32.GetSystemMetrics
            self.GetSystemMetrics.argtypes = [ctypes.c_int]
            self.GetSystemMetrics.restype = ctypes.c_int

            self.MoveWindow = user32.MoveWindow
            self.MoveWindow.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_bool,
            ]
            self.MoveWindow.restype = ctypes.c_bool

            self.GetWindowRect = user32.GetWindowRect
            self.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            self.GetWindowRect.restype = ctypes.c_bool
        except Exception as e:
            print(f"[b3d] Win32 setup warning: {e}")

    def _setup_linux(self):
        try:
            from ctypes import (
                c_ulong,
                c_int,
                c_char_p,
                c_void_p,
                POINTER,
                c_long,
                c_uint,
                c_ubyte,
                Structure,
                Union,
            )

            self.x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
            x11 = self.x11

            class XClientMessageEvent(Structure):
                _fields_ = [
                    ("type", c_int),
                    ("serial", c_ulong),
                    ("send_event", c_int),
                    ("display", c_void_p),
                    ("window", c_ulong),
                    ("message_type", c_ulong),
                    ("format", c_int),
                    ("data_l", c_long * 5),
                ]

            class XEvent(Union):
                _fields_ = [
                    ("type", c_int),
                    ("xclient", XClientMessageEvent),
                    ("pad", c_long * 24),
                ]

            self.XEvent = XEvent
            self.XClientMessageEvent = XClientMessageEvent

            x11.XOpenDisplay.argtypes = [c_char_p]
            x11.XOpenDisplay.restype = c_void_p

            x11.XDefaultRootWindow.argtypes = [c_void_p]
            x11.XDefaultRootWindow.restype = c_ulong

            x11.XInternAtom.argtypes = [c_void_p, c_char_p, c_int]
            x11.XInternAtom.restype = c_ulong

            x11.XGetWindowProperty.argtypes = [
                c_void_p,
                c_ulong,
                c_ulong,
                c_long,
                c_long,
                c_int,
                c_ulong,
                POINTER(c_ulong),
                POINTER(c_int),
                POINTER(c_ulong),
                POINTER(c_ulong),
                POINTER(POINTER(c_ubyte)),
            ]
            x11.XGetWindowProperty.restype = c_int

            x11.XGetGeometry.argtypes = [
                c_void_p,
                c_ulong,
                POINTER(c_ulong),
                POINTER(c_int),
                POINTER(c_int),
                POINTER(c_uint),
                POINTER(c_uint),
                POINTER(c_uint),
                POINTER(c_uint),
            ]
            x11.XGetGeometry.restype = c_int

            x11.XTranslateCoordinates.argtypes = [
                c_void_p,
                c_ulong,
                c_ulong,
                c_int,
                c_int,
                POINTER(c_int),
                POINTER(c_int),
                POINTER(c_ulong),
            ]
            x11.XTranslateCoordinates.restype = c_int

            x11.XMoveResizeWindow.argtypes = [
                c_void_p,
                c_ulong,
                c_int,
                c_int,
                c_uint,
                c_uint,
            ]
            x11.XMoveResizeWindow.restype = c_int

            x11.XSendEvent.argtypes = [
                c_void_p,
                c_ulong,
                c_int,
                c_long,
                POINTER(XEvent),
            ]
            x11.XSendEvent.restype = c_int

            x11.XDisplayWidth.argtypes = [c_void_p, c_int]
            x11.XDisplayWidth.restype = c_int

            x11.XDisplayHeight.argtypes = [c_void_p, c_int]
            x11.XDisplayHeight.restype = c_int

            x11.XFree.argtypes = [c_void_p]
            x11.XFree.restype = c_int

            x11.XCloseDisplay.argtypes = [c_void_p]
            x11.XCloseDisplay.restype = c_int

            x11.XFlush.argtypes = [c_void_p]
            x11.XFlush.restype = c_int
        except Exception as e:
            print(f"[b3d] Linux X11 setup warning: {e}")
            self.x11 = None

    def get_foreground_window(self):
        if self.is_win32:
            try:
                return self.GetForegroundWindow()
            except Exception:
                return None
        elif self.is_linux and self.x11:
            display = self.x11.XOpenDisplay(None)
            if not display:
                return None
            try:
                from ctypes import c_ulong, c_int, POINTER, byref, c_ubyte

                root = self.x11.XDefaultRootWindow(display)
                atom_active = self.x11.XInternAtom(display, b"_NET_ACTIVE_WINDOW", 0)
                actual_type, actual_format, nitems, bytes_after = (
                    c_ulong(),
                    c_int(),
                    c_ulong(),
                    c_ulong(),
                )
                prop = POINTER(c_ubyte)()

                status = self.x11.XGetWindowProperty(
                    display,
                    root,
                    atom_active,
                    0,
                    1,
                    0,
                    0,
                    byref(actual_type),
                    byref(actual_format),
                    byref(nitems),
                    byref(bytes_after),
                    byref(prop),
                )
                if status == 0 and prop:
                    win_id = ctypes.cast(prop, POINTER(c_ulong)).contents.value
                    self.x11.XFree(prop)
                    return win_id
            except Exception:
                pass
            finally:
                self.x11.XCloseDisplay(display)
        return None

    def get_window_pid(self, hwnd_or_id):
        if not hwnd_or_id:
            return None
        if self.is_win32:
            try:
                window_pid = ctypes.c_ulong(0)
                self.GetWindowThreadProcessId(hwnd_or_id, ctypes.byref(window_pid))
                return window_pid.value
            except Exception:
                return None
        elif self.is_linux and self.x11:
            display = self.x11.XOpenDisplay(None)
            if not display:
                return None
            try:
                from ctypes import c_ulong, c_int, POINTER, byref, c_ubyte

                atom_pid = self.x11.XInternAtom(display, b"_NET_WM_PID", 0)
                actual_type, actual_format, nitems, bytes_after = (
                    c_ulong(),
                    c_int(),
                    c_ulong(),
                    c_ulong(),
                )
                prop_pid = POINTER(c_ubyte)()
                status_pid = self.x11.XGetWindowProperty(
                    display,
                    hwnd_or_id,
                    atom_pid,
                    0,
                    1,
                    0,
                    0,
                    byref(actual_type),
                    byref(actual_format),
                    byref(nitems),
                    byref(bytes_after),
                    byref(prop_pid),
                )
                if status_pid == 0 and prop_pid and nitems.value > 0:
                    pid = ctypes.cast(prop_pid, POINTER(c_ulong)).contents.value
                    self.x11.XFree(prop_pid)
                    return pid
            except Exception:
                pass
            finally:
                self.x11.XCloseDisplay(display)
        return None

    def get_screen_size(self):
        if self.is_win32:
            try:
                return self.GetSystemMetrics(0), self.GetSystemMetrics(1)
            except Exception:
                return 1920, 1080
        elif self.is_linux and self.x11:
            display = self.x11.XOpenDisplay(None)
            if not display:
                return 1920, 1080
            try:
                sw = self.x11.XDisplayWidth(display, 0)
                sh = self.x11.XDisplayHeight(display, 0)
                return sw, sh
            except Exception:
                return 1920, 1080
            finally:
                self.x11.XCloseDisplay(display)
        return 1920, 1080

    def get_window_rect(self, hwnd_or_id):
        if not hwnd_or_id:
            return None
        if self.is_win32:
            try:

                class RECT(ctypes.Structure):
                    _fields_ = [
                        ("left", ctypes.c_long),
                        ("top", ctypes.c_long),
                        ("right", ctypes.c_long),
                        ("bottom", ctypes.c_long),
                    ]

                rect = RECT()
                if self.GetWindowRect(hwnd_or_id, ctypes.byref(rect)):
                    w = rect.right - rect.left
                    h = rect.bottom - rect.top
                    return rect.left, rect.top, rect.right, rect.bottom, w, h
            except Exception:
                return None
        elif self.is_linux and self.x11:
            display = self.x11.XOpenDisplay(None)
            if not display:
                return None
            try:
                from ctypes import c_ulong, c_int, c_uint, byref

                root = self.x11.XDefaultRootWindow(display)
                root_ret, x_ret, y_ret = c_ulong(), c_int(), c_int()
                w_ret, h_ret, border_ret, depth_ret = (
                    c_uint(),
                    c_uint(),
                    c_uint(),
                    c_uint(),
                )
                self.x11.XGetGeometry(
                    display,
                    hwnd_or_id,
                    byref(root_ret),
                    byref(x_ret),
                    byref(y_ret),
                    byref(w_ret),
                    byref(h_ret),
                    byref(border_ret),
                    byref(depth_ret),
                )

                dest_x, dest_y, child_ret = c_int(), c_int(), c_ulong()
                self.x11.XTranslateCoordinates(
                    display,
                    hwnd_or_id,
                    root,
                    0,
                    0,
                    byref(dest_x),
                    byref(dest_y),
                    byref(child_ret),
                )
                left = dest_x.value
                top = dest_y.value
                w = w_ret.value
                h = h_ret.value
                return left, top, left + w, top + h, w, h
            except Exception:
                return None
            finally:
                self.x11.XCloseDisplay(display)
        return None

    def move_resize_window(self, hwnd_or_id, x, y, width, height):
        if not hwnd_or_id:
            return False
        if self.is_win32:
            try:
                return bool(
                    self.MoveWindow(
                        hwnd_or_id, int(x), int(y), int(width), int(height), True
                    )
                )
            except Exception:
                return False
        elif self.is_linux and self.x11:
            display = self.x11.XOpenDisplay(None)
            if not display:
                return False
            try:
                from ctypes import byref

                root = self.x11.XDefaultRootWindow(display)
                atom_moveresize = self.x11.XInternAtom(
                    display, b"_NET_MOVERESIZE_WINDOW", 0
                )

                event = self.XEvent()
                event.xclient.type = 33
                event.xclient.serial = 0
                event.xclient.send_event = True
                event.xclient.display = display
                event.xclient.window = hwnd_or_id
                event.xclient.message_type = atom_moveresize
                event.xclient.format = 32
                event.xclient.data_l[0] = 0x0F00 | 1
                event.xclient.data_l[1] = int(x)
                event.xclient.data_l[2] = int(y)
                event.xclient.data_l[3] = int(width)
                event.xclient.data_l[4] = int(height)

                mask = (1 << 19) | (1 << 20)
                self.x11.XSendEvent(display, root, False, mask, byref(event))
                self.x11.XMoveResizeWindow(
                    display, hwnd_or_id, int(x), int(y), int(width), int(height)
                )
                self.x11.XFlush(display)
                return True
            except Exception:
                return False
            finally:
                self.x11.XCloseDisplay(display)
        return False


# Shared singleton instance for the addon
window_helper = WindowHelper()
