#!/usr/bin/env python3
"""
Script to identify all temperature sensors
"""

import os
from dotenv import load_dotenv
from perifal_client import PerifalClient

# All T-sensors from the API response
T_SENSORS = [
    "T01", "T02", "T03", "T04", "T05", "T06", "T07", "T08", "T09", "T10",
    "T11", "T12", "T15", "T27", "T28", "T29", "T30", "T31", "T32", "T33",
    "T34", "T35", "T36", "T37", "T38", "T39", "T40", "T41", "T42", "T43",
    "T44", "T46", "T47", "T48"
]

# Other interesting parameters for identification
OTHER_TEMPS = [
    "Zone 1 Room Temp", "Zone 2 Room Temp", "Zone 2 Mixing Temp",
    "Zone 2 Water Target"
]

# Known mappings from typical heat pump setups
LIKELY_MAPPINGS = {
    "T01": "Framledning / Flow temp",
    "T02": "Retur / Return temp",
    "T03": "Utomhus / Outdoor (or indoor?)",
    "T04": "Förångare / Evaporator",
    "T05": "Sugas / Suction gas",
    "T06": "?",
    "T07": "?",
    "T08": "?",
    "T12": "Kompressor utlopp / Compressor discharge",
    "T15": "Varmvatten / Hot water tank",
}

load_dotenv()

username = os.getenv("PERIFAL_USERNAME")
password = os.getenv("PERIFAL_PASSWORD")
device_code = os.getenv("PERIFAL_DEVICE_CODE", "A09A520276BA")

client = PerifalClient(username, password)
if not client.login():
    print("Login failed!")
    exit(1)

all_codes = T_SENSORS + OTHER_TEMPS
params = client.get_all_parameters(device_code, all_codes)

print("\n" + "="*60)
print("TEMPERATURSENSORER - IDENTIFIERING")
print("="*60)
print("\nJämför dessa värden med vad du ser i Warmlink-appen")
print("och på pumpens display för att identifiera sensorerna.\n")

print(f"{'Kod':<10} {'Värde':>10}  {'Trolig betydelse':<30}")
print("-"*60)

for code in T_SENSORS:
    value = params.get(code, "N/A")
    guess = LIKELY_MAPPINGS.get(code, "")
    if value != "N/A" and value != "0.0" and value != "-55.0":  # Filter out unused
        print(f"{code:<10} {value:>10}  {guess:<30}")

print("\n" + "-"*60)
print("ANDRA TEMPERATURER:")
print("-"*60)
for code in OTHER_TEMPS:
    value = params.get(code, "N/A")
    if value != "N/A":
        print(f"{code:<25} {value:>10}")

print("\n" + "="*60)
print("TIPS FÖR IDENTIFIERING:")
print("="*60)
print("""
1. Gå ut och känn på utomhustempen - vilken sensor matchar?
2. Kolla varmvattentanken i Warmlink - vilken T-sensor visar samma?
3. Framledning = det som går UT från pumpen till radiatorer
4. Retur = det som kommer TILLBAKA från radiatorer
5. T12 är ofta kompressor (hög temp 60-90°C när den kör)
6. Sensorer som visar 0.0 eller -55.0 är troligen oanvända
""")
