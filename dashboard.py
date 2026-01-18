#!/usr/bin/env python3
"""
Perifal LV-418 Advanced Web Dashboard
With COP calculation, energy stats, and historical data
Supports SQLite (local) and PostgreSQL (Railway)
"""

from flask import Flask, render_template_string, jsonify, request, redirect, url_for, session, flash
import os
import threading
import time
import secrets
import hashlib
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from functools import wraps
from dotenv import load_dotenv
from perifal_client import PerifalClient

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))

# Email configuration
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "martin@strandholm.com")
APP_URL = os.getenv("APP_URL", "https://web-production-3bb0d.up.railway.app")

# Credentials
USERNAME = os.getenv("PERIFAL_USERNAME")
PASSWORD = os.getenv("PERIFAL_PASSWORD")
DEVICE_CODE = os.getenv("PERIFAL_DEVICE_CODE", "A09A520276BA")

# Database configuration - PostgreSQL on Railway, SQLite locally
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL:
    # Railway PostgreSQL
    import psycopg2
    from psycopg2.extras import RealDictCursor
    USE_POSTGRES = True
    # Fix Railway's postgres:// URL to postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
else:
    # Local SQLite
    import sqlite3
    USE_POSTGRES = False
    DB_PATH = os.path.join(os.path.dirname(__file__), 'perifal_history.db')

# All parameters to fetch
ALL_PARAMS = [
    "Power", "Mode", "ModeState",
    "T01", "T02", "T03", "T04", "T05", "T06", "T08", "T10", "T11", "T12", "T15",
    "T33", "T34", "T35", "T36", "T37", "T38", "T39", "T53",
    "R01", "M1 Hot Water Target", "M1 Heating Target",
    "compensate_offset", "compensate_slope",
    "M1 Max. Power",
    "hanControl", "Fault1", "Fault5", "Fault6",
    "app_heartbeat", "O15", "O17",
    "D12", "D14", "D15", "T38",
    "SG Status", "SG01",
    # Numeric codes for power/energy
    "2054",  # Electrical power (kW)
    # Curve points (AT compensation)
    "CP1-1", "CP1-2", "CP1-3", "CP1-4", "CP1-5", "CP1-6", "CP1-7",
    # Zone 2
    "Zone 2 Curve Offset", "Zone 2 Cure Slope", "Zone 2 Water Target",
]

# ============== Database Functions ==============

def get_db_connection():
    """Get database connection (PostgreSQL or SQLite)"""
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    """Initialize database tables"""
    conn = get_db_connection()
    cur = conn.cursor()

    if USE_POSTGRES:
        cur.execute('''
            CREATE TABLE IF NOT EXISTS readings (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP NOT NULL,
                t01_return REAL,
                t02_flow REAL,
                t04_outdoor REAL,
                t06_tank REAL,
                t12_compressor REAL,
                t33_comp_freq REAL,
                t39_power_kw REAL,
                d12_flow_rate REAL,
                cop_calculated REAL,
                heat_power_kw REAL,
                mode VARCHAR(10),
                UNIQUE(timestamp)
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email VARCHAR(255) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                name VARCHAR(255),
                email_verified BOOLEAN DEFAULT FALSE,
                admin_approved BOOLEAN DEFAULT FALSE,
                is_admin BOOLEAN DEFAULT FALSE,
                verification_token VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    else:
        cur.execute('''
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL UNIQUE,
                t01_return REAL,
                t02_flow REAL,
                t04_outdoor REAL,
                t06_tank REAL,
                t12_compressor REAL,
                t33_comp_freq REAL,
                t39_power_kw REAL,
                d12_flow_rate REAL,
                cop_calculated REAL,
                heat_power_kw REAL,
                mode TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                name TEXT,
                email_verified INTEGER DEFAULT 0,
                admin_approved INTEGER DEFAULT 0,
                is_admin INTEGER DEFAULT 0,
                verification_token TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')

    conn.commit()
    conn.close()
    print("Database initialized", flush=True)

# ============== User Authentication Functions ==============

def hash_password(password):
    """Hash a password with salt"""
    salt = secrets.token_hex(16)
    pwd_hash = hashlib.sha256((password + salt).encode()).hexdigest()
    return f"{salt}:{pwd_hash}"

def verify_password(password, stored_hash):
    """Verify a password against stored hash"""
    try:
        salt, pwd_hash = stored_hash.split(':')
        return hashlib.sha256((password + salt).encode()).hexdigest() == pwd_hash
    except:
        return False

def send_email(to_email, subject, html_body):
    """Send an email"""
    if not SMTP_USER or not SMTP_PASSWORD:
        print(f"Email not configured - would send to {to_email}: {subject}", flush=True)
        return False

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = SMTP_USER
        msg['To'] = to_email
        msg.attach(MIMEText(html_body, 'html'))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, to_email, msg.as_string())

        print(f"Email sent to {to_email}", flush=True)
        return True
    except Exception as e:
        print(f"Email error: {e}", flush=True)
        return False

def create_user(email, password, name):
    """Create a new user"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        password_hash = hash_password(password)
        verification_token = secrets.token_urlsafe(32)

        if USE_POSTGRES:
            cur.execute('''
                INSERT INTO users (email, password_hash, name, verification_token)
                VALUES (%s, %s, %s, %s) RETURNING id
            ''', (email.lower(), password_hash, name, verification_token))
            user_id = cur.fetchone()[0]
        else:
            cur.execute('''
                INSERT INTO users (email, password_hash, name, verification_token)
                VALUES (?, ?, ?, ?)
            ''', (email.lower(), password_hash, name, verification_token))
            user_id = cur.lastrowid

        conn.commit()
        conn.close()

        # Send verification email
        verify_url = f"{APP_URL}/verify/{verification_token}"
        send_email(email, "Verifiera din e-post - Perifal LV-418",
            f"""<h2>Välkommen till Perifal LV-418 Dashboard!</h2>
            <p>Klicka på länken nedan för att verifiera din e-postadress:</p>
            <p><a href="{verify_url}">{verify_url}</a></p>
            <p>Efter verifiering måste en administratör godkänna ditt konto.</p>""")

        # Notify admin
        send_email(ADMIN_EMAIL, "Ny användare väntar på godkännande - Perifal LV-418",
            f"""<h2>Ny registrering</h2>
            <p><strong>Namn:</strong> {name}</p>
            <p><strong>E-post:</strong> {email}</p>
            <p><a href="{APP_URL}/admin/approve/{user_id}">Klicka här för att godkänna</a></p>""")

        return user_id
    except Exception as e:
        print(f"Create user error: {e}", flush=True)
        return None

def get_user_by_email(email):
    """Get user by email"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        if USE_POSTGRES:
            cur.execute('SELECT * FROM users WHERE email = %s', (email.lower(),))
        else:
            cur.execute('SELECT * FROM users WHERE email = ?', (email.lower(),))

        row = cur.fetchone()
        conn.close()

        if row:
            if USE_POSTGRES:
                return dict(zip(['id', 'email', 'password_hash', 'name', 'email_verified',
                               'admin_approved', 'is_admin', 'verification_token', 'created_at'], row))
            else:
                return dict(row)
        return None
    except Exception as e:
        print(f"Get user error: {e}", flush=True)
        return None

def ensure_admin_exists():
    """Create initial admin user if no users exist"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        if USE_POSTGRES:
            cur.execute('SELECT COUNT(*) FROM users')
        else:
            cur.execute('SELECT COUNT(*) FROM users')

        count = cur.fetchone()[0]
        conn.close()

        if count == 0:
            # Create initial admin
            admin_email = os.getenv("ADMIN_EMAIL", "martin@strandholm.com")
            admin_password = os.getenv("ADMIN_PASSWORD", "admin123")  # Should be changed!

            conn = get_db_connection()
            cur = conn.cursor()

            password_hash = hash_password(admin_password)

            if USE_POSTGRES:
                cur.execute('''
                    INSERT INTO users (email, password_hash, name, email_verified, admin_approved, is_admin)
                    VALUES (%s, %s, %s, TRUE, TRUE, TRUE)
                ''', (admin_email.lower(), password_hash, 'Admin'))
            else:
                cur.execute('''
                    INSERT INTO users (email, password_hash, name, email_verified, admin_approved, is_admin)
                    VALUES (?, ?, ?, 1, 1, 1)
                ''', (admin_email.lower(), password_hash, 'Admin'))

            conn.commit()
            conn.close()
            print(f"Created initial admin user: {admin_email}", flush=True)
    except Exception as e:
        print(f"Ensure admin error: {e}", flush=True)

def login_required(f):
    """Decorator to require login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Decorator to require admin"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or not session.get('is_admin'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ============== Cloud Data Import ==============

def import_cloud_history():
    """Import historical data from cloud to fill database"""
    try:
        print("Importing cloud history to database...", flush=True)
        client = PerifalClient(USERNAME, PASSWORD)
        if not client.login():
            print("Failed to login for cloud import", flush=True)
            return 0

        end_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        start_time = (datetime.now() - timedelta(hours=72)).strftime('%Y-%m-%d %H:%M:%S')

        # Fetch cloud data
        flow_data = client.get_history(DEVICE_CODE, "2046", start_time, end_time, "day")
        tank_data = client.get_history(DEVICE_CODE, "2047", start_time, end_time, "day")
        outdoor_data = client.get_history(DEVICE_CODE, "2048", start_time, end_time, "day")
        power_data = client.get_history(DEVICE_CODE, "2054", start_time, end_time, "day")

        flow_values = flow_data.get('valueList', []) if isinstance(flow_data, dict) else []
        tank_values = tank_data.get('valueList', []) if isinstance(tank_data, dict) else []
        outdoor_values = outdoor_data.get('valueList', []) if isinstance(outdoor_data, dict) else []
        power_values = power_data.get('valueList', []) if isinstance(power_data, dict) else []

        flow_by_time = {v['dateTime']: float(v['addressValue']) for v in flow_values}
        tank_by_time = {v['dateTime']: float(v['addressValue']) for v in tank_values}
        outdoor_by_time = {v['dateTime']: float(v['addressValue']) for v in outdoor_values}
        power_by_time = {v['dateTime']: float(v['addressValue']) for v in power_values}

        all_times = set(flow_by_time.keys()) | set(tank_by_time.keys()) | set(outdoor_by_time.keys())

        conn = get_db_connection()
        cur = conn.cursor()
        imported = 0

        for dt_str in all_times:
            try:
                timestamp = datetime.strptime(dt_str, '%Y-%m-%d %H')
                t02 = flow_by_time.get(dt_str)
                t06 = tank_by_time.get(dt_str)
                t04 = outdoor_by_time.get(dt_str)
                t39 = power_by_time.get(dt_str)

                if USE_POSTGRES:
                    cur.execute('''
                        INSERT INTO readings (timestamp, t02_flow, t06_tank, t04_outdoor, t39_power_kw)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (timestamp) DO NOTHING
                    ''', (timestamp, t02, t06, t04, t39))
                else:
                    cur.execute('''
                        INSERT OR IGNORE INTO readings (timestamp, t02_flow, t06_tank, t04_outdoor, t39_power_kw)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (timestamp.isoformat(), t02, t06, t04, t39))

                imported += 1
            except:
                continue

        conn.commit()
        conn.close()
        print(f"Imported {imported} readings from cloud", flush=True)
        return imported
    except Exception as e:
        print(f"Cloud import error: {e}", flush=True)
        return 0

def log_reading(params):
    """Log a reading to the database"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Calculate COP - 2054 = power (kW), T39 = flow (m³/h)
        power_kw = float(params.get('2054', 0) or 0)  # 2054 = Electrical power kW
        t39 = float(params.get('T39', 0) or 0)  # Flow rate m³/h
        t02 = float(params.get('T02', 0) or 0)
        t01 = float(params.get('T01', 0) or 0)

        delta_t = t02 - t01
        flow_lmin = t39 * 1000 / 60  # m³/h to l/min
        heat_power = (flow_lmin * delta_t * 4.186) / 60 if flow_lmin > 0 else 0
        cop = min(heat_power / power_kw, 5.0) if power_kw > 0.1 else None  # Max COP 5.0

        timestamp = datetime.now().replace(minute=0, second=0, microsecond=0)

        if USE_POSTGRES:
            cur.execute('''
                INSERT INTO readings (timestamp, t01_return, t02_flow, t04_outdoor, t06_tank,
                    t12_compressor, t33_comp_freq, t39_power_kw, d12_flow_rate, cop_calculated, heat_power_kw, mode)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (timestamp) DO UPDATE SET
                    t01_return = EXCLUDED.t01_return,
                    t02_flow = EXCLUDED.t02_flow,
                    t04_outdoor = EXCLUDED.t04_outdoor,
                    t06_tank = EXCLUDED.t06_tank,
                    t12_compressor = EXCLUDED.t12_compressor,
                    t33_comp_freq = EXCLUDED.t33_comp_freq,
                    t39_power_kw = EXCLUDED.t39_power_kw,
                    d12_flow_rate = EXCLUDED.d12_flow_rate,
                    cop_calculated = EXCLUDED.cop_calculated,
                    heat_power_kw = EXCLUDED.heat_power_kw,
                    mode = EXCLUDED.mode
            ''', (
                timestamp,
                float(params.get('T01', 0) or 0),
                float(params.get('T02', 0) or 0),
                float(params.get('T04', 0) or 0),
                float(params.get('T06', 0) or 0),
                float(params.get('T12', 0) or 0),
                float(params.get('T33', 0) or 0),
                power_kw,  # Power kW (stored in t39_power_kw column)
                t39,  # Flow m³/h (stored in d12_flow_rate column)
                cop,
                heat_power,
                params.get('Mode', '')
            ))
        else:
            cur.execute('''
                INSERT OR REPLACE INTO readings (timestamp, t01_return, t02_flow, t04_outdoor, t06_tank,
                    t12_compressor, t33_comp_freq, t39_power_kw, d12_flow_rate, cop_calculated, heat_power_kw, mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                timestamp.isoformat(),
                float(params.get('T01', 0) or 0),
                float(params.get('T02', 0) or 0),
                float(params.get('T04', 0) or 0),
                float(params.get('T06', 0) or 0),
                float(params.get('T12', 0) or 0),
                float(params.get('T33', 0) or 0),
                power_kw,  # Power kW (stored in t39_power_kw column)
                t39,  # Flow m³/h (stored in d12_flow_rate column)
                cop,
                heat_power,
                params.get('Mode', '')
            ))

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error logging reading: {e}", flush=True)
        return False

def get_local_history(hours=72):
    """Get history from local database"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        if USE_POSTGRES:
            cur.execute('''
                SELECT timestamp, t01_return, t02_flow, t04_outdoor, t06_tank,
                       t39_power_kw, cop_calculated
                FROM readings
                WHERE timestamp > NOW() - INTERVAL '%s hours'
                ORDER BY timestamp ASC
            ''', (hours,))
        else:
            cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
            cur.execute('''
                SELECT timestamp, t01_return, t02_flow, t04_outdoor, t06_tank,
                       t39_power_kw, cop_calculated
                FROM readings
                WHERE timestamp > ?
                ORDER BY timestamp ASC
            ''', (cutoff,))

        rows = cur.fetchall()
        conn.close()

        readings = []
        for row in rows:
            if USE_POSTGRES:
                readings.append({
                    'timestamp': row[0].isoformat() if row[0] else None,
                    't01_return': row[1],
                    't02_flow': row[2],
                    't04_outdoor': row[3],
                    't06': row[4],
                    't39_power_kw': row[5],
                    'cop_calculated': row[6]
                })
            else:
                readings.append({
                    'timestamp': row['timestamp'],
                    't01_return': row['t01_return'],
                    't02_flow': row['t02_flow'],
                    't04_outdoor': row['t04_outdoor'],
                    't06': row['t06_tank'],
                    't39_power_kw': row['t39_power_kw'],
                    'cop_calculated': row['cop_calculated']
                })

        return readings
    except Exception as e:
        print(f"Error getting local history: {e}", flush=True)
        return []

def get_db_stats():
    """Get database statistics"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        if USE_POSTGRES:
            cur.execute('SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM readings')
        else:
            cur.execute('SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM readings')

        row = cur.fetchone()
        conn.close()

        return {
            'count': row[0] if row else 0,
            'oldest': row[1] if row else None,
            'newest': row[2] if row else None
        }
    except Exception as e:
        print(f"Error getting db stats: {e}", flush=True)
        return {'count': 0, 'oldest': None, 'newest': None}

# Background logging thread
logging_active = False

def background_logger():
    """Background thread that logs data every 10 minutes"""
    global logging_active
    print("Background logger started", flush=True)

    while logging_active:
        try:
            client = PerifalClient(USERNAME, PASSWORD)
            if client.login():
                params = client.get_all_parameters(DEVICE_CODE, ALL_PARAMS)
                if params:
                    if log_reading(params):
                        stats = get_db_stats()
                        print(f"Logged reading at {datetime.now().strftime('%H:%M')} - DB has {stats['count']} readings", flush=True)
        except Exception as e:
            print(f"Logger error: {e}", flush=True)

        # Sleep 10 minutes (check every 10 sec if we should stop)
        for _ in range(60):
            if not logging_active:
                break
            time.sleep(10)

    print("Background logger stopped", flush=True)

def start_logger():
    """Start the background logger"""
    global logging_active
    if not logging_active:
        logging_active = True
        thread = threading.Thread(target=background_logger, daemon=True)
        thread.start()

def stop_logger():
    """Stop the background logger"""
    global logging_active
    logging_active = False

# ============== End Database Functions ==============

# ============== Auth Templates ==============

LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Logga in - Perifal LV-418</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
               min-height: 100vh; display: flex; align-items: center; justify-content: center; }
        .container { background: #1e293b; padding: 40px; border-radius: 12px;
                     box-shadow: 0 4px 20px rgba(0,0,0,0.3); width: 100%; max-width: 400px; }
        h1 { color: #fff; text-align: center; margin-bottom: 30px; font-size: 24px; }
        .form-group { margin-bottom: 20px; }
        label { display: block; color: #94a3b8; margin-bottom: 8px; font-size: 14px; }
        input { width: 100%; padding: 12px; border: 1px solid #334155; border-radius: 6px;
                background: #0f172a; color: #fff; font-size: 16px; }
        input:focus { outline: none; border-color: #3b82f6; }
        button { width: 100%; padding: 14px; background: #3b82f6; color: #fff; border: none;
                 border-radius: 6px; font-size: 16px; cursor: pointer; margin-top: 10px; }
        button:hover { background: #2563eb; }
        .message { padding: 12px; border-radius: 6px; margin-bottom: 20px; text-align: center; }
        .error { background: #7f1d1d; color: #fca5a5; }
        .success { background: #14532d; color: #86efac; }
        .link { text-align: center; margin-top: 20px; }
        .link a { color: #3b82f6; text-decoration: none; }
        .link a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Perifal LV-418</h1>
        {% if error %}<div class="message error">{{ error }}</div>{% endif %}
        {% if success %}<div class="message success">{{ success }}</div>{% endif %}
        <form method="POST">
            <div class="form-group">
                <label>E-post</label>
                <input type="email" name="email" required>
            </div>
            <div class="form-group">
                <label>Lösenord</label>
                <input type="password" name="password" required>
            </div>
            <button type="submit">Logga in</button>
        </form>
        <div class="link"><a href="/register">Skapa konto</a></div>
    </div>
</body>
</html>
"""

REGISTER_TEMPLATE = """
<!DOCTYPE html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Registrera - Perifal LV-418</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
               min-height: 100vh; display: flex; align-items: center; justify-content: center; }
        .container { background: #1e293b; padding: 40px; border-radius: 12px;
                     box-shadow: 0 4px 20px rgba(0,0,0,0.3); width: 100%; max-width: 400px; }
        h1 { color: #fff; text-align: center; margin-bottom: 30px; font-size: 24px; }
        .form-group { margin-bottom: 20px; }
        label { display: block; color: #94a3b8; margin-bottom: 8px; font-size: 14px; }
        input { width: 100%; padding: 12px; border: 1px solid #334155; border-radius: 6px;
                background: #0f172a; color: #fff; font-size: 16px; }
        input:focus { outline: none; border-color: #3b82f6; }
        button { width: 100%; padding: 14px; background: #3b82f6; color: #fff; border: none;
                 border-radius: 6px; font-size: 16px; cursor: pointer; margin-top: 10px; }
        button:hover { background: #2563eb; }
        .message { padding: 12px; border-radius: 6px; margin-bottom: 20px; text-align: center; }
        .error { background: #7f1d1d; color: #fca5a5; }
        .success { background: #14532d; color: #86efac; }
        .link { text-align: center; margin-top: 20px; }
        .link a { color: #3b82f6; text-decoration: none; }
        .link a:hover { text-decoration: underline; }
        .info { color: #94a3b8; font-size: 13px; text-align: center; margin-top: 15px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Skapa konto</h1>
        {% if error %}<div class="message error">{{ error }}</div>{% endif %}
        {% if success %}<div class="message success">{{ success }}</div>{% endif %}
        <form method="POST">
            <div class="form-group">
                <label>Namn</label>
                <input type="text" name="name" required>
            </div>
            <div class="form-group">
                <label>E-post</label>
                <input type="email" name="email" required>
            </div>
            <div class="form-group">
                <label>Lösenord</label>
                <input type="password" name="password" required minlength="6">
            </div>
            <button type="submit">Registrera</button>
        </form>
        <div class="link"><a href="/login">Har redan konto? Logga in</a></div>
        <div class="info">Efter registrering behöver du verifiera din e-post och invänta admin-godkännande.</div>
    </div>
</body>
</html>
"""

MESSAGE_TEMPLATE = """
<!DOCTYPE html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }} - Perifal LV-418</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
               min-height: 100vh; display: flex; align-items: center; justify-content: center; }
        .container { background: #1e293b; padding: 40px; border-radius: 12px;
                     box-shadow: 0 4px 20px rgba(0,0,0,0.3); width: 100%; max-width: 400px; text-align: center; }
        h1 { color: #fff; margin-bottom: 20px; font-size: 24px; }
        .message { color: #94a3b8; font-size: 16px; line-height: 1.6; }
        .success { color: #86efac; }
        .error { color: #fca5a5; }
        .link { margin-top: 25px; }
        .link a { color: #3b82f6; text-decoration: none; }
    </style>
</head>
<body>
    <div class="container">
        <h1>{{ title }}</h1>
        <div class="message {{ msg_class }}">{{ message }}</div>
        <div class="link"><a href="/login">Gå till inloggning</a></div>
    </div>
</body>
</html>
"""

# ============== End Auth Templates ==============

SETTINGS_TEMPLATE = """
<!DOCTYPE html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Inställningar - Perifal LV-418</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #0f0f1a 0%, #1a1a2e 50%, #16213e 100%);
            color: #fff;
            min-height: 100vh;
            padding: 15px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            flex-wrap: wrap;
            gap: 10px;
        }
        h1 { font-size: 1.5em; }
        .badges { display: flex; gap: 8px; flex-wrap: wrap; }
        .badge {
            padding: 5px 12px;
            border-radius: 15px;
            font-size: 0.8em;
            font-weight: 500;
        }
        .card {
            background: rgba(255,255,255,0.08);
            border-radius: 12px;
            padding: 15px;
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.1);
            margin-bottom: 15px;
        }
        .card h2 {
            font-size: 1em;
            margin-bottom: 12px;
            color: #90caf9;
        }
        .controls {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 12px;
        }
        .control-item {
            background: rgba(0,0,0,0.2);
            padding: 10px;
            border-radius: 8px;
        }
        .control-item label {
            display: block;
            font-size: 0.75em;
            color: #90caf9;
            margin-bottom: 5px;
        }
        .control-row {
            display: flex;
            gap: 8px;
        }
        .control-row input, .control-row select {
            flex: 1;
            padding: 8px;
            border-radius: 5px;
            border: 1px solid rgba(255,255,255,0.2);
            background: rgba(0,0,0,0.3);
            color: #fff;
            font-size: 0.9em;
        }
        .control-row button {
            padding: 8px 15px;
            border-radius: 5px;
            border: none;
            background: #4caf50;
            color: white;
            cursor: pointer;
            font-size: 0.85em;
        }
        .control-row button:hover { background: #45a049; }
        .toggle {
            padding: 8px 20px;
            border-radius: 5px;
            border: none;
            font-weight: bold;
            cursor: pointer;
            width: 100%;
        }
        .toggle.active { background: #4caf50; color: white; }
        .toggle.inactive { background: #546e7a; color: white; }
        .message {
            position: fixed;
            bottom: 20px;
            right: 20px;
            padding: 12px 20px;
            border-radius: 8px;
            background: #4caf50;
            color: white;
            opacity: 0;
            transition: opacity 0.3s;
        }
        .message.show { opacity: 1; }
        .message.error { background: #ff5252; }
        .debug-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.85em;
        }
        .debug-table th, .debug-table td {
            padding: 8px 12px;
            border: 1px solid rgba(255,255,255,0.1);
            text-align: left;
        }
        .debug-table th {
            background: rgba(0,0,0,0.3);
            color: #90caf9;
        }
        .debug-table tr:nth-child(even) {
            background: rgba(0,0,0,0.15);
        }
        .debug-table td:first-child {
            font-family: monospace;
            color: #ffca28;
        }
        .search-box {
            padding: 10px;
            border-radius: 5px;
            border: 1px solid rgba(255,255,255,0.2);
            background: rgba(0,0,0,0.3);
            color: #fff;
            width: 100%;
            margin-bottom: 15px;
        }
        .status-info {
            font-size: 0.8em;
            color: #888;
            margin-top: 10px;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>⚙️ Inställningar - Perifal LV-418</h1>
            <div class="badges">
                <a href="/" class="badge" style="background:#7c4dff;text-decoration:none;cursor:pointer">← DASHBOARD</a>
            </div>
        </header>

        <!-- Controls -->
        <div class="card">
            <h2>🎛️ Styrning</h2>
            <p style="font-size:0.75em;color:#ff8a80;margin-bottom:15px;">⚠️ Kräver lösenord. Ändra endast om du vet vad du gör.</p>

            <h3 style="font-size:0.85em;color:#90caf9;margin:12px 0 10px 0;">Grundinställningar</h3>
            <div class="controls">
                <div class="control-item">
                    <label>Kurva offset</label>
                    <div class="control-row">
                        <input type="number" id="inputCurveOffset" step="0.5" min="15" max="60">
                        <button onclick="safeSetParam('compensate_offset', document.getElementById('inputCurveOffset').value)">OK</button>
                    </div>
                </div>
                <div class="control-item">
                    <label>Kurva lutning</label>
                    <div class="control-row">
                        <input type="number" id="inputCurveSlope" step="0.1" min="0" max="3.5">
                        <button onclick="safeSetParam('compensate_slope', document.getElementById('inputCurveSlope').value)">OK</button>
                    </div>
                </div>
                <div class="control-item">
                    <label>VV börvärde</label>
                    <div class="control-row">
                        <input type="number" id="inputHwTarget" step="1" min="30" max="58">
                        <button onclick="safeSetParam('R01', document.getElementById('inputHwTarget').value)">OK</button>
                    </div>
                </div>
                <div class="control-item">
                    <label>Tyst läge</label>
                    <button id="silentBtn" class="toggle inactive" onclick="safeToggleSilent()">AV</button>
                </div>
            </div>

            <h3 style="font-size:0.85em;color:#90caf9;margin:20px 0 10px 0;">Avancerat</h3>
            <div class="controls">
                <div class="control-item">
                    <label>Värme börvärde (M1)</label>
                    <div class="control-row">
                        <input type="number" id="inputM1HeatTarget" step="1" min="15" max="60">
                        <button onclick="safeSetParam('M1 Heating Target', document.getElementById('inputM1HeatTarget').value)">OK</button>
                    </div>
                </div>
                <div class="control-item">
                    <label>Max effekt %</label>
                    <div class="control-row">
                        <input type="number" id="inputMaxPower" step="5" min="0" max="100">
                        <button onclick="safeSetParam('M1 Max. Power', document.getElementById('inputMaxPower').value)">OK</button>
                    </div>
                </div>
                <div class="control-item">
                    <label>Pump PÅ/AV</label>
                    <button id="powerBtn" class="toggle active" onclick="safeTogglePower()">PÅ</button>
                </div>
                <div class="control-item">
                    <label>SG Ready läge</label>
                    <div class="control-row">
                        <select id="inputSG">
                            <option value="0">Av</option>
                            <option value="1">Normal</option>
                            <option value="2">Lågt elpris</option>
                            <option value="3">Överskott</option>
                        </select>
                        <button onclick="safeSetParam('SG Status', document.getElementById('inputSG').value)">OK</button>
                    </div>
                </div>
            </div>

            <h3 style="font-size:0.85em;color:#90caf9;margin:20px 0 10px 0;">Värmekurva (kurvpunkter)</h3>
            <div class="controls">
                <div class="control-item">
                    <label>CP1-1 (-20°C)</label>
                    <div class="control-row">
                        <input type="number" id="inputCP1_1" step="1">
                        <button onclick="safeSetParam('CP1-1', document.getElementById('inputCP1_1').value)">OK</button>
                    </div>
                </div>
                <div class="control-item">
                    <label>CP1-2 (-10°C)</label>
                    <div class="control-row">
                        <input type="number" id="inputCP1_2" step="1">
                        <button onclick="safeSetParam('CP1-2', document.getElementById('inputCP1_2').value)">OK</button>
                    </div>
                </div>
                <div class="control-item">
                    <label>CP1-3 (0°C)</label>
                    <div class="control-row">
                        <input type="number" id="inputCP1_3" step="1">
                        <button onclick="safeSetParam('CP1-3', document.getElementById('inputCP1_3').value)">OK</button>
                    </div>
                </div>
                <div class="control-item">
                    <label>CP1-4 (5°C)</label>
                    <div class="control-row">
                        <input type="number" id="inputCP1_4" step="1">
                        <button onclick="safeSetParam('CP1-4', document.getElementById('inputCP1_4').value)">OK</button>
                    </div>
                </div>
                <div class="control-item">
                    <label>CP1-5 (10°C)</label>
                    <div class="control-row">
                        <input type="number" id="inputCP1_5" step="1">
                        <button onclick="safeSetParam('CP1-5', document.getElementById('inputCP1_5').value)">OK</button>
                    </div>
                </div>
                <div class="control-item">
                    <label>CP1-6 (15°C)</label>
                    <div class="control-row">
                        <input type="number" id="inputCP1_6" step="1">
                        <button onclick="safeSetParam('CP1-6', document.getElementById('inputCP1_6').value)">OK</button>
                    </div>
                </div>
                <div class="control-item">
                    <label>CP1-7 (20°C)</label>
                    <div class="control-row">
                        <input type="number" id="inputCP1_7" step="1">
                        <button onclick="safeSetParam('CP1-7', document.getElementById('inputCP1_7').value)">OK</button>
                    </div>
                </div>
            </div>

            <h3 style="font-size:0.85em;color:#90caf9;margin:20px 0 10px 0;">Zone 2</h3>
            <div class="controls">
                <div class="control-item">
                    <label>Zone 2 Offset</label>
                    <div class="control-row">
                        <input type="number" id="inputZ2Offset" step="0.5">
                        <button onclick="safeSetParam('Zone 2 Curve Offset', document.getElementById('inputZ2Offset').value)">OK</button>
                    </div>
                </div>
                <div class="control-item">
                    <label>Zone 2 Slope</label>
                    <div class="control-row">
                        <input type="number" id="inputZ2Slope" step="0.1">
                        <button onclick="safeSetParam('Zone 2 Cure Slope', document.getElementById('inputZ2Slope').value)">OK</button>
                    </div>
                </div>
            </div>
        </div>

        <!-- Debug Parameters -->
        <div class="card">
            <h2>🔧 Debug - Alla parametrar</h2>
            <input type="text" class="search-box" id="paramSearch" placeholder="Sök parameter..." oninput="filterParams()">
            <div style="max-height:500px;overflow-y:auto;">
                <table class="debug-table">
                    <thead>
                        <tr>
                            <th>Parameter</th>
                            <th>Värde</th>
                        </tr>
                    </thead>
                    <tbody id="paramTableBody">
                        <tr><td colspan="2">Laddar...</td></tr>
                    </tbody>
                </table>
            </div>
            <div class="status-info">
                Senast uppdaterad: <span id="lastUpdate">--</span>
            </div>
        </div>
    </div>

    <div id="message" class="message"></div>

    <script>
        const CONTROL_PASSWORD = 'Mb29661';
        let passwordVerified = false;
        let passwordTimeout = null;
        let silentMode = false;
        let pumpPower = true;
        let allParams = {};

        function showMessage(text, isError = false) {
            const msg = document.getElementById('message');
            msg.textContent = text;
            msg.className = 'message show' + (isError ? ' error' : '');
            setTimeout(() => msg.className = 'message', 3000);
        }

        function verifyPassword() {
            const pwd = prompt('⚠️ VARNING: Du är på väg att ändra pumpen.\\n\\nAnge lösenord för att bekräfta:');
            if (pwd === CONTROL_PASSWORD) {
                passwordVerified = true;
                if (passwordTimeout) clearTimeout(passwordTimeout);
                passwordTimeout = setTimeout(() => { passwordVerified = false; }, 5 * 60 * 1000);
                return true;
            } else if (pwd !== null) {
                showMessage('Fel lösenord!', true);
            }
            return false;
        }

        async function safeSetParam(code, value) {
            if (!passwordVerified && !verifyPassword()) return;
            await setParam(code, value);
        }

        function safeToggleSilent() {
            if (!passwordVerified && !verifyPassword()) return;
            setParam('hanControl', silentMode ? '0000000000000000' : '0000000000000010');
        }

        function safeTogglePower() {
            if (!passwordVerified && !verifyPassword()) return;
            const newState = pumpPower ? '0' : '1';
            if (!pumpPower || confirm('Är du säker på att du vill STÄNGA AV pumpen?')) {
                setParam('Power', newState);
            }
        }

        async function setParam(code, value) {
            try {
                const res = await fetch('/api/control', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({code, value: String(value)})
                });
                const data = await res.json();
                if (data.success) {
                    showMessage('Sparat: ' + code + ' = ' + value);
                    fetchStatus();
                } else {
                    showMessage('Fel', true);
                }
            } catch (e) {
                showMessage('Anslutningsfel', true);
            }
        }

        function filterParams() {
            const search = document.getElementById('paramSearch').value.toLowerCase();
            const rows = document.querySelectorAll('#paramTableBody tr');
            rows.forEach(row => {
                const text = row.textContent.toLowerCase();
                row.style.display = text.includes(search) ? '' : 'none';
            });
        }

        async function fetchStatus() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();
                allParams = data;

                // Update controls with current values
                document.getElementById('inputCurveOffset').value = data.compensate_offset || '';
                document.getElementById('inputCurveSlope').value = data.compensate_slope || '';
                document.getElementById('inputHwTarget').value = data.R01 || '';
                document.getElementById('inputM1HeatTarget').value = data['M1 Heating Target'] || '';
                document.getElementById('inputMaxPower').value = data['M1 Max. Power'] || '';
                document.getElementById('inputCP1_1').value = data['CP1-1'] || '';
                document.getElementById('inputCP1_2').value = data['CP1-2'] || '';
                document.getElementById('inputCP1_3').value = data['CP1-3'] || '';
                document.getElementById('inputCP1_4').value = data['CP1-4'] || '';
                document.getElementById('inputCP1_5').value = data['CP1-5'] || '';
                document.getElementById('inputCP1_6').value = data['CP1-6'] || '';
                document.getElementById('inputCP1_7').value = data['CP1-7'] || '';
                document.getElementById('inputZ2Offset').value = data['Zone 2 Curve Offset'] || '';
                document.getElementById('inputZ2Slope').value = data['Zone 2 Cure Slope'] || '';
                document.getElementById('inputSG').value = data['SG Status'] || '0';

                // Silent mode
                silentMode = data.hanControl === '0000000000000010';
                const silentBtn = document.getElementById('silentBtn');
                silentBtn.textContent = silentMode ? 'PÅ' : 'AV';
                silentBtn.className = 'toggle ' + (silentMode ? 'active' : 'inactive');

                // Power state
                pumpPower = data.Power === '1';
                const powerBtn = document.getElementById('powerBtn');
                powerBtn.textContent = pumpPower ? 'PÅ' : 'AV';
                powerBtn.className = 'toggle ' + (pumpPower ? 'active' : 'inactive');

                // Debug table
                const tbody = document.getElementById('paramTableBody');
                const sortedKeys = Object.keys(data).sort();
                tbody.innerHTML = sortedKeys.map(key =>
                    `<tr><td>${key}</td><td>${data[key]}</td></tr>`
                ).join('');

                document.getElementById('lastUpdate').textContent = new Date().toLocaleTimeString();

                // Re-apply filter
                filterParams();
            } catch (e) {
                console.error('Fetch error:', e);
            }
        }

        // Initial load and refresh every 30 seconds
        fetchStatus();
        setInterval(fetchStatus, 30000);
    </script>
</body>
</html>
"""

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Perifal LV-418 Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns"></script>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #0f0f1a 0%, #1a1a2e 50%, #16213e 100%);
            color: #fff;
            min-height: 100vh;
            padding: 15px;
        }
        .container { max-width: 1400px; margin: 0 auto; }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            flex-wrap: wrap;
            gap: 10px;
        }
        h1 { font-size: 1.5em; }
        .badges { display: flex; gap: 8px; flex-wrap: wrap; }
        .badge {
            padding: 5px 12px;
            border-radius: 15px;
            font-size: 0.8em;
            font-weight: 500;
        }
        .badge.online { background: #00c853; }
        .badge.offline { background: #ff5252; }
        .badge.heating { background: #ff7043; }
        .badge.hotwater { background: #ffca28; color: #000; }
        .badge.idle { background: #546e7a; }
        .badge.silent { background: #9c27b0; }
        .badge.compressor { background: #2196f3; }
        .badge.wood { background: #8d6e63; }
        .badge.stopped { background: #ff5252; }

        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 15px;
            margin-bottom: 15px;
        }
        .grid-wide {
            grid-template-columns: 1fr;
        }

        .card {
            background: rgba(255,255,255,0.08);
            border-radius: 12px;
            padding: 15px;
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.1);
        }
        .card h2 {
            font-size: 0.9em;
            margin-bottom: 12px;
            color: #90caf9;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        /* COP Card */
        .cop-display { text-align: center; padding: 15px 0; }
        .cop-value {
            font-size: 3.5em;
            font-weight: bold;
            background: linear-gradient(135deg, #4caf50, #8bc34a);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        .cop-label { color: #90caf9; margin-top: 5px; font-size: 0.85em; }
        .cop-details {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 8px;
            margin-top: 12px;
            font-size: 0.8em;
        }
        .cop-detail {
            background: rgba(0,0,0,0.2);
            padding: 8px;
            border-radius: 6px;
            text-align: center;
        }
        .cop-detail-value { font-size: 1.3em; font-weight: bold; }
        .cop-detail-label { color: #90caf9; font-size: 0.75em; }

        /* Temperature grid */
        .temps {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 8px;
        }
        .temp-item {
            text-align: center;
            padding: 10px 6px;
            background: rgba(0,0,0,0.2);
            border-radius: 6px;
        }
        .temp-value { font-size: 1.4em; font-weight: bold; }
        .temp-label { font-size: 0.65em; color: #90caf9; margin-top: 2px; }
        .temp-outdoor .temp-value { color: #64b5f6; }
        .temp-flow .temp-value { color: #ef5350; }
        .temp-return .temp-value { color: #ff7043; }
        .temp-hw .temp-value { color: #ffca28; }
        .temp-tank .temp-value { color: #ce93d8; }
        .temp-evap .temp-value { color: #4dd0e1; }
        .temp-comp .temp-value { color: #ff5722; }

        /* Stats */
        .stats {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 8px;
        }
        .stat-item {
            background: rgba(0,0,0,0.2);
            padding: 10px;
            border-radius: 6px;
            text-align: center;
        }
        .stat-value { font-size: 1.3em; font-weight: bold; }
        .stat-label { font-size: 0.7em; color: #90caf9; }

        /* Controls */
        .controls {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 8px;
            max-width: 100%;
            overflow: hidden;
        }
        .control-item {
            background: rgba(0,0,0,0.2);
            border-radius: 6px;
            padding: 10px;
            min-width: 0;
        }
        .control-item input, .control-item select {
            max-width: 80px;
        }
        .control-item label {
            display: block;
            font-size: 0.7em;
            color: #90caf9;
            margin-bottom: 5px;
        }
        .control-row { display: flex; gap: 6px; }
        input[type="number"], input[type="date"], select {
            flex: 1;
            padding: 6px;
            border: none;
            border-radius: 5px;
            background: rgba(255,255,255,0.9);
            font-size: 0.9em;
        }
        button {
            padding: 6px 12px;
            border: none;
            border-radius: 5px;
            background: #2196f3;
            color: white;
            cursor: pointer;
            font-size: 0.85em;
        }
        button:hover { background: #1976d2; }
        button.toggle { width: 100%; padding: 10px; }
        button.toggle.active { background: #00c853; }
        button.toggle.inactive { background: #546e7a; }

        /* Chart */
        .chart-controls {
            display: flex;
            gap: 10px;
            margin-bottom: 10px;
            flex-wrap: wrap;
            align-items: center;
        }
        .chart-controls label { font-size: 0.8em; color: #90caf9; }
        .chart-controls select, .chart-controls input {
            padding: 5px 10px;
            border-radius: 5px;
            border: none;
            font-size: 0.85em;
        }
        .chart-container {
            position: relative;
            height: 300px;
        }

        /* Status indicator */
        .pump-status {
            text-align: center;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 15px;
        }
        .pump-status.running { background: rgba(0,200,83,0.2); border: 1px solid #00c853; }
        .pump-status.stopped { background: rgba(255,82,82,0.2); border: 1px solid #ff5252; }
        .pump-status.wood { background: rgba(141,110,99,0.3); border: 1px solid #8d6e63; }
        .pump-status h3 { font-size: 1.1em; margin-bottom: 5px; }
        .pump-status p { font-size: 0.85em; color: #aaa; }

        .update-info {
            text-align: center;
            color: #546e7a;
            font-size: 0.7em;
            margin-top: 15px;
        }

        .message {
            position: fixed;
            bottom: 20px;
            left: 50%;
            transform: translateX(-50%);
            padding: 10px 20px;
            border-radius: 6px;
            background: #00c853;
            color: white;
            display: none;
            z-index: 100;
            font-size: 0.9em;
        }
        .message.error { background: #ff5252; }
        .message.show { display: block; }

        @media (max-width: 600px) {
            .temps { grid-template-columns: repeat(2, 1fr); }
            .cop-value { font-size: 2.5em; }
            .chart-container { height: 250px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🔥 Perifal LV-418</h1>
            <div class="badges">
                <span id="statusBadge" class="badge online">ONLINE</span>
                <span id="modeBadge" class="badge heating">VÄRME</span>
                <span id="compBadge" class="badge compressor" style="display:none">KOMPRESSOR</span>
                <span id="woodBadge" class="badge wood" style="display:none">VEDELDNING</span>
                <span id="silentBadge" class="badge silent" style="display:none">TYST</span>
                <a href="/settings" target="_blank" class="badge" style="background:#7c4dff;text-decoration:none;cursor:pointer">INSTÄLLNINGAR</a>
                <a href="/logout" class="badge" style="background:#546e7a;text-decoration:none;cursor:pointer">LOGGA UT</a>
            </div>
        </header>

        <!-- Pump status indicator -->
        <div id="pumpStatus" class="pump-status running">
            <h3 id="pumpStatusTitle">🟢 Värmepump aktiv</h3>
            <p id="pumpStatusDesc">Kompressorn körs</p>
        </div>

        <div class="grid">
            <!-- COP Card -->
            <div class="card">
                <h2>⚡ Effektivitet</h2>
                <div class="cop-display">
                    <div class="cop-value" id="copValue">--</div>
                    <div class="cop-label">COP (Coefficient of Performance)</div>
                </div>
                <div class="cop-details">
                    <div class="cop-detail">
                        <div class="cop-detail-value" id="powerIn">--</div>
                        <div class="cop-detail-label">El in (kW)</div>
                    </div>
                    <div class="cop-detail">
                        <div class="cop-detail-value" id="powerOut">--</div>
                        <div class="cop-detail-label">Värme ut (kW)</div>
                    </div>
                    <div class="cop-detail">
                        <div class="cop-detail-value" id="deltaT">--</div>
                        <div class="cop-detail-label">ΔT (°C)</div>
                    </div>
                    <div class="cop-detail">
                        <div class="cop-detail-value" id="compFreq">--</div>
                        <div class="cop-detail-label">Kompressor %</div>
                    </div>
                </div>
            </div>

            <!-- Temperatures & Stats -->
            <div class="card">
                <h2>🌡️ Temperaturer & Driftdata</h2>
                <div class="temps">
                    <div class="temp-item temp-outdoor">
                        <div class="temp-value" id="tempOutdoor">--</div>
                        <div class="temp-label">Utomhus</div>
                    </div>
                    <div class="temp-item temp-flow">
                        <div class="temp-value" id="tempFlow">--</div>
                        <div class="temp-label">Utgående</div>
                    </div>
                    <div class="temp-item temp-return">
                        <div class="temp-value" id="tempIngåenden">--</div>
                        <div class="temp-label">Ingående</div>
                    </div>
                    <div class="temp-item temp-tank">
                        <div class="temp-value" id="tempTank">--</div>
                        <div class="temp-label">Ackumulatortank</div>
                    </div>
                    <div class="temp-item temp-comp">
                        <div class="temp-value" id="tempComp">--</div>
                        <div class="temp-label">Kompressor</div>
                    </div>
                    <div class="temp-item temp-evap">
                        <div class="temp-value" id="tempEvap">--</div>
                        <div class="temp-label">Förångare</div>
                    </div>
                </div>
                <div class="stats" style="grid-template-columns: repeat(2, 1fr); margin-top: 15px;">
                    <div class="stat-item">
                        <div class="stat-value" id="pumpActive" style="color:#4caf50;">--</div>
                        <div class="stat-label">Pump</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value" id="calcTarget">--</div>
                        <div class="stat-label">Börvärde</div>
                    </div>
                </div>
                <div class="stats" style="grid-template-columns: 1fr; margin-top: 10px;">
                    <div class="stat-item" style="background: rgba(141, 110, 99, 0.3);">
                        <div class="stat-value" style="color:#8d6e63;"><span id="woodHours">--</span> / <span id="woodSessions">--</span></div>
                        <div class="stat-label">🪵 Eldning senaste 7 dygn (timmar / tillfällen)</div>
                    </div>
                </div>
            </div>

        </div>

        <!-- Historical Chart -->
        <div class="card">
            <h2>📈 Historik</h2>
            <div class="chart-controls">
                <label>Visa:</label>
                <select id="historyRange" onchange="loadHistory()">
                    <option value="1">Senaste timmen</option>
                    <option value="6">6 timmar</option>
                    <option value="24" selected>24 timmar</option>
                    <option value="72">3 dagar</option>
                    <option value="168">7 dagar</option>
                    <option value="custom">Anpassat...</option>
                </select>
                <div id="customDateRange" style="display:none;">
                    <input type="date" id="dateFrom">
                    <span>till</span>
                    <input type="date" id="dateTo">
                    <button onclick="loadHistory()">Hämta</button>
                </div>
                <label style="margin-left:15px;">Visa:</label>
                <select id="chartDataset" onchange="updateChartVisibility()">
                    <option value="all">Alla</option>
                    <option value="temps">Temperaturer</option>
                    <option value="cop">COP & Effekt</option>
                </select>
            </div>
            <div class="chart-container">
                <canvas id="historyChart"></canvas>
            </div>
        </div>

        <!-- Energy Statistics -->
        <div class="card">
            <h2>⚡ Elförbrukning</h2>
            <div class="stats" style="grid-template-columns: repeat(4, 1fr);">
                <div class="stat-item">
                    <div class="stat-value" id="energyToday">--</div>
                    <div class="stat-label">Idag (kWh)</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value" id="energy24h">--</div>
                    <div class="stat-label">24h (kWh)</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value" id="energy7d">--</div>
                    <div class="stat-label">7 dagar (kWh)</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value" id="energyTotal">--</div>
                    <div class="stat-label">Totalt (kWh)</div>
                </div>
            </div>
            <div class="chart-container" style="height:200px;margin-top:15px;">
                <canvas id="energyChart"></canvas>
            </div>
        </div>

        <div class="update-info">
            Live-data var 10s | Historik från molnet | Senast: <span id="lastUpdate">--</span>
            <br><span id="dbStatus" style="color:#666">Moln: kontrollerar...</span>
        </div>
    </div>

    <div id="message" class="message"></div>

    <script>
        let sessionStart = Date.now();
        let totalEnergy = 0;
        let totalHeat = 0;
        let copReadings = [];
        let silentMode = false;
        let pumpPower = true;
        let chart = null;
        let energyChart = null;

        function showMessage(text, isError = false) {
            const msg = document.getElementById('message');
            msg.textContent = text;
            msg.className = 'message show' + (isError ? ' error' : '');
            setTimeout(() => msg.className = 'message', 3000);
        }

        function calculateCOP(data) {
            const powerIn = parseFloat(data['2054']) || 0;  // 2054 = Electrical power (kW)
            const flowTemp = parseFloat(data.T02) || 0;
            const returnTemp = parseFloat(data.T01) || 0;
            const deltaT = flowTemp - returnTemp;
            // T39 is flow rate in m³/h, convert to l/min
            const flowM3h = parseFloat(data.T39) || 0;
            const flowLmin = flowM3h * 1000 / 60;  // Convert m³/h to l/min
            // Heat power: Q = flow (l/min) * deltaT (°C) * 4.186 (kJ/kg·K) / 60 (s/min) = kW
            const heatPower = (flowLmin * deltaT * 4.186) / 60;
            let cop = 0;
            if (powerIn > 0.1) cop = Math.min(heatPower / powerIn, 5.0);  // Max COP 5.0
            return { cop, powerIn, heatPower, deltaT, flowLmin };
        }

        function initChart() {
            const ctx = document.getElementById('historyChart').getContext('2d');
            chart = new Chart(ctx, {
                type: 'line',
                data: {
                    datasets: [
                        { label: 'Utgående', data: [], borderColor: '#ef5350', borderWidth: 1.5, pointRadius: 0, tension: 0.3 },
                        { label: 'Ingående', data: [], borderColor: '#ff7043', borderWidth: 1.5, pointRadius: 0, tension: 0.3 },
                        { label: 'Utomhus', data: [], borderColor: '#64b5f6', borderWidth: 1.5, pointRadius: 0, tension: 0.3 },
                        { label: 'Tank', data: [], borderColor: '#ce93d8', borderWidth: 1.5, pointRadius: 0, tension: 0.3 },
                        { label: 'COP', data: [], borderColor: '#4caf50', borderWidth: 2, pointRadius: 0, tension: 0.3, yAxisID: 'cop' },
                        { label: 'El kW', data: [], borderColor: '#ffca28', borderWidth: 1.5, pointRadius: 0, tension: 0.3, yAxisID: 'power' },
                        {
                            label: '🪵 Vedeldning',
                            data: [],
                            borderColor: '#8d6e63',
                            backgroundColor: 'rgba(141, 110, 99, 0.3)',
                            borderWidth: 0,
                            pointRadius: 0,
                            fill: true,
                            tension: 0.3
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { intersect: false, mode: 'index' },
                    plugins: {
                        legend: { labels: { color: '#90caf9', boxWidth: 12, font: { size: 10 } } },
                        tooltip: { callbacks: { label: ctx => ctx.dataset.label + ': ' + (ctx.parsed.y?.toFixed(1) || '--') } }
                    },
                    scales: {
                        x: {
                            type: 'time',
                            reverse: true,
                            time: {
                                displayFormats: {
                                    hour: 'HH:mm',
                                    day: 'dd MMM'
                                }
                            },
                            ticks: {
                                color: '#666',
                                maxTicksLimit: 12,
                                callback: function(value, index, ticks) {
                                    const date = new Date(value);
                                    const hours = date.getHours();
                                    // Show date at midnight or first tick of a new day
                                    if (hours === 0 || index === 0) {
                                        return date.toLocaleDateString('sv-SE', { day: 'numeric', month: 'short' }) + ' ' + date.toLocaleTimeString('sv-SE', { hour: '2-digit', minute: '2-digit' });
                                    }
                                    return date.toLocaleTimeString('sv-SE', { hour: '2-digit', minute: '2-digit' });
                                }
                            },
                            grid: {
                                color: function(context) {
                                    // Darker line at midnight
                                    if (context.tick && new Date(context.tick.value).getHours() === 0) {
                                        return 'rgba(255,255,255,0.2)';
                                    }
                                    return 'rgba(255,255,255,0.05)';
                                }
                            }
                        },
                        y: {
                            position: 'left',
                            ticks: { color: '#666' },
                            grid: { color: 'rgba(255,255,255,0.05)' },
                            title: { display: true, text: '°C', color: '#666' }
                        },
                        cop: {
                            position: 'right',
                            ticks: { color: '#4caf50' },
                            grid: { display: false },
                            title: { display: true, text: 'COP', color: '#4caf50' },
                            min: 0, max: 8
                        },
                        power: {
                            position: 'right',
                            ticks: { color: '#ffca28' },
                            grid: { display: false },
                            title: { display: true, text: 'kW', color: '#ffca28' },
                            min: 0
                        }
                    }
                }
            });
        }

        async function loadHistory() {
            const range = document.getElementById('historyRange').value;
            const customDiv = document.getElementById('customDateRange');

            if (range === 'custom') {
                customDiv.style.display = 'flex';
                const from = document.getElementById('dateFrom').value;
                const to = document.getElementById('dateTo').value;
                if (!from || !to) return;
            } else {
                customDiv.style.display = 'none';
            }

            let url = '/api/history?hours=' + range;

            if (range === 'custom') {
                const from = document.getElementById('dateFrom').value;
                const to = document.getElementById('dateTo').value;
                url = '/api/history?from=' + from + '&to=' + to;
            }

            try {
                const res = await fetch(url);
                const data = await res.json();

                if (data.readings && data.readings.length > 0) {
                    const source = data.source === 'cloud' ? 'Moln' : 'Databas';
                    document.getElementById('dbStatus').textContent = source + ': ' + data.readings.length + ' mätpunkter';

                    // Detect wood heating periods for chart (same time span as other data)
                    const WOOD_THRESHOLD = 7;
                    let woodHeatingPeriods = [];
                    data.readings.forEach(r => {
                        const tankTemp = r.t06;
                        const flowTemp = r.t02_flow;
                        const timestamp = new Date(r.timestamp);
                        const isWoodHeating = tankTemp !== null && flowTemp !== null && tankTemp > flowTemp + WOOD_THRESHOLD;
                        if (isWoodHeating) {
                            woodHeatingPeriods.push({x: timestamp, y: tankTemp});
                        } else {
                            woodHeatingPeriods.push({x: timestamp, y: null});
                        }
                    });

                    // Update chart datasets
                    chart.data.datasets[0].data = data.readings
                        .filter(r => r.t02_flow !== null)
                        .map(r => ({x: new Date(r.timestamp), y: r.t02_flow}));
                    chart.data.datasets[1].data = data.readings
                        .filter(r => r.t01_return !== null)
                        .map(r => ({x: new Date(r.timestamp), y: r.t01_return}));
                    chart.data.datasets[2].data = data.readings
                        .filter(r => r.t04_outdoor !== null)
                        .map(r => ({x: new Date(r.timestamp), y: r.t04_outdoor}));
                    chart.data.datasets[3].data = data.readings
                        .filter(r => r.t06 !== null)
                        .map(r => ({x: new Date(r.timestamp), y: r.t06}));
                    chart.data.datasets[4].data = data.readings
                        .filter(r => r.cop_calculated !== null && r.cop_calculated <= 5.0)
                        .map(r => ({x: new Date(r.timestamp), y: Math.min(r.cop_calculated, 5.0)}));
                    chart.data.datasets[5].data = data.readings
                        .filter(r => r.t39_power_kw !== null)
                        .map(r => ({x: new Date(r.timestamp), y: r.t39_power_kw}));
                    chart.data.datasets[6].data = woodHeatingPeriods;

                    chart.update();
                } else {
                    document.getElementById('dbStatus').textContent = 'Moln: ingen historik tillgänglig';
                }
            } catch (e) {
                console.error('History fetch error:', e);
                document.getElementById('dbStatus').textContent = 'Moln: fel vid hämtning';
            }
        }

        function updateChartVisibility() {
            const mode = document.getElementById('chartDataset').value;
            if (mode === 'temps') {
                chart.data.datasets[0].hidden = false;  // Utgående
                chart.data.datasets[1].hidden = false;  // Ingående
                chart.data.datasets[2].hidden = false;  // Utomhus
                chart.data.datasets[3].hidden = false;  // Tank
                chart.data.datasets[4].hidden = true;   // COP
                chart.data.datasets[5].hidden = true;   // El kW
                chart.data.datasets[6].hidden = false;  // Vedeldning
            } else if (mode === 'cop') {
                chart.data.datasets[0].hidden = true;
                chart.data.datasets[1].hidden = true;
                chart.data.datasets[2].hidden = true;
                chart.data.datasets[3].hidden = true;
                chart.data.datasets[4].hidden = false;
                chart.data.datasets[5].hidden = false;
                chart.data.datasets[6].hidden = true;
            } else {
                chart.data.datasets.forEach(ds => ds.hidden = false);
            }
            chart.update();
        }

        async function fetchStatus() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();

                // Temperatures
                const t04 = parseFloat(data.T04) || 0;
                const t02 = parseFloat(data.T02) || 0;
                const t01 = parseFloat(data.T01) || 0;
                const t11 = parseFloat(data.T11) || 0;
                const t08 = parseFloat(data.T08) || 0;  // Tank temp
                const t03 = parseFloat(data.T03) || 0;
                const powerKW = parseFloat(data['2054']) || 0;  // 2054 = Electrical power kW
                const t33 = parseFloat(data.T33) || 0;
                const heatTarget = parseFloat(data['M1 Heating Target']) || 0;

                document.getElementById('tempOutdoor').textContent = t04.toFixed(1) + '°';
                document.getElementById('tempFlow').textContent = t02.toFixed(1) + '°';
                document.getElementById('tempIngåenden').textContent = t01.toFixed(1) + '°';
                document.getElementById('tempTank').textContent = t08.toFixed(1) + '°';
                document.getElementById('tempComp').textContent = (parseFloat(data.T12) || 0).toFixed(1) + '°';
                document.getElementById('tempEvap').textContent = t03.toFixed(1) + '°';

                // Driftdata - interpolate target from curve points
                const outdoorTemps = [-20, -10, 0, 5, 10, 15, 20];
                const curveTemps = [
                    parseFloat(data['CP1-1']) || 0,
                    parseFloat(data['CP1-2']) || 0,
                    parseFloat(data['CP1-3']) || 0,
                    parseFloat(data['CP1-4']) || 0,
                    parseFloat(data['CP1-5']) || 0,
                    parseFloat(data['CP1-6']) || 0,
                    parseFloat(data['CP1-7']) || 0
                ];

                // Interpolate target temp based on current outdoor temp
                let calcTarget = curveTemps[0];
                for (let i = 0; i < outdoorTemps.length - 1; i++) {
                    if (t04 >= outdoorTemps[i] && t04 <= outdoorTemps[i + 1]) {
                        const ratio = (t04 - outdoorTemps[i]) / (outdoorTemps[i + 1] - outdoorTemps[i]);
                        calcTarget = curveTemps[i] + ratio * (curveTemps[i + 1] - curveTemps[i]);
                        break;
                    } else if (t04 < outdoorTemps[0]) {
                        calcTarget = curveTemps[0];
                    } else if (t04 > outdoorTemps[outdoorTemps.length - 1]) {
                        calcTarget = curveTemps[curveTemps.length - 1];
                    }
                }

                document.getElementById('calcTarget').textContent = calcTarget.toFixed(1) + '°C';

                // COP
                const copData = calculateCOP(data);
                document.getElementById('copValue').textContent = copData.cop > 0 ? copData.cop.toFixed(2) : '--';
                document.getElementById('powerIn').textContent = copData.powerIn.toFixed(2);
                document.getElementById('powerOut').textContent = copData.heatPower > 0 ? copData.heatPower.toFixed(1) : '--';
                document.getElementById('deltaT').textContent = copData.deltaT.toFixed(1);
                document.getElementById('compFreq').textContent = t33.toFixed(0);

                // Pump active status
                const pumpActiveEl = document.getElementById('pumpActive');
                if (t33 > 5 || powerKW > 0.2) {
                    pumpActiveEl.textContent = 'AKTIV';
                    pumpActiveEl.style.color = '#4caf50';
                } else {
                    pumpActiveEl.textContent = 'VILAR';
                    pumpActiveEl.style.color = '#ff9800';
                }

                // Pump status - detect if pump is stopped due to high tank temp (wood heating)
                const pumpStatus = document.getElementById('pumpStatus');
                const statusTitle = document.getElementById('pumpStatusTitle');
                const statusDesc = document.getElementById('pumpStatusDesc');
                const woodBadge = document.getElementById('woodBadge');
                const compBadge = document.getElementById('compBadge');

                const compRunning = powerKW > 0.2 || t33 > 10;
                const tankAboveTarget = t08 > heatTarget || t01 > heatTarget;

                if (!compRunning && tankAboveTarget) {
                    // Wood heating detected - tank temp above target
                    pumpStatus.className = 'pump-status wood';
                    statusTitle.textContent = '🪵 Vedeldning detekterad';
                    statusDesc.textContent = 'Tank: ' + t08.toFixed(1) + '°C > Börvärde: ' + heatTarget.toFixed(0) + '°C - Pumpen vilar';
                    woodBadge.style.display = 'inline-block';
                    compBadge.style.display = 'none';
                } else if (compRunning) {
                    pumpStatus.className = 'pump-status running';
                    statusTitle.textContent = '🟢 Värmepump aktiv';
                    statusDesc.textContent = 'Kompressor: ' + t33.toFixed(0) + '% | Effekt: ' + powerKW.toFixed(2) + ' kW';
                    woodBadge.style.display = 'none';
                    compBadge.style.display = 'inline-block';
                } else {
                    pumpStatus.className = 'pump-status stopped';
                    statusTitle.textContent = '🔴 Värmepump stannad';
                    statusDesc.textContent = 'Kompressorn är av';
                    woodBadge.style.display = 'none';
                    compBadge.style.display = 'none';
                }

                // Mode badge
                const modeBadge = document.getElementById('modeBadge');
                const mode = data.Mode;
                modeBadge.textContent = mode === '1' ? 'VÄRME' : mode === '2' ? 'KYLA' : mode === '3' ? 'VV' : 'LÄGE ' + mode;
                modeBadge.className = 'badge ' + (mode === '1' ? 'heating' : mode === '3' ? 'hotwater' : 'idle');

                // Silent mode badge
                silentMode = data.hanControl && data.hanControl.includes('1');
                document.getElementById('silentBadge').style.display = silentMode ? 'inline-block' : 'none';

                // Power state
                pumpPower = data.Power === '1';

                document.getElementById('lastUpdate').textContent = new Date().toLocaleTimeString('sv-SE');

            } catch (e) {
                console.error('Fetch error:', e);
            }
        }

        const CONTROL_PASSWORD = 'Mb29661';  // Same as login password
        let passwordVerified = false;
        let passwordTimeout = null;

        function verifyPassword() {
            const pwd = prompt('⚠️ VARNING: Du är på väg att ändra pumpen.\\n\\nAnge lösenord för att bekräfta:');
            if (pwd === CONTROL_PASSWORD) {
                passwordVerified = true;
                // Password valid for 5 minutes
                if (passwordTimeout) clearTimeout(passwordTimeout);
                passwordTimeout = setTimeout(() => { passwordVerified = false; }, 5 * 60 * 1000);
                return true;
            } else if (pwd !== null) {
                showMessage('Fel lösenord!', true);
            }
            return false;
        }

        async function safeSetParam(code, value) {
            if (!passwordVerified && !verifyPassword()) return;
            await setParam(code, value);
        }

        function safeToggleSilent() {
            if (!passwordVerified && !verifyPassword()) return;
            setParam('hanControl', silentMode ? '0000000000000000' : '0000000000000010');
        }

        function safeTogglePower() {
            if (!passwordVerified && !verifyPassword()) return;
            const newState = pumpPower ? '0' : '1';
            if (!pumpPower || confirm('Är du säker på att du vill STÄNGA AV pumpen?')) {
                setParam('Power', newState);
            }
        }

        async function setParam(code, value) {
            try {
                const res = await fetch('/api/control', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({code, value: String(value)})
                });
                const data = await res.json();
                if (data.success) {
                    showMessage('Sparat: ' + code + ' = ' + value);
                    fetchStatus();
                } else {
                    showMessage('Fel', true);
                }
            } catch (e) {
                showMessage('Anslutningsfel', true);
            }
        }

        function toggleSilent() {
            setParam('hanControl', silentMode ? '0000000000000000' : '0000000000000010');
        }

        function toggleControls() {
            const panel = document.getElementById('controlsPanel');
            const arrow = document.getElementById('controlsArrow');
            if (panel.style.display === 'none') {
                panel.style.display = 'block';
                arrow.textContent = '▼';
            } else {
                panel.style.display = 'none';
                arrow.textContent = '▶';
            }
        }

        function initEnergyChart() {
            const ctx = document.getElementById('energyChart').getContext('2d');
            energyChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    datasets: [{
                        label: 'kWh',
                        data: [],
                        backgroundColor: 'rgba(255, 193, 7, 0.7)',
                        borderColor: '#ffc107',
                        borderWidth: 1
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false }
                    },
                    scales: {
                        x: {
                            type: 'time',
                            reverse: true,
                            time: { unit: 'day', displayFormats: { day: 'dd MMM', hour: 'HH:mm' } },
                            ticks: { color: '#666' },
                            grid: { color: 'rgba(255,255,255,0.05)' }
                        },
                        y: {
                            ticks: { color: '#666' },
                            grid: { color: 'rgba(255,255,255,0.05)' },
                            title: { display: true, text: 'kWh', color: '#666' }
                        }
                    }
                }
            });
        }

        async function loadEnergy() {
            try {
                const res = await fetch('/api/energy?hours=72');
                const data = await res.json();

                // Update stats
                document.getElementById('energyToday').textContent = (data.today_kwh || 0).toFixed(1);
                document.getElementById('energy24h').textContent = (data.last_24h_kwh || 0).toFixed(1);
                document.getElementById('energy7d').textContent = (data.total_kwh || 0).toFixed(1);
                document.getElementById('energyTotal').textContent = (data.total_kwh || 0).toFixed(1);

                if (data.readings && data.readings.length > 0) {
                    // Aggregate by day
                    const dailyData = {};
                    data.readings.forEach(r => {
                        const day = r.timestamp.split('T')[0];
                        if (!dailyData[day]) dailyData[day] = 0;
                        dailyData[day] += r.kwh;
                    });

                    const chartData = Object.entries(dailyData)
                        .map(([day, kwh]) => ({
                            x: new Date(day),
                            y: parseFloat(kwh.toFixed(2))
                        }))
                        .sort((a, b) => a.x - b.x);

                    energyChart.data.datasets[0].data = chartData;
                    energyChart.update();
                }
            } catch (e) {
                console.error('Energy fetch error:', e);
            }
        }

        // Load wood heating stats (7 days / 168h)
        async function loadWoodStats() {
            try {
                const res = await fetch('/api/history?hours=168');
                const data = await res.json();
                if (data.readings && data.readings.length > 0) {
                    let woodHeatingHours = 0;
                    let woodHeatingSessions = 0;
                    let prevWoodHeating = null;

                    const WOOD_THRESHOLD = 7;  // Tank must be 7°C warmer than flow
                    data.readings.forEach(r => {
                        const tankTemp = r.t06;
                        const flowTemp = r.t02_flow;
                        const isWoodHeating = tankTemp !== null && flowTemp !== null && tankTemp > flowTemp + WOOD_THRESHOLD;

                        if (isWoodHeating) {
                            woodHeatingHours++;
                            if (prevWoodHeating === false || prevWoodHeating === null) {
                                woodHeatingSessions++;
                            }
                        }
                        prevWoodHeating = isWoodHeating;
                    });

                    document.getElementById('woodHours').textContent = woodHeatingHours;
                    document.getElementById('woodSessions').textContent = woodHeatingSessions;
                }
            } catch (e) {
                console.error('Wood stats error:', e);
            }
        }

        // Initialize
        initChart();
        initEnergyChart();
        fetchStatus();
        loadHistory();
        loadEnergy();
        loadWoodStats();
        setInterval(fetchStatus, 10000);
        setInterval(loadHistory, 60000); // Refresh history every minute
        setInterval(loadEnergy, 300000); // Refresh energy every 5 minutes
        setInterval(loadWoodStats, 300000); // Refresh wood stats every 5 minutes
    </script>
</body>
</html>
"""

def get_client():
    client = PerifalClient(USERNAME, PASSWORD)
    client.login()
    return client

# ============== Auth Routes ==============

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        user = get_user_by_email(email)
        if not user:
            return render_template_string(LOGIN_TEMPLATE, error="Felaktig e-post eller lösenord")

        if not verify_password(password, user['password_hash']):
            return render_template_string(LOGIN_TEMPLATE, error="Felaktig e-post eller lösenord")

        if not user['email_verified']:
            return render_template_string(LOGIN_TEMPLATE, error="Du behöver verifiera din e-postadress först")

        if not user['admin_approved']:
            return render_template_string(LOGIN_TEMPLATE, error="Ditt konto väntar på admin-godkännande")

        # Login successful
        session['user_id'] = user['id']
        session['user_email'] = user['email']
        session['user_name'] = user['name']
        session['is_admin'] = bool(user['is_admin'])
        return redirect(url_for('index'))

    return render_template_string(LOGIN_TEMPLATE)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        if not name or not email or not password:
            return render_template_string(REGISTER_TEMPLATE, error="Alla fält måste fyllas i")

        if len(password) < 6:
            return render_template_string(REGISTER_TEMPLATE, error="Lösenordet måste vara minst 6 tecken")

        # Check if user exists
        existing = get_user_by_email(email)
        if existing:
            return render_template_string(REGISTER_TEMPLATE, error="E-postadressen är redan registrerad")

        # Create user
        user_id = create_user(email, password, name)
        if user_id:
            return render_template_string(REGISTER_TEMPLATE,
                success="Konto skapat! Kolla din e-post för verifieringslänk.")
        else:
            return render_template_string(REGISTER_TEMPLATE, error="Kunde inte skapa konto, försök igen")

    return render_template_string(REGISTER_TEMPLATE)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/verify/<token>')
def verify_email(token):
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        if USE_POSTGRES:
            cur.execute('SELECT id, email FROM users WHERE verification_token = %s', (token,))
        else:
            cur.execute('SELECT id, email FROM users WHERE verification_token = ?', (token,))

        row = cur.fetchone()
        if not row:
            conn.close()
            return render_template_string(MESSAGE_TEMPLATE,
                title="Ogiltig länk", message="Verifieringslänken är ogiltig eller har redan använts.",
                msg_class="error")

        if USE_POSTGRES:
            cur.execute('UPDATE users SET email_verified = TRUE, verification_token = NULL WHERE id = %s', (row[0],))
        else:
            cur.execute('UPDATE users SET email_verified = 1, verification_token = NULL WHERE id = ?', (row[0],))

        conn.commit()
        conn.close()

        return render_template_string(MESSAGE_TEMPLATE,
            title="E-post verifierad!", message="Din e-postadress är nu verifierad. En administratör kommer granska och godkänna ditt konto.",
            msg_class="success")
    except Exception as e:
        print(f"Verify error: {e}", flush=True)
        return render_template_string(MESSAGE_TEMPLATE,
            title="Fel", message="Ett fel uppstod vid verifiering.",
            msg_class="error")

@app.route('/admin/approve/<int:user_id>')
def admin_approve(user_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        if USE_POSTGRES:
            cur.execute('SELECT email, name, email_verified FROM users WHERE id = %s', (user_id,))
        else:
            cur.execute('SELECT email, name, email_verified FROM users WHERE id = ?', (user_id,))

        row = cur.fetchone()
        if not row:
            conn.close()
            return render_template_string(MESSAGE_TEMPLATE,
                title="Användare finns inte", message="Användaren kunde inte hittas.",
                msg_class="error")

        if USE_POSTGRES:
            cur.execute('UPDATE users SET admin_approved = TRUE WHERE id = %s', (user_id,))
        else:
            cur.execute('UPDATE users SET admin_approved = 1 WHERE id = ?', (user_id,))

        conn.commit()
        conn.close()

        email = row[0] if USE_POSTGRES else row['email']
        name = row[1] if USE_POSTGRES else row['name']

        # Notify user
        send_email(email, "Ditt konto har godkänts - Perifal LV-418",
            f"""<h2>Välkommen {name}!</h2>
            <p>Ditt konto har nu godkänts av en administratör.</p>
            <p><a href="{APP_URL}/login">Klicka här för att logga in</a></p>""")

        return render_template_string(MESSAGE_TEMPLATE,
            title="Användare godkänd", message=f"{name} ({email}) har nu godkänts och kan logga in.",
            msg_class="success")
    except Exception as e:
        print(f"Approve error: {e}", flush=True)
        return render_template_string(MESSAGE_TEMPLATE,
            title="Fel", message="Ett fel uppstod vid godkännande.",
            msg_class="error")

@app.route('/api/import-cloud')
@admin_required
def api_import_cloud():
    """Import cloud history to database (admin only)"""
    imported = import_cloud_history()
    return jsonify({'imported': imported})

# ============== End Auth Routes ==============

@app.route('/')
@login_required
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/settings')
@login_required
def settings():
    return render_template_string(SETTINGS_TEMPLATE)

@app.route('/api/status')
def api_status():
    client = get_client()
    params = client.get_all_parameters(DEVICE_CODE, ALL_PARAMS)
    return jsonify(params)

@app.route('/api/history')
def api_history():
    """Fetch history from cloud API (no local logging needed)"""
    hours = request.args.get('hours', 24, type=int)
    date_from = request.args.get('from')
    date_to = request.args.get('to')

    try:
        client = get_client()

        # Calculate time range
        if date_from and date_to:
            start_time = date_from + " 00:00:00"
            end_time = date_to + " 23:59:59"
        else:
            end_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            start_time = (datetime.now() - timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')

        # Determine frequency based on time range
        # Cloud API supports: "day" (hourly points), "week", "month", "year"
        if hours <= 72:
            frequency = "day"
        elif hours <= 168:
            frequency = "week"
        else:
            frequency = "month"

        # Fetch all data series from cloud
        # 2046 = Outlet/Flow temp (T02)
        # 2047 = Tank temp (T06)
        # 2048 = Outdoor temp (T04)
        # 2054 = Power In (kW)
        flow_data = client.get_history(DEVICE_CODE, "2046", start_time, end_time, frequency)
        tank_data = client.get_history(DEVICE_CODE, "2047", start_time, end_time, frequency)
        outdoor_data = client.get_history(DEVICE_CODE, "2048", start_time, end_time, frequency)
        power_data = client.get_history(DEVICE_CODE, "2054", start_time, end_time, frequency)

        # Combine data into unified format
        readings = []

        # Get value lists
        flow_values = flow_data.get('valueList', []) if isinstance(flow_data, dict) else []
        tank_values = tank_data.get('valueList', []) if isinstance(tank_data, dict) else []
        outdoor_values = outdoor_data.get('valueList', []) if isinstance(outdoor_data, dict) else []
        power_values = power_data.get('valueList', []) if isinstance(power_data, dict) else []

        # Create lookup dicts by datetime
        flow_by_time = {v['dateTime']: float(v['addressValue']) for v in flow_values}
        tank_by_time = {v['dateTime']: float(v['addressValue']) for v in tank_values}
        outdoor_by_time = {v['dateTime']: float(v['addressValue']) for v in outdoor_values}
        power_by_time = {v['dateTime']: float(v['addressValue']) for v in power_values}

        # Get all unique timestamps and sort chronologically (oldest first)
        all_times = set(flow_by_time.keys()) | set(tank_by_time.keys()) | set(outdoor_by_time.keys()) | set(power_by_time.keys())

        for dt in sorted(all_times):
            # Parse datetime - cloud format is "YYYY-MM-DD HH" (hourly)
            try:
                timestamp = datetime.strptime(dt, '%Y-%m-%d %H')
            except:
                continue

            flow_temp = flow_by_time.get(dt)
            power_kw = power_by_time.get(dt)

            # Calculate COP if we have flow temp and power
            # Note: We don't have return temp from cloud, so we estimate deltaT as ~2°C
            cop = None
            if flow_temp and power_kw and power_kw > 0.1:
                # Estimate: assume deltaT ~2°C and flow ~50 l/min
                estimated_heat = (50 * 2 * 4.186) / 60  # ~7 kW at full flow
                cop = estimated_heat / power_kw if power_kw > 0 else None

            readings.append({
                'timestamp': timestamp.isoformat(),
                't02_flow': flow_temp,
                't06': tank_by_time.get(dt),
                't04_outdoor': outdoor_by_time.get(dt),
                't01_return': None,  # Not available from cloud
                'cop_calculated': cop,
                't39_power_kw': power_kw
            })

        return jsonify({
            'readings': readings,
            'source': 'cloud',
            'hours_requested': hours,
            'start_time': start_time,
            'end_time': end_time,
            'frequency': frequency,
            'flow_title': flow_data.get('title') if isinstance(flow_data, dict) else None,
            'tank_title': tank_data.get('title') if isinstance(tank_data, dict) else None,
            'outdoor_title': outdoor_data.get('title') if isinstance(outdoor_data, dict) else None
        })

    except Exception as e:
        import traceback
        return jsonify({'readings': [], 'error': str(e), 'traceback': traceback.format_exc(), 'source': 'cloud'})

@app.route('/api/energy')
def api_energy():
    """Fetch energy consumption history from cloud"""
    hours = request.args.get('hours', 168, type=int)  # Default 7 days

    try:
        client = get_client()

        end_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        start_time = (datetime.now() - timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')

        # Always use "day" frequency for consistent hourly data format
        frequency = "day"

        # 2054 = Power In (Total)
        power_data = client.get_history(DEVICE_CODE, "2054", start_time, end_time, frequency)

        readings = []
        total_kwh = 0
        today_kwh = 0
        last_24h_kwh = 0

        today = datetime.now().strftime('%Y-%m-%d')
        now = datetime.now()

        if isinstance(power_data, dict):
            values = power_data.get('valueList', [])
            for v in values:
                dt_str = v.get('dateTime', '')
                kwh = float(v.get('addressValue', 0) or 0)
                total_kwh += kwh

                # Parse date - try multiple formats
                dt = None
                for fmt in ['%Y-%m-%d %H', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M']:
                    try:
                        dt = datetime.strptime(dt_str, fmt)
                        break
                    except:
                        continue

                if dt:
                    readings.append({
                        'timestamp': dt.isoformat(),
                        'kwh': kwh
                    })

                    # Today's consumption
                    if dt.date() == now.date():
                        today_kwh += kwh

                    # Last 24 hours
                    if dt >= now - timedelta(hours=24):
                        last_24h_kwh += kwh

        return jsonify({
            'readings': readings,
            'total_kwh': round(total_kwh, 2),
            'today_kwh': round(today_kwh, 2),
            'last_24h_kwh': round(last_24h_kwh, 2),
            'hours': hours,
            'points': len(readings)
        })

    except Exception as e:
        import traceback
        return jsonify({'readings': [], 'error': str(e), 'traceback': traceback.format_exc()})

@app.route('/api/events')
def api_events():
    hours = request.args.get('hours', 24, type=int)

    try:
        conn = get_db()
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        c.execute('''
            SELECT * FROM events
            WHERE timestamp > datetime('now', '-' || ? || ' hours')
            ORDER BY timestamp DESC
            LIMIT 100
        ''', (hours,))

        rows = c.fetchall()
        conn.close()

        return jsonify({'events': [dict(row) for row in rows]})

    except Exception as e:
        return jsonify({'events': [], 'error': str(e)})

@app.route('/api/control', methods=['POST'])
def api_control():
    data = request.json
    code = data.get('code')
    value = data.get('value')

    if not code or value is None:
        return jsonify({'success': False, 'error': 'Missing code or value'})

    client = get_client()
    success = client.control(DEVICE_CODE, code, value)
    return jsonify({'success': success})

@app.route('/api/db-stats')
def api_db_stats():
    """Get database statistics"""
    stats = get_db_stats()
    return jsonify(stats)

@app.route('/api/local-history')
def api_local_history():
    """Get history from local database"""
    hours = request.args.get('hours', 168, type=int)
    readings = get_local_history(hours)
    stats = get_db_stats()
    return jsonify({
        'readings': readings,
        'source': 'local_db',
        'db_stats': stats
    })

# Initialize on module load (for gunicorn)
init_db()
ensure_admin_exists()
start_logger()

if __name__ == '__main__':
    db_type = "PostgreSQL" if USE_POSTGRES else "SQLite"
    stats = get_db_stats()

    print("\n" + "="*50)
    print("  Perifal LV-418 Advanced Dashboard")
    print("  http://localhost:5051")
    print("")
    print(f"  Databas: {db_type}")
    print(f"  Lagrade mätpunkter: {stats['count']}")
    print("  Loggning var 10:e minut aktiverad")
    print("="*50 + "\n")

    port = int(os.getenv("PORT", 5051))
    app.run(host='0.0.0.0', port=port, debug=False)
