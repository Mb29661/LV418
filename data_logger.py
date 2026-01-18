#!/usr/bin/env python3
"""
Perifal Data Logger - Logs pump data to SQLite database
Run this continuously to collect historical data
"""

import sqlite3
import time
import os
from datetime import datetime
from dotenv import load_dotenv
from perifal_client import PerifalClient

load_dotenv()

DB_PATH = os.path.join(os.path.dirname(__file__), 'perifal_history.db')

# Parameters to log
LOG_PARAMS = [
    "Power", "Mode", "ModeState",
    "T01", "T02", "T03", "T04", "T05", "T06", "T08", "T10", "T11", "T12", "T15",
    "T33", "T34", "T35", "T36", "T37", "T38", "T39",
    "R01", "M1 Hot Water Target", "M1 Heating Target",
    "compensate_offset", "compensate_slope",
    "hanControl", "Fault1",
    "SG Status",
]

def init_db():
    """Create database tables if they don't exist"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Main data table
    c.execute('''
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            power TEXT,
            mode TEXT,
            mode_state TEXT,
            t01_return REAL,
            t02_flow REAL,
            t03_evaporator REAL,
            t04_outdoor REAL,
            t05 REAL,
            t06 REAL,
            t08 REAL,
            t10 REAL,
            t11_hotwater REAL,
            t12_compressor REAL,
            t15 REAL,
            t33_comp_freq REAL,
            t34_runtime REAL,
            t35_pressure_lp REAL,
            t36_pressure_hp REAL,
            t37 REAL,
            t38 REAL,
            t39_power_kw REAL,
            r01_hw_target REAL,
            m1_hw_target REAL,
            m1_heat_target REAL,
            curve_offset REAL,
            curve_slope REAL,
            silent_mode TEXT,
            fault1 TEXT,
            sg_status TEXT,
            cop_calculated REAL,
            heat_power_kw REAL,
            delta_t REAL
        )
    ''')

    # Events table (pump start/stop, mode changes, etc)
    c.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            event_type TEXT,
            description TEXT,
            value_before TEXT,
            value_after TEXT
        )
    ''')

    # Create index for faster time-based queries
    c.execute('CREATE INDEX IF NOT EXISTS idx_readings_timestamp ON readings(timestamp)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)')

    conn.commit()
    conn.close()
    print(f"Database initialized: {DB_PATH}")

def calculate_cop(data, flow_rate=25):
    """Calculate COP from data"""
    try:
        power_in = float(data.get('T39', 0) or 0)
        flow_temp = float(data.get('T02', 0) or 0)
        return_temp = float(data.get('T01', 0) or 0)
        delta_t = flow_temp - return_temp

        heat_power = (flow_rate * delta_t * 4.186) / 60

        cop = 0
        if power_in > 0.1:
            cop = heat_power / power_in

        return cop, heat_power, delta_t
    except:
        return 0, 0, 0

def log_reading(data):
    """Log a single reading to database"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    cop, heat_power, delta_t = calculate_cop(data)

    c.execute('''
        INSERT INTO readings (
            power, mode, mode_state,
            t01_return, t02_flow, t03_evaporator, t04_outdoor,
            t05, t06, t08, t10, t11_hotwater, t12_compressor, t15,
            t33_comp_freq, t34_runtime, t35_pressure_lp, t36_pressure_hp,
            t37, t38, t39_power_kw,
            r01_hw_target, m1_hw_target, m1_heat_target,
            curve_offset, curve_slope,
            silent_mode, fault1, sg_status,
            cop_calculated, heat_power_kw, delta_t
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data.get('Power'),
        data.get('Mode'),
        data.get('ModeState'),
        float(data.get('T01', 0) or 0),
        float(data.get('T02', 0) or 0),
        float(data.get('T03', 0) or 0),
        float(data.get('T04', 0) or 0),
        float(data.get('T05', 0) or 0),
        float(data.get('T06', 0) or 0),
        float(data.get('T08', 0) or 0),
        float(data.get('T10', 0) or 0),
        float(data.get('T11', 0) or 0),
        float(data.get('T12', 0) or 0),
        float(data.get('T15', 0) or 0),
        float(data.get('T33', 0) or 0),
        float(data.get('T34', 0) or 0),
        float(data.get('T35', 0) or 0),
        float(data.get('T36', 0) or 0),
        float(data.get('T37', 0) or 0),
        float(data.get('T38', 0) or 0),
        float(data.get('T39', 0) or 0),
        float(data.get('R01', 0) or 0),
        float(data.get('M1 Hot Water Target', 0) or 0),
        float(data.get('M1 Heating Target', 0) or 0),
        float(data.get('compensate_offset', 0) or 0),
        float(data.get('compensate_slope', 0) or 0),
        data.get('hanControl'),
        data.get('Fault1'),
        data.get('SG Status'),
        cop,
        heat_power,
        delta_t
    ))

    conn.commit()
    conn.close()

def log_event(event_type, description, value_before=None, value_after=None):
    """Log an event (state change)"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO events (event_type, description, value_before, value_after)
        VALUES (?, ?, ?, ?)
    ''', (event_type, description, value_before, value_after))
    conn.commit()
    conn.close()

def run_logger(interval=30):
    """Main logger loop"""
    import sys
    print(f"Starting Perifal Data Logger (interval: {interval}s)", flush=True)
    print(f"Database: {DB_PATH}", flush=True)
    print("-" * 50, flush=True)

    init_db()

    username = os.getenv("PERIFAL_USERNAME")
    password = os.getenv("PERIFAL_PASSWORD")
    device_code = os.getenv("PERIFAL_DEVICE_CODE", "A09A520276BA")

    if not username or not password:
        print("Error: Set PERIFAL_USERNAME and PERIFAL_PASSWORD in .env", flush=True)
        return

    client = PerifalClient(username, password)
    print("Logging in...", flush=True)
    if not client.login():
        print("Login failed!", flush=True)
        return
    print("Login OK", flush=True)

    last_power = None
    last_mode = None
    count = 0

    while True:
        try:
            data = client.get_all_parameters(device_code, LOG_PARAMS)

            if data:
                # Log reading
                log_reading(data)
                count += 1

                # Check for state changes
                current_power = data.get('Power')
                current_mode = data.get('Mode')

                if last_power is not None and current_power != last_power:
                    event_desc = "Pump ON" if current_power == '1' else "Pump OFF"
                    log_event('power_change', event_desc, last_power, current_power)
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] EVENT: {event_desc}", flush=True)

                if last_mode is not None and current_mode != last_mode:
                    modes = {'1': 'VÄRME', '2': 'KYLA', '3': 'VV'}
                    event_desc = f"Mode: {modes.get(last_mode, last_mode)} → {modes.get(current_mode, current_mode)}"
                    log_event('mode_change', event_desc, last_mode, current_mode)
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] EVENT: {event_desc}", flush=True)

                last_power = current_power
                last_mode = current_mode

                # Print status every reading
                cop, heat_power, delta_t = calculate_cop(data)
                t04 = data.get('T04', '?')
                t06 = data.get('T06', '?')
                t39 = data.get('T39', '0')
                print(f"[{datetime.now().strftime('%H:%M:%S')}] #{count} Ute:{t04}° Tank:{t06}° El:{t39}kW COP:{cop:.1f}", flush=True)

        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Error: {e}", flush=True)
            # Re-login on error
            try:
                client.login()
            except:
                pass

        time.sleep(interval)

if __name__ == '__main__':
    import sys
    interval = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    run_logger(interval)
