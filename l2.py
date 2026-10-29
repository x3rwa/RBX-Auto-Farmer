"""Roblox Fishing + Inventory automation (Windows).

F6 starts/resumes, F8 pauses, and closing the GUI exits.
Fish for 60 seconds, then stop fishing for 10 seconds. While fishing is
paused, attempt Items -> Equip Best -> Close. Resume fishing after the 10-second
break even if an inventory button is not found. Never fish during the break.
Repeat until stopped. Roblox must remain the foreground window.
"""
import ctypes
import queue
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

import cv2
import keyboard
import mss
import numpy as np
import pyautogui

# Original fishing parameters (unchanged)
REF_W, REF_H = 2047, 1152
CLICK_HOLD = 0.0001
BETWEEN_CLICKS = 0.01

# Timing: 60 seconds of fishing, then 20 seconds without fishing clicks.
FISHING_SECONDS = 3 * 60
INVENTORY_BREAK_SECONDS = 5

# Original inventory parameters (unchanged)
STEPS = (
    ('Items', 'items.png', (15, 11, 139, 136), 5.0, 0.6),
    ('Equip Best', 'equip_best.png', (9, 17, 271, 112), 6.0, 0.35),
    ('Close', 'close.png', (6, 9, 133, 138), 5.0, 0.25),
)
MATCH_THRESHOLD = 0.79
SCALES = (1.0, 0.9, 1.1, 0.8, 1.2)

try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', wintypes.LONG), ('dy', wintypes.LONG),
        ('mouseData', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD), ('dwExtraInfo', ULONG_PTR),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ('u',)
    _fields_ = [('type', wintypes.DWORD), ('u', INPUTUNION)]


class RECT(ctypes.Structure):
    _fields_ = [('left', wintypes.LONG), ('top', wintypes.LONG),
                ('right', wintypes.LONG), ('bottom', wintypes.LONG)]


class POINT(ctypes.Structure):
    _fields_ = [('x', wintypes.LONG), ('y', wintypes.LONG)]


user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]

run_event = threading.Event()
closing = threading.Event()
actions = queue.Queue()
state_lock = threading.Lock()

phase = 'FISHING'
remaining_seconds = float(FISHING_SECONDS)
break_remaining = float(INVENTORY_BREAK_SECONDS)
inventory_step = 0
inventory_finished = False
inventory_success = False
completed_cycles = 0
note = 'Press F6 or Start to begin.'
started_once = False


def update_note(message):
    global note
    with state_lock:
        note = message
    print(message, flush=True)


def relative_move(dx, dy):
    packet = INPUT(type=0, mi=MOUSEINPUT(int(dx), int(dy), 0, 0x0001, 0, 0))
    written = user32.SendInput(1, ctypes.byref(packet), ctypes.sizeof(INPUT))
    if written != 1:
        raise ctypes.WinError()


def fresh_bubble_approach(x, y):
    # Original shake: go to target, 2 px left, then 2 px right.
    pyautogui.moveTo(x, y, duration=0.08)
    relative_move(-2, 0)
    time.sleep(0.014)
    relative_move(2, 0)
    time.sleep(0.01)


def roblox_foreground():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    length = user32.GetWindowTextLengthW(hwnd)
    title = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, title, length + 1)
    return 'roblox' in title.value.lower()


def roblox_region():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    length = user32.GetWindowTextLengthW(hwnd)
    title = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, title, length + 1)
    if 'roblox' not in title.value.lower():
        return None

    rect = RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    top_left = POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(top_left))
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None
    return {'left': top_left.x, 'top': top_left.y,
            'width': width, 'height': height}


def detect_bubble(frame):
    # Original fishing circle detector (unchanged).
    region = frame[225:830, 350:1710]
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, None, fx=0.5, fy=0.5)
    small = cv2.medianBlur(small, 5)

    circles = cv2.HoughCircles(
        small, cv2.HOUGH_GRADIENT, dp=1.2, minDist=65,
        param1=110, param2=32, minRadius=35, maxRadius=135
    )
    if circles is None:
        return None

    best, best_score = None, -1e9
    for sx, sy, sr in circles[0]:
        x, y, radius = int(350 + 2 * sx), int(225 + 2 * sy), int(2 * sr)
        angles = np.linspace(0, 2 * np.pi, 24, endpoint=False)
        xs = np.clip((sx + sr * np.cos(angles)).astype(int), 0, small.shape[1] - 1)
        ys = np.clip((sy + sr * np.sin(angles)).astype(int), 0, small.shape[0] - 1)
        rim = np.mean(small[ys, xs])
        cx, cy = int(sx), int(sy)
        center = np.mean(small[max(0, cy-8):cy+9, max(0, cx-8):cx+9])
        score = (center - rim) * 0.8 - abs(radius - 105) * 0.2
        if score > best_score:
            best, best_score = (x, y), score

    return best if best_score > 5 else None


def can_fish():
    with state_lock:
        allowed = phase == 'FISHING'
    return allowed and run_event.is_set() and not closing.is_set() and roblox_foreground()


def click_six_times(point, monitor):
    if not can_fish():
        return
    x = int(monitor['left'] + round(point[0] * monitor['width'] / REF_W))
    y = int(monitor['top'] + round(point[1] * monitor['height'] / REF_H))

    fresh_bubble_approach(x, y)
    if not can_fish():
        return

    print(f'Circle detected: ({x}, {y}); clicking 6 times', flush=True)
    for _ in range(6):
        if not can_fish():
            return
        try:
            pyautogui.mouseDown(button='left')
            time.sleep(CLICK_HOLD)
        finally:
            pyautogui.mouseUp(button='left')
        time.sleep(BETWEEN_CLICKS)


def resource_path(name):
    # Supports direct execution and PyInstaller --onefile with --add-data.
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return base / 'images' / name


def load_templates():
    result = {}
    for label, filename, crop, timeout, delay in STEPS:
        path = resource_path(filename)
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise FileNotFoundError(f'Missing inventory button image: {path}')
        x0, y0, x1, y1 = crop
        image = image[y0:y1, x0:x1]
        if image.size == 0:
            raise ValueError(f'Invalid template crop: {path}')
        result[label] = (image, timeout, delay)
    return result


def locate(gray_frame, template, label):
    # Original inventory template matcher; Items is restricted to bottom hotbar.
    height, width = gray_frame.shape[:2]
    if label == 'Items':
        x0 = int(width * 0.19)
        x1 = int(width * 0.44)
        y0 = int(height * 0.76)
        y1 = height
    else:
        x0, y0, x1, y1 = 0, 0, width, height

    search_area = gray_frame[y0:y1, x0:x1]
    best_score, best_point = -1.0, None
    for scale in SCALES:
        w = max(1, round(template.shape[1] * scale))
        h = max(1, round(template.shape[0] * scale))
        if w > search_area.shape[1] or h > search_area.shape[0]:
            continue
        scaled = cv2.resize(template, (w, h), interpolation=cv2.INTER_LINEAR)
        result = cv2.matchTemplate(search_area, scaled, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(result)
        if score > best_score:
            best_score = score
            best_point = (x0 + loc[0] + w // 2,
                          y0 + loc[1] + h // 2)
    return best_point, best_score


def can_inventory():
    """Inventory actions may only happen during the 10-second break."""
    with state_lock:
        in_break = phase == 'BREAK' and break_remaining > 0
    return in_break and run_event.is_set() and not closing.is_set() and roblox_foreground()


def wait_until_ready(require_break=False):
    """Wait for F6/Roblox focus, but never cross a break boundary."""
    while not closing.is_set():
        if require_break:
            with state_lock:
                if phase != 'BREAK' or break_remaining <= 0:
                    return False
        if run_event.is_set() and roblox_foreground():
            return True
        time.sleep(0.05)
    return False


def active_delay(seconds, require_break=False):
    """Count button delays only while automation is active."""
    remaining = seconds
    while remaining > 0 and not closing.is_set():
        if not wait_until_ready(require_break=require_break):
            return False
        started = time.monotonic()
        time.sleep(min(0.025, remaining))
        if require_break and not can_inventory():
            return False
        if run_event.is_set() and roblox_foreground():
            remaining -= time.monotonic() - started
    return not closing.is_set()


def find_and_click(capture, label, template, timeout):
    """Find a button; never click it outside the dedicated inventory break."""
    remaining_timeout = timeout
    last_score = -1.0
    while remaining_timeout > 0 and not closing.is_set():
        if not wait_until_ready(require_break=True):
            return False
        started = time.monotonic()
        region = roblox_region()
        if region is None:
            continue
        image = np.asarray(capture.grab(region))[:, :, :3]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        point, score = locate(gray, template, label)
        last_score = score
        if point is not None and score >= MATCH_THRESHOLD:
            x = region['left'] + point[0]
            y = region['top'] + point[1]
            if not can_inventory():
                return False
            fresh_bubble_approach(x, y)
            if not can_inventory():
                return False
            pyautogui.click()
            print(f'{label}: clicked at ({x}, {y}), match={score:.2f}', flush=True)
            return True
        remaining_timeout -= time.monotonic() - started
        if can_inventory() and remaining_timeout > 0:
            delay = min(0.12, remaining_timeout)
            time.sleep(delay)
            remaining_timeout -= delay

    if can_inventory():
        update_note(f'{label} not found (match={last_score:.2f}). '
                    'Fishing remains stopped until the 7-second break ends.')
    return False


def run_inventory(templates, capture):
    """Attempt inventory once per break; failure must never pause the whole bot."""
    global inventory_step, inventory_finished, inventory_success, completed_cycles
    succeeded = False
    opened_items = False
    failed_step = None
    try:
        for index, (label, _, _, _, _) in enumerate(STEPS):
            if not can_inventory():
                return
            template, timeout, delay = templates[label]
            update_note(f'Inventory: {label} ({index + 1}/{len(STEPS)})')
            if not find_and_click(capture, label, template, timeout):
                if can_inventory():
                    failed_step = label
                return
            if label == 'Items':
                opened_items = True
            with state_lock:
                inventory_step = index + 1
            if not active_delay(delay, require_break=True):
                return
        succeeded = True
    finally:
        # If Equip Best is absent, try closing the open inventory instead
        # of leaving its panel on screen. Only do so within the break.
        if failed_step is not None and opened_items and failed_step != 'Close' and can_inventory():
            update_note(f'{failed_step} not found. Trying to close the inventory.')
            close_template, close_timeout, _ = templates['Close']
            find_and_click(capture, 'Close', close_template, close_timeout)

        with state_lock:
            still_break = phase == 'BREAK'
            if still_break:
                inventory_finished = True  # also for a failed lookup
                inventory_success = succeeded
                if succeeded:
                    completed_cycles += 1
        if still_break:
            if succeeded:
                update_note('Items -> Equip Best -> Close done. Waiting for break to end.')
            elif failed_step:
                update_note(f'{failed_step} not found. Fishing stays off for all 10 seconds.')


def timer_worker():
    """Enforce 60 seconds fishing / 10 seconds break, regardless of inventory outcome."""
    global phase, remaining_seconds, break_remaining, inventory_step
    global inventory_finished, inventory_success
    last_tick = time.monotonic()
    was_active = False
    while not closing.is_set():
        now = time.monotonic()
        active = run_event.is_set() and roblox_foreground()
        announcement = None
        if active:
            elapsed = max(0.0, now - last_tick) if was_active else 0.0
            with state_lock:
                if phase == 'FISHING':
                    remaining_seconds = max(0.0, remaining_seconds - elapsed)
                    if remaining_seconds <= 0:
                        phase = 'BREAK'
                        break_remaining = float(INVENTORY_BREAK_SECONDS)
                        inventory_step = 0
                        inventory_finished = False
                        inventory_success = False
                        announcement = 'Fishing OFF for 10 seconds. Inventory attempt starts.'
                elif phase == 'BREAK':
                    break_remaining = max(0.0, break_remaining - elapsed)
                    if break_remaining <= 0:
                        # Critical: this does not depend on Equip Best/Close success.
                        phase = 'FISHING'
                        remaining_seconds = float(FISHING_SECONDS)
                        announcement = '10-second break ended. Fishing resumed for 60 seconds.'
        last_tick = now
        was_active = active
        if announcement:
            update_note(announcement)
        time.sleep(0.02)


def automation_worker():
    try:
        templates = load_templates()
        with mss.mss() as capture:
            monitor = capture.monitors[1]
            while not closing.is_set():
                if not run_event.is_set() or not roblox_foreground():
                    time.sleep(0.075)
                    continue

                with state_lock:
                    current_phase = phase
                    finished = inventory_finished
                if current_phase == 'BREAK':
                    if not finished:
                        run_inventory(templates, capture)
                    else:
                        time.sleep(0.05)
                    continue

                frame = np.array(capture.grab(monitor))[:, :, :3]
                frame = cv2.resize(frame, (REF_W, REF_H), interpolation=cv2.INTER_AREA)
                target = detect_bubble(frame)
                if can_fish():
                    if target is not None:
                        click_six_times(target, monitor)
                    else:
                        time.sleep(0.06)

    except pyautogui.FailSafeException:
        actions.put(('error', 'Emergency stop: mouse moved to top-left.'))
    except Exception as exc:
        actions.put(('error', f'Error: {exc}'))


root = tk.Tk()
root.title('Roblox Fishing + Inventory')
root.geometry('400x275')
root.resizable(False, False)
root.configure(bg='#161b25')

tk.Label(root, text='Roblox Fishing + Inventory', bg='#161b25', fg='white',
         font=('Segoe UI', 17, 'bold')).pack(pady=(17, 9))

status_label = tk.Label(root, text='STOPPED', bg='#161b25', fg='#fb7185',
                        font=('Segoe UI', 12, 'bold'))
status_label.pack()

countdown_label = tk.Label(root, text='Fishing remaining: 01:00', bg='#161b25', fg='white',
                           font=('Segoe UI', 11))
countdown_label.pack(pady=(8, 4))

cycles_label = tk.Label(root, text='Inventory cycles: 0', bg='#161b25', fg='#a1a1aa',
                        font=('Segoe UI', 9))
cycles_label.pack()

buttons = tk.Frame(root, bg='#161b25')
buttons.pack(pady=13)


def start_bot():
    global started_once
    if closing.is_set():
        return
    started_once = True
    run_event.set()


def stop_bot():
    run_event.clear()


tk.Button(buttons, text='Start (F6)', command=start_bot,
          bg='#16a34a', fg='white', activebackground='#15803d',
          font=('Segoe UI', 11, 'bold'), relief='flat', width=12, height=2
          ).pack(side='left', padx=7)

tk.Button(buttons, text='Stop (F8)', command=stop_bot,
          bg='#dc2626', fg='white', activebackground='#b91c1c',
          font=('Segoe UI', 11, 'bold'), relief='flat', width=12, height=2
          ).pack(side='left', padx=7)

info_label = tk.Label(root, text='60s fishing / 10s break + inventory / repeat',
                      bg='#161b25', fg='#a1a1aa', font=('Segoe UI', 9))
info_label.pack(pady=(0, 3))

message_label = tk.Label(root, text='Press F6 or Start to begin.', bg='#161b25',
                         fg='#a1a1aa', font=('Segoe UI', 9), wraplength=375)
message_label.pack()


def ui_update():
    if closing.is_set():
        return
    while not actions.empty():
        event = actions.get_nowait()
        if event == 'start':
            start_bot()
        elif event == 'stop':
            stop_bot()
        elif isinstance(event, tuple) and event[0] == 'error':
            stop_bot()
            update_note(event[1])

    with state_lock:
        current_phase = phase
        remaining = remaining_seconds
        pause_remaining = break_remaining
        finished = inventory_finished
        success = inventory_success
        step_idx = inventory_step
        cycles = completed_cycles
        message = note

    if not run_event.is_set():
        status_label.config(text='PAUSED' if started_once else 'STOPPED', fg='#fb7185')
    elif not roblox_foreground():
        status_label.config(text='WAITING FOR ROBLOX', fg='#fbbf24')
    elif current_phase == 'FISHING':
        status_label.config(text='FISHING', fg='#4ade80')
    elif finished:
        status_label.config(text='BREAK - INVENTORY DONE' if success else 'BREAK - INVENTORY SKIPPED',
                            fg='#fbbf24')
    else:
        status_label.config(text='BREAK - INVENTORY RUNNING', fg='#38bdf8')

    seconds = max(0, int(remaining + 0.999))
    pause_seconds = max(0, int(pause_remaining + 0.999))
    if current_phase == 'FISHING':
        countdown_text = f'Fishing remaining: {seconds // 60:02d}:{seconds % 60:02d}'
    elif pause_seconds:
        countdown_text = f'Break remaining: {pause_seconds // 60:02d}:{pause_seconds % 60:02d}'
    else:
        countdown_text = 'Returning to fishing...'
    countdown_label.config(text=countdown_text)
    cycles_label.config(text=f'Inventory cycles: {cycles}')
    message_label.config(text=message)
    root.after(100, ui_update)


def on_close():
    closing.set()
    run_event.clear()
    keyboard.unhook_all_hotkeys()
    root.destroy()


root.protocol('WM_DELETE_WINDOW', on_close)
keyboard.add_hotkey('f6', lambda: actions.put('start'))
keyboard.add_hotkey('f8', lambda: actions.put('stop'))
threading.Thread(target=automation_worker, daemon=True).start()
threading.Thread(target=timer_worker, daemon=True).start()
root.after(100, ui_update)
root.mainloop()
