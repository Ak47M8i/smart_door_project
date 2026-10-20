import os
import json
import time
import threading
import serial
import serial.tools.list_ports
from flask import Flask, render_template_string, request, jsonify, session

app = Flask(__name__)
app.secret_key = "remote_fob_secret_key"

# --- Persistent PIN Management ---
PIN_FILE = "pin.json"

def get_current_pin():
    if not os.path.exists(PIN_FILE):
        set_pin("1234")
        return "1234"
    try:
        with open(PIN_FILE, "r") as f:
            data = json.load(f)
            return data.get("pin", "1234")
    except Exception:
        return "1234"

def set_pin(new_pin):
    with open(PIN_FILE, "w") as f:
        json.dump({"pin": str(new_pin)}, f)

# --- Global Hardware State ---
ser = None
current_port = "None"
hw_status = "Searching for Arduino..."
door_status = "CLOSED"
lock_status = "LOCKED"
serial_lock = threading.Lock()

def auto_detect_port():
    ports = serial.tools.list_ports.comports()
    # 1. Prefer known Arduino / USB Serial chips
    for p in ports:
        desc = p.description.lower()
        if "arduino" in desc or "ch340" in desc or "usb serial" in desc or "cp210" in desc or "ftdi" in desc:
            return p.device
    # 2. Fallback to any active COM port if present
    for p in ports:
        if p.device.upper().startswith("COM"):
            return p.device
    return None

# --- Background Self-Healing Connection Thread ---
def hardware_manager():
    global ser, current_port, hw_status, door_status, lock_status
    while True:
        # Check if we need to establish / re-establish connection
        if ser is None or not ser.is_open:
            port = auto_detect_port()
            if port:
                try:
                    with serial_lock:
                        ser = serial.Serial(port, 9600, timeout=1)
                        time.sleep(2)  # Wait for Arduino bootloader to stabilize
                        current_port = port
                        hw_status = f"Connected ({port})"
                        print(f"[RECONNECTED] Arduino found and connected on {port}")
                except serial.SerialException as e:
                    err_str = str(e).lower()
                    if "access is denied" in err_str or "permission" in err_str:
                        hw_status = "PORT BUSY: Close Arduino IDE Serial Monitor!"
                    else:
                        hw_status = f"Connecting error on {port}..."
                    ser = None
            else:
                hw_status = "Arduino unplugged. Plug in USB cable."
                ser = None
            time.sleep(1.5)
            continue

        # If connected, continuously read incoming lines
        try:
            if ser.in_waiting > 0:
                with serial_lock:
                    line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line == "STATUS:OPEN":
                    door_status = "OPEN"
                elif line == "STATUS:CLOSED":
                    door_status = "CLOSED"
                elif line == "ACK:LOCKED":
                    lock_status = "LOCKED"
                elif line == "ACK:UNLOCKED":
                    lock_status = "UNLOCKED"
        except Exception:
            # USB cable was disconnected or port dropped
            print("[DISCONNECTED] Lost connection to Arduino.")
            with serial_lock:
                try:
                    if ser:
                        ser.close()
                except Exception:
                    pass
                ser = None
            hw_status = "Disconnected. Reconnecting..."
            time.sleep(1)

        time.sleep(0.04)

threading.Thread(target=hardware_manager, daemon=True).start()

def safe_write(payload):
    with serial_lock:
        if ser and ser.is_open:
            try:
                ser.write(payload)
                ser.flush()
                return True
            except Exception:
                return False
    return False

# --- Remote UI ---
REMOTE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Door Remote</title>
    <style>
        * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f1115; color: #fff; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; padding: 15px; }
        .fob { background: #1a1d24; width: 100%; max-width: 340px; padding: 25px; border-radius: 28px; box-shadow: 0 12px 35px rgba(0,0,0,0.6); border: 1px solid #282c35; text-align: center; }
        .title { font-size: 17px; font-weight: 600; color: #8f95a3; margin-bottom: 12px; letter-spacing: 0.5px; }
        .hw-badge { font-size: 11px; padding: 5px 10px; border-radius: 20px; display: inline-block; margin-bottom: 20px; font-weight: 600; }
        .hw-ok { background: rgba(34,197,94,0.15); color: #4ade80; border: 1px solid rgba(74,222,128,0.3); }
        .hw-warn { background: rgba(239,68,68,0.15); color: #f87171; border: 1px solid rgba(248,113,113,0.3); }
        .indicator-row { display: flex; gap: 10px; margin-bottom: 25px; }
        .indicator { flex: 1; padding: 10px 5px; border-radius: 12px; font-size: 13px; font-weight: bold; background: #222630; }
        .tag-active { color: #4ade80; border: 1px solid rgba(74,222,128,0.3); }
        .tag-danger { color: #f87171; border: 1px solid rgba(248,113,113,0.3); }
        .tag-info { color: #60a5fa; border: 1px solid rgba(96,165,250,0.3); }
        .btn-large { width: 100%; height: 70px; margin: 10px 0; border: none; border-radius: 18px; font-size: 18px; font-weight: 700; cursor: pointer; transition: transform 0.1s; }
        .btn-large:active { transform: scale(0.97); }
        .btn-unlock { background: #22c55e; color: #042f13; box-shadow: 0 6px 20px rgba(34,197,94,0.3); }
        .btn-lock { background: #ef4444; color: #450a0a; box-shadow: 0 6px 20px rgba(239,68,68,0.3); }
        .btn-secondary { background: transparent; border: 1px solid #333946; color: #8f95a3; width: 100%; padding: 10px; border-radius: 12px; margin-top: 10px; font-weight: 600; cursor: pointer; }
        .pin-box { margin: 20px 0 10px 0; }
        .pin-input { width: 160px; height: 50px; text-align: center; font-size: 26px; letter-spacing: 12px; border-radius: 14px; border: 1px solid #3b4252; background: #222630; color: #fff; outline: none; }
        .pin-input:focus { border-color: #60a5fa; }
        .pin-btn { width: 100%; height: 50px; background: #3b82f6; color: white; border: none; border-radius: 14px; font-size: 16px; font-weight: bold; cursor: pointer; margin-top: 15px; }
        .error-text { color: #f87171; font-size: 13px; margin-top: 10px; display: none; }
        #pinChangePanel { display: none; background: #222630; border-radius: 16px; padding: 15px; margin-top: 15px; border: 1px solid #333946; }
        .modal-input { width: 90%; height: 40px; margin: 6px 0; border-radius: 8px; border: 1px solid #444; background: #1a1d24; color: white; text-align: center; font-size: 16px; }
        .save-btn { width: 90%; height: 38px; background: #3b82f6; color: white; border: none; border-radius: 8px; font-weight: bold; margin-top: 8px; cursor: pointer; }
        .alert-msg { font-size: 12px; margin-top: 6px; }
    </style>
</head>
<body>
    <div class="fob">
        <div class="title">SMART DOOR REMOTE</div>
        <div id="hwBadge" class="hw-badge hw-warn">Checking Hardware...</div>

        {% if not session.get('authorized') %}
            <!-- PIN ENTRY SCREEN -->
            <div class="pin-box">
                <p style="font-size: 14px; color: #aaa; margin: 0 0 15px 0;">Enter 4-Digit PIN</p>
                <input type="password" id="pinInput" class="pin-input" maxlength="4" pattern="\\d*" inputmode="numeric" autofocus>
                <button type="button" class="pin-btn" onclick="verifyPin()">Unlock Remote</button>
                <div id="pinError" class="error-text">Incorrect PIN</div>
            </div>

        {% else %}
            <!-- ACTIVE REMOTE -->
            <div class="indicator-row">
                <div id="lockState" class="indicator tag-danger">LOCKED</div>
                <div id="doorState" class="indicator tag-info">DOOR CLOSED</div>
            </div>

            <button type="button" class="btn-large btn-unlock" onclick="send('unlock')">
                UNLOCK
            </button>

            <button type="button" class="btn-large btn-lock" onclick="send('lock')">
                LOCK
            </button>

            <button type="button" class="btn-secondary" onclick="togglePinPanel()">⚙ Change PIN</button>

            <div id="pinChangePanel">
                <p style="margin: 0 0 8px 0; font-size: 13px; font-weight: bold; color: #aaa;">Update 4-Digit PIN</p>
                <input type="password" id="currPin" class="modal-input" placeholder="Current PIN" maxlength="4" inputmode="numeric">
                <input type="password" id="newPin" class="modal-input" placeholder="New 4-Digit PIN" maxlength="4" inputmode="numeric">
                <button type="button" class="save-btn" onclick="changePin()">Save New PIN</button>
                <div id="pinChangeMsg" class="alert-msg"></div>
            </div>

            <button type="button" class="btn-secondary" style="margin-top: 10px;" onclick="lockRemote()">Lock Remote</button>
        {% endif %}
    </div>

    <script>
        function updateStatus() {
            fetch('/status')
            .then(res => res.json())
            .then(data => {
                const hwBadge = document.getElementById('hwBadge');
                if (hwBadge) {
                    hwBadge.innerText = data.hw;
                    hwBadge.className = 'hw-badge ' + (data.hw.startsWith('Connected') ? 'hw-ok' : 'hw-warn');
                }

                const lockEl = document.getElementById('lockState');
                const doorEl = document.getElementById('doorState');
                if (lockEl && doorEl) {
                    lockEl.innerText = data.lock;
                    lockEl.className = 'indicator ' + (data.lock === 'LOCKED' ? 'tag-danger' : 'tag-active');

                    doorEl.innerText = 'DOOR ' + data.door;
                    doorEl.className = 'indicator ' + (data.door === 'OPEN' ? 'tag-danger' : 'tag-info');
                }
            });
        }
        setInterval(updateStatus, 800);
        updateStatus();

        function verifyPin() {
            const pin = document.getElementById('pinInput').value.trim();
            fetch('/verify', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pin: pin })
            })
            .then(res => res.json())
            .then(data => {
                if (data.ok) {
                    location.reload();
                } else {
                    const err = document.getElementById('pinError');
                    err.style.display = 'block';
                    document.getElementById('pinInput').value = '';
                }
            });
        }

        const pinField = document.getElementById('pinInput');
        if (pinField) {
            pinField.addEventListener('keypress', function(e) {
                if (e.key === 'Enter') verifyPin();
            });
        }

        function togglePinPanel() {
            const panel = document.getElementById('pinChangePanel');
            panel.style.display = (panel.style.display === 'block') ? 'none' : 'block';
        }

        function changePin() {
            const current_pin = document.getElementById('currPin').value.trim();
            const new_pin = document.getElementById('newPin').value.trim();
            const msgBox = document.getElementById('pinChangeMsg');

            if (!current_pin || !new_pin) {
                msgBox.innerText = "Please fill in both fields.";
                msgBox.style.color = "#f87171";
                return;
            }

            fetch('/change_pin', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ current_pin: current_pin, new_pin: new_pin })
            })
            .then(res => res.json())
            .then(data => {
                if (data.ok) {
                    msgBox.innerText = "PIN updated successfully!";
                    msgBox.style.color = "#4ade80";
                    document.getElementById('currPin').value = '';
                    document.getElementById('newPin').value = '';
                    setTimeout(() => togglePinPanel(), 1200);
                } else {
                    msgBox.innerText = data.error || "Update failed.";
                    msgBox.style.color = "#f87171";
                }
            })
            .catch(() => {
                msgBox.innerText = "Network error.";
                msgBox.style.color = "#f87171";
            });
        }

        function lockRemote() {
            fetch('/logout', { method: 'POST' }).then(() => location.reload());
        }

        function send(cmd) {
            fetch('/' + cmd, { method: 'POST' });
        }
    </script>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(REMOTE_HTML)

@app.route('/verify', methods=['POST'])
def verify():
    data = request.get_json() or {}
    active_pin = get_current_pin()
    if data.get('pin') == active_pin:
        session['authorized'] = True
        safe_write(b"REMOTE_CONNECT\n")
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 401

@app.route('/change_pin', methods=['POST'])
def change_pin():
    if not session.get('authorized'):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    
    data = request.get_json() or {}
    current_pin = data.get('current_pin', '').strip()
    new_pin = data.get('new_pin', '').strip()

    if current_pin != get_current_pin():
        return jsonify({"ok": False, "error": "Incorrect current PIN."}), 400

    if len(new_pin) != 4 or not new_pin.isdigit():
        return jsonify({"ok": False, "error": "New PIN must be exactly 4 digits."}), 400

    set_pin(new_pin)
    return jsonify({"ok": True})

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    safe_write(b"REMOTE_DISCONNECT\n")
    return jsonify({"ok": True})

@app.route('/unlock', methods=['POST'])
def unlock():
    if not session.get('authorized'):
        return jsonify({"error": "Unauthorized"}), 401
    safe_write(b"UNLOCK\n")
    return jsonify({"status": "ok"})

@app.route('/lock', methods=['POST'])
def lock():
    if not session.get('authorized'):
        return jsonify({"error": "Unauthorized"}), 401
    safe_write(b"LOCK\n")
    return jsonify({"status": "ok"})

@app.route('/status')
def status():
    return jsonify({
        "door": door_status,
        "lock": lock_status,
        "hw": hw_status
    })

if __name__ == '__main__':
    # Threaded and single-process to ensure stable, perpetual serial connections
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)