#!/usr/bin/env python3
"""
Perifal LV-418 CLI - Command line tool for heat pump control
"""

import argparse
import os
import sys
from dotenv import load_dotenv
from perifal_client import PerifalClient

# Important parameter codes - mapped sensors:
# T01 = Retur (ingående vatten till pump)
# T02 = Framledning (utgående vatten från pump)
# T03 = Förångare
# T04 = Utomhustemperatur
# T11 = Varmvattentank
# T12 = Kompressor hetgas
TEMP_SENSORS = ["T01", "T02", "T03", "T04", "T05", "T06", "T08", "T10", "T11", "T12", "T15"]
SETTINGS = ["Power", "Mode", "ModeState", "M1 Mode", "M1 Heating Target", "M1 Hot Water Target",
            "R01", "R02", "R03", "compensate_slope", "compensate_offset"]
FAULTS = ["Fault1", "Fault5", "Fault6"]

ALL_CODES = TEMP_SENSORS + SETTINGS + FAULTS


def print_status(client: PerifalClient, device_code: str):
    """Print current pump status"""
    status = client.get_device_status(device_code)
    params = client.get_all_parameters(device_code, ALL_CODES)

    print("\n╔══════════════════════════════════════════════╗")
    print("║         PERIFAL LV-418 STATUS                ║")
    print("╠══════════════════════════════════════════════╣")

    # Online status
    online = status.get("status", "UNKNOWN")
    fault = status.get("is_fault", False)
    power = params.get("Power", "?")

    print(f"║  Status: {online:<10} Power: {'ON' if power == '1' else 'OFF':<5} Fault: {'YES' if fault else 'NO':<4}║")
    print("╠══════════════════════════════════════════════╣")

    # Temperatures
    print("║  TEMPERATURER                                ║")
    print(f"║    Utomhus (T04):        {params.get('T04', '?'):>6}°C           ║")
    print(f"║    Framledning (T02):    {params.get('T02', '?'):>6}°C           ║")
    print(f"║    Retur (T01):          {params.get('T01', '?'):>6}°C           ║")
    print(f"║    Varmvatten (T11):     {params.get('T11', '?'):>6}°C           ║")
    print(f"║    Förångare (T03):      {params.get('T03', '?'):>6}°C           ║")
    print(f"║    Kompressor (T12):     {params.get('T12', '?'):>6}°C           ║")
    print("╠══════════════════════════════════════════════╣")

    # Settings
    print("║  INSTÄLLNINGAR                               ║")
    print(f"║    Värmekurva offset:    {params.get('compensate_offset', '?'):>6}             ║")
    print(f"║    Värmekurva lutning:   {params.get('compensate_slope', '?'):>6}             ║")
    print(f"║    Värme börvärde:       {params.get('M1 Heating Target', '?'):>6}°C           ║")
    print(f"║    VV börvärde (M1):     {params.get('M1 Hot Water Target', '?'):>6}°C           ║")
    print(f"║    VV börvärde (R01):    {params.get('R01', '?'):>6}°C           ║")
    print("╚══════════════════════════════════════════════╝")


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Perifal LV-418 Heat Pump CLI")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Status command
    subparsers.add_parser("status", help="Show pump status")

    # Get parameter
    get_parser = subparsers.add_parser("get", help="Get parameter value")
    get_parser.add_argument("code", help="Parameter code (e.g., T01, R01)")

    # Set parameter
    set_parser = subparsers.add_parser("set", help="Set parameter value")
    set_parser.add_argument("code", help="Parameter code")
    set_parser.add_argument("value", help="New value")

    # Convenience commands
    subparsers.add_parser("on", help="Turn pump on")
    subparsers.add_parser("off", help="Turn pump off")

    curve_parser = subparsers.add_parser("curve", help="Set heating curve")
    curve_parser.add_argument("--offset", type=float, help="Curve offset (15-60)")
    curve_parser.add_argument("--slope", type=float, help="Curve slope (0-3.5)")

    vv_parser = subparsers.add_parser("vv", help="Set hot water temperature")
    vv_parser.add_argument("temp", type=float, help="Temperature (30-58)")

    args = parser.parse_args()

    # Get credentials
    username = os.getenv("PERIFAL_USERNAME")
    password = os.getenv("PERIFAL_PASSWORD")
    device_code = os.getenv("PERIFAL_DEVICE_CODE", "A09A520276BA")

    if not username or not password:
        print("Error: Set PERIFAL_USERNAME and PERIFAL_PASSWORD in .env")
        sys.exit(1)

    # Connect
    client = PerifalClient(username, password)
    if not client.login():
        print("Login failed!")
        sys.exit(1)

    # Execute command
    if args.command == "status" or args.command is None:
        print_status(client, device_code)

    elif args.command == "get":
        params = client.get_all_parameters(device_code, [args.code])
        value = params.get(args.code, "NOT FOUND")
        print(f"{args.code} = {value}")

    elif args.command == "set":
        if client.control(device_code, args.code, args.value):
            print(f"OK: {args.code} = {args.value}")
        else:
            print("Failed!")
            sys.exit(1)

    elif args.command == "on":
        client.set_power(device_code, True)

    elif args.command == "off":
        client.set_power(device_code, False)

    elif args.command == "curve":
        if args.offset is not None:
            client.set_curve_offset(device_code, args.offset)
        if args.slope is not None:
            client.set_curve_slope(device_code, args.slope)
        if args.offset is None and args.slope is None:
            params = client.get_all_parameters(device_code, ["compensate_offset", "compensate_slope"])
            print(f"Offset: {params.get('compensate_offset')}")
            print(f"Slope:  {params.get('compensate_slope')}")

    elif args.command == "vv":
        client.set_hot_water_temp(device_code, args.temp)


if __name__ == "__main__":
    main()
