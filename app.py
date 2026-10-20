from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import serial
import serial.tools.list_ports
import time
import threading
import json
import os

app = Flask(__name__)
app.secret_key = 'smart_door_secure_key'

# Automatically find the Arduino COM port
def find_arduino_port():
    ports = serial.tools.list_ports.comports()
    for port in ports:
        desc = port.description.lower()
        if 'arduino' in desc or 'ch340' in desc or 'usb serial' in desc or 'cp210' in desc:
            print(f"Auto-detected Arduino on {port.device} ({port.description})")
            return port.device
    if ports:
        print(f"Fallback to available port: {ports[0].device}")
        return ports[0].device
    return 'COM6'

PORT = find_arduino_port()
BAUD = 9600

arduino = None
try:
    arduino = serial.Serial(PORT, BAUD, timeout=1)
    time.sleep(2)
    print(f"Successfully connected to Arduino on {PORT}")
except Exception as e:
    print(f"Warning: Could not open serial port {PORT}: {e}. Running in simulation mode.")

door_status = "CLOSED"

# Use absolute path so users.json is permanently saved in the project folder
USERS_FILE = os.path.join(os.path.dirname(__file__), 'users.json')

def load_users():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return []
    default_users = [
        {
            "email": "arkay509@gmail.com",
            "phone": "1234567890",
            "password": "SecurePassword123"
        }
    ]
    save_users(default_users)
    return default_users

def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=4)

USERS_DB = load_users()

# Background thread to read status updates from Arduino
def read_from_arduino():
    global door_status
    while arduino and arduino.is_open:
        try:
            if arduino.in_waiting > 0:
                line = arduino.readline().decode('utf-8').strip()
                if line.startswith("STATUS:"):
                    door_status = line.split(":", 1)[1]
                    print(f"Door Status Updated: {door_status}")
        except Exception as e:
            print(f"Serial read error: {e}")
            break
        time.sleep(0.1)

if arduino:
    thread = threading.Thread(target=read_from_arduino, daemon=True)
    thread.start()

@app.route('/')
def index():
    if not session.get('authenticated'):
        return redirect(url_for('login'))
    return render_template('index.html', status=door_status)

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        identifier = request.form.get('identifier')
        identifier = identifier.strip() if identifier else ''
        password = request.form.get('password') or ''
        
        global USERS_DB
        USERS_DB = load_users() # Always load latest accounts from file
        
        user = next((u for u in USERS_DB if u['email'] == identifier or u['phone'] == identifier), None)
        
        if user and user['password'] == password:
            session['authenticated'] = True
            session['user'] = identifier
            return redirect(url_for('index'))
        else:
            error = "Invalid email/phone or password. Please try again."
            
    return render_template('login.html', error=error)

@app.route('/signup', methods=['POST'])
def signup():
    data = request.get_json() or {}
    email = data.get('email')
    email = email.strip() if email else ''
    phone = data.get('phone')
    phone = phone.strip() if phone else ''
    password = data.get('password') or ''

    if not email or not phone or not password:
        return jsonify({'success': False, 'error': 'All fields are required.'}), 400

    global USERS_DB
    USERS_DB = load_users()
    existing_user = next((u for u in USERS_DB if u['email'] == email or u['phone'] == phone), None)
    if existing_user:
        return jsonify({'success': False, 'error': 'Email or Phone number already registered.'}), 400

    USERS_DB.append({"email": email, "phone": phone, "password": password})
    save_users(USERS_DB)
    
    return jsonify({'success': True, 'message': 'Account saved successfully! You can now log in.'})

@app.route('/forgot-password', methods=['POST'])
def forgot_password():
    data = request.get_json() or {}
    identifier = data.get('identifier')
    identifier = identifier.strip() if identifier else ''
    
    global USERS_DB
    USERS_DB = load_users()
    user = next((u for u in USERS_DB if u['email'] == identifier or u['phone'] == identifier), None)
    if user:
        return jsonify({'success': True, 'message': f"Account found! Your password is: {user['password']}"})
    else:
        return jsonify({'success': False, 'error': 'No account found with this email or phone number.'}), 400

@app.route('/logout')
def logout():
    session.pop('authenticated', None)
    session.pop('user', None)
    return redirect(url_for('login'))

@app.route('/control', methods=['POST'])
def control():
    if not session.get('authenticated'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        
    data = request.get_json() or {}
    command = data.get('command')
    
    global door_status
    if command in ['LOCK', 'UNLOCK']:
        if arduino and arduino.is_open:
            arduino.write((command + '\n').encode('utf-8'))
            print(f"Sent command to Arduino: {command}")
        else:
            door_status = "OPEN" if command == "UNLOCK" else "CLOSED"
            print(f"Simulation Mode: Door set to {door_status}")
            
        return jsonify({'success': True, 'command': command})
    
    return jsonify({'success': False, 'error': 'Invalid command'}), 400

@app.route('/status')
def get_status():
    if not session.get('authenticated'):
        return jsonify({'status': 'UNAUTHORIZED'}), 401
    return jsonify({'status': door_status})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)