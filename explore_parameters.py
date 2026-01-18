#!/usr/bin/env python3
"""
Explore all controllable parameters
"""

import os
from dotenv import load_dotenv
from perifal_client import PerifalClient

# All parameters from the API that have rangeStart/rangeEnd (controllable)
CONTROL_PARAMS = [
    # Power & Mode
    ("Power", "Av/På", "0-1"),
    ("Mode", "Driftläge", "?"),

    # Heating curve
    ("compensate_offset", "Värmekurva offset", "15-60"),
    ("compensate_slope", "Värmekurva lutning", "0-3.5"),

    # Hot water
    ("R01", "VV börvärde", "30-58"),
    ("M1 Hot Water Target", "VV börvärde M1", "30-58"),

    # Heating targets
    ("M1 Heating Target", "Värme börvärde M1", "15-60"),
    ("M2 Heating Target", "Värme börvärde M2", "15-60"),
    ("R02", "Värme börvärde R02", "15-60"),

    # Cooling
    ("M1 Cooling Target", "Kyla börvärde M1", "12-28"),
    ("R03", "Kyla börvärde R03", "12-28"),

    # Max power limits
    ("M1 Max. Power", "Max effekt M1", "0-99.9"),

    # Zone 2
    ("Zone 2 Water Target", "Zon 2 vattentemp", "?"),
    ("Zone 2 Curve Offset", "Zon 2 kurva offset", "15-40"),
    ("Zone 2 Cure Slope", "Zon 2 kurva lutning", "0-4"),

    # Silent/Mute mode
    ("hanControl", "Tyst läge (bitmask)", "bits"),
    ("Timer_Mute_On_En", "Tyst timer på", "0-1"),
    ("Timer_Mute_Off_En", "Tyst timer av", "0-1"),
    ("TimerMuteOnHour", "Tyst på timme", "0-23"),
    ("TimerMuteOnMinute", "Tyst på minut", "0-59"),
    ("TimerMuteOffHour", "Tyst av timme", "0-23"),
    ("TimerMuteOffMinute", "Tyst av minut", "0-59"),

    # SG Ready (Smart Grid)
    ("SG Status", "SG Ready status", "0-5"),
    ("SG02", "SG parameter", "0-120"),

    # Anti-legionella
    ("G01", "Legionella temp", "60-70"),
    ("G04", "Legionella intervall", "1-30"),
    ("G05", "Legionella aktiv", "0-1"),

    # Defrost
    ("D01", "Avfrostning starttemp", "-37-45"),
    ("D02", "Avfrostning tid", "0-120"),

    # Curve points (CP1 = heating curve points at different outdoor temps)
    ("CP1-1", "Kurvpunkt -20°C ute", "15-60"),
    ("CP1-2", "Kurvpunkt -10°C ute", "15-60"),
    ("CP1-3", "Kurvpunkt 0°C ute", "15-60"),
    ("CP1-5", "Kurvpunkt +10°C ute", "15-60"),
    ("CP1-6", "Kurvpunkt +15°C ute", "15-60"),
    ("CP1-7", "Kurvpunkt +20°C ute", "15-60"),
]

load_dotenv()

username = os.getenv("PERIFAL_USERNAME")
password = os.getenv("PERIFAL_PASSWORD")
device_code = os.getenv("PERIFAL_DEVICE_CODE", "A09A520276BA")

client = PerifalClient(username, password)
if not client.login():
    print("Login failed!")
    exit(1)

codes = [p[0] for p in CONTROL_PARAMS]
params = client.get_all_parameters(device_code, codes)

print("\n" + "="*70)
print("KONTROLLERBARA PARAMETRAR - PERIFAL LV-418")
print("="*70)

print(f"\n{'Kod':<25} {'Värde':>8} {'Range':<12} {'Beskrivning':<20}")
print("-"*70)

for code, desc, range_str in CONTROL_PARAMS:
    value = params.get(code, "N/A")
    if value != "N/A":
        print(f"{code:<25} {str(value):>8} {range_str:<12} {desc:<20}")

print("\n" + "="*70)
print("TESTA ATT ÄNDRA (var försiktig!):")
print("="*70)
print("""
# Värmekurva (säkert att testa)
python3 perifal_cli.py set compensate_offset 37.0
python3 perifal_cli.py set compensate_slope 1.2

# Varmvatten börvärde
python3 perifal_cli.py set R01 52.0

# Tyst läge (bitmask - bit 1 = tyst)
python3 perifal_cli.py set hanControl 0000000000000010

# Max effektbegränsning (0 = ingen begränsning)
python3 perifal_cli.py set "M1 Max. Power" 3.0

# Legionella-program
python3 perifal_cli.py set G05 1  # Aktivera
python3 perifal_cli.py set G01 65  # Temp 65°C
""")
