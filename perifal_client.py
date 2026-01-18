#!/usr/bin/env python3
"""
Perifal LV-418 Heat Pump API Client
Reverse-engineered from Warmlink/Linked-Go cloud API

Sensor Mapping (verified 2026-01-17):
  T01 = Retur / Return (ingående vatten till pump)
  T02 = Framledning / Flow (utgående vatten från pump)
  T03 = Förångare / Evaporator
  T04 = Utomhustemperatur / Outdoor
  T11 = Varmvattentank / Hot water tank
  T12 = Kompressor hetgas / Compressor discharge

Control parameters:
  Power = 0/1 (av/på)
  compensate_offset = Värmekurva offset (15-60)
  compensate_slope = Värmekurva lutning (0-3.5)
  R01 = Varmvatten börvärde (30-58)
  M1 Heating Target = Värme börvärde (15-60)
"""

import hashlib
import requests
from typing import Optional

# Sensor code to Swedish name mapping
SENSOR_NAMES = {
    "T01": "Retur",
    "T02": "Framledning",
    "T03": "Förångare",
    "T04": "Utomhus",
    "T11": "Varmvatten",
    "T12": "Kompressor",
}


class PerifalClient:
    BASE_URL = "https://cloud.linked-go.com:449/crmservice/api"

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.token: Optional[str] = None
        self.user_id: Optional[str] = None
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "okhttp/5.1.0",
        })

    def _md5_hash(self, text: str) -> str:
        """Hash password with MD5"""
        return hashlib.md5(text.encode()).hexdigest()

    def _request(self, method: str, endpoint: str, json_data: dict = None) -> dict:
        """Make API request with token header"""
        url = f"{self.BASE_URL}{endpoint}?lang=sv"
        headers = {}
        if self.token:
            headers["x-token"] = self.token

        response = self.session.request(
            method=method,
            url=url,
            json=json_data,
            headers=headers
        )
        response.raise_for_status()
        return response.json()

    def login(self) -> bool:
        """Login and get token"""
        data = {
            "userName": self.username,
            "password": self._md5_hash(self.password),
            "loginSource": "Android",
            "type": "2",
            "areaCode": "sv",
            "appId": "16"
        }

        result = self._request("POST", "/app/user/login", data)

        if result.get("error_code") == "0":
            obj = result.get("objectResult", {})
            self.token = obj.get("x-token")
            self.user_id = obj.get("userId")
            print(f"Login OK - User ID: {self.user_id}")
            return True
        else:
            print(f"Login failed: {result.get('error_msg')}")
            return False

    def get_device_list(self) -> list:
        """Get list of devices"""
        # Note: The actual request body may need productIds,
        # but let's try simple first
        data = {}
        result = self._request("POST", "/app/device/deviceList", data)

        if result.get("error_code") == "0":
            devices = result.get("objectResult", [])
            return devices
        return []

    def get_device_status(self, device_code: str) -> dict:
        """Get device online/fault status"""
        data = {"deviceCode": device_code}
        result = self._request("POST", "/app/device/getDeviceStatus", data)

        if result.get("error_code") == "0":
            return result.get("objectResult", {})
        return {}

    def get_all_parameters(self, device_code: str, codes: list = None, retry_login: bool = True) -> dict:
        """Get device parameters by code"""
        # If no codes specified, request common ones
        if codes is None:
            codes = [
                "Power", "Mode", "ModeState",
                "T01", "T02", "T03", "T04", "T05", "T06", "T08", "T10", "T11", "T12",
                "R01", "R02", "R03",
                "M1 Heating Target", "M1 Hot Water Target", "M1 Mode",
                "compensate_slope", "compensate_offset",
                "Fault1", "Fault5", "Fault6"
            ]

        data = {
            "deviceCode": device_code,
            "protocalCodes": codes  # Note: API uses "protocal" (typo in their API)
        }

        try:
            result = self._request("POST", "/app/device/getDataByCode", data)

            if result.get("error_code") == "0":
                params = result.get("objectResult", [])
                # Convert to dict for easier access
                return {p["code"]: p["value"] for p in params}
            elif result.get("error_code") == "-100" and retry_login:
                # Token expired - re-login and retry
                print("Token expired, re-logging in...", flush=True)
                if self.login():
                    return self.get_all_parameters(device_code, codes, retry_login=False)
                return {}
            else:
                error_msg = result.get("error_msg", "Unknown error")
                error_code = result.get("error_code", "?")
                print(f"API error {error_code}: {error_msg}", flush=True)
                return {}
        except Exception as e:
            print(f"Request error: {e}", flush=True)
            return {}

    def control(self, device_code: str, protocol_code: str, value: str) -> bool:
        """Send control command to device"""
        data = {
            "param": [{
                "deviceCode": device_code,
                "protocolCode": protocol_code,
                "value": value
            }]
        }
        result = self._request("POST", "/app/device/control", data)

        if result.get("error_code") == "0":
            print(f"Control OK: {protocol_code} = {value}")
            return True
        else:
            print(f"Control failed: {result.get('error_msg')}")
            return False

    def set_power(self, device_code: str, on: bool) -> bool:
        """Turn device on/off"""
        return self.control(device_code, "Power", "1" if on else "0")

    def set_hot_water_temp(self, device_code: str, temp: float) -> bool:
        """Set hot water target temperature"""
        return self.control(device_code, "R01", str(temp))

    def set_heating_temp(self, device_code: str, temp: float) -> bool:
        """Set heating target temperature"""
        return self.control(device_code, "M1 Heating Target", str(temp))

    def set_curve_offset(self, device_code: str, offset: float) -> bool:
        """Set heating curve offset"""
        return self.control(device_code, "compensate_offset", str(offset))

    def set_curve_slope(self, device_code: str, slope: float) -> bool:
        """Set heating curve slope"""
        return self.control(device_code, "compensate_slope", str(slope))

    def get_history(self, device_code: str, address: str, start_time: str, end_time: str, frequency: str = "day") -> list:
        """
        Get historical data from cloud.

        Args:
            device_code: Device code
            address: Data address (2046=?, 2047=?, 2048=?)
            start_time: Start time "YYYY-MM-DD HH:MM:SS"
            end_time: End time "YYYY-MM-DD HH:MM:SS"
            frequency: "day" or other intervals

        Returns:
            List of historical data points
        """
        data = {
            "deviceCode": device_code,
            "address": address,
            "startTime": start_time,
            "endTime": end_time,
            "frequency": frequency,
            "timeZone": 1,
            "sessionid": ""
        }

        try:
            result = self._request("POST", "/device/snapshot/listCollectData", data)

            if result.get("error_code") == "0":
                return result.get("objectResult", [])
            elif result.get("error_code") == "-100":
                # Token expired - re-login and retry
                print("Token expired, re-logging in...", flush=True)
                if self.login():
                    result = self._request("POST", "/device/snapshot/listCollectData", data)
                    if result.get("error_code") == "0":
                        return result.get("objectResult", [])
            else:
                print(f"History API error: {result.get('error_msg')}", flush=True)
            return []
        except Exception as e:
            print(f"History request error: {e}", flush=True)
            return []


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    load_dotenv()

    username = os.getenv("PERIFAL_USERNAME")
    password = os.getenv("PERIFAL_PASSWORD")
    device_code = os.getenv("PERIFAL_DEVICE_CODE", "A09A520276BA")

    if not username or not password:
        print("Set PERIFAL_USERNAME and PERIFAL_PASSWORD in .env file")
        exit(1)

    client = PerifalClient(username, password)

    if client.login():
        print("\n--- Device Status ---")
        status = client.get_device_status(device_code)
        print(f"Status: {status}")

        print("\n--- Parameters ---")
        params = client.get_all_parameters(device_code)
        for code, value in params.items():
            print(f"  {code}: {value}")
