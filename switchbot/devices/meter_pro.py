from decimal import ROUND_HALF_UP, Decimal
import time
from typing import Any

from ..const.meter_pro import MeterProWeatherIcon
from ..helpers import parse_uint24_be
from .device import SwitchbotDevice, SwitchbotOperationError

SETTINGS_HEADER = "570f68"
QUERY_HEADER = "570f69"

COMMAND_SET_TIME_OFFSET = f"{SETTINGS_HEADER}0506"
COMMAND_GET_TIME_OFFSET = f"{QUERY_HEADER}0506"
MAX_TIME_OFFSET = (1 << 24) - 1

COMMAND_GET_DEVICE_DATETIME = f"{QUERY_HEADER}01"
COMMAND_GET_DEVICE_DATETIME_EPOCH = "57000603"
COMMAND_SET_DEVICE_DATETIME = "57000503"
COMMAND_SET_DISPLAY_FORMAT = f"{SETTINGS_HEADER}0505"
COMMAND_SET_DATE_FORMAT = f"{SETTINGS_HEADER}070107"
COMMAND_SHOW_BATTERY_LEVEL = f"{SETTINGS_HEADER}070108"
COMMAND_DISPLAY_DATA_SOURCE = f"{SETTINGS_HEADER}0701030f"
COMMAND_BUTTON_FUNCTION = f"{SETTINGS_HEADER}070106"
COMMAND_TEMPERATURE_UPDATE_INTERVAL = f"{SETTINGS_HEADER}070105"
COMMAND_COMFORTLEVEL = f"{SETTINGS_HEADER}020188"
COMMAND_ALERT_SOUND = f"{SETTINGS_HEADER}0204"
COMMAND_GET_DEVICE_SETTINGS = f"{QUERY_HEADER}0701"
COMMAND_GET_TEMPERATURE_HUMIDITY_OFFSETS = f"{QUERY_HEADER}0704"

COMMAND_GET_WEATHER = f"{QUERY_HEADER}06"
COMMAND_SET_WEATHER = f"{SETTINGS_HEADER}0601"

COMMAND_FORCE_NEW_CO2_MEASUREMENT = f"{SETTINGS_HEADER}0b04"
COMMAND_CALIBRATE_CO2_SENSOR = f"{SETTINGS_HEADER}0b02"
COMMAND_CO2_THRESHOLDS = f"{SETTINGS_HEADER}020302"
COMMAND_CO2_UPDATE_INTERVAL = f"{SETTINGS_HEADER}0b06"
COMMAND_GET_CO2_UPDATE_INTERVAL = f"{QUERY_HEADER}0b04"
COMMAND_ALERT_CO2 = f"{SETTINGS_HEADER}020301"
COMMAND_ALERT_TEMPERATURE_HUMIDITY = "570f44"

# History Commands
COMMAND_INIT_HISTORY = "57090100000000"
COMMAND_GET_HISTORY_INFO = f"{QUERY_HEADER}0801"
COMMAND_GET_HISTORY_RANGE = f"{QUERY_HEADER}080201"
COMMAND_GET_HISTORY_PAGE = f"{QUERY_HEADER}0803010000"


def _weather_decimal(value: float | Decimal, name: str) -> Decimal:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a number, not a boolean")
    if not isinstance(value, (int, float, Decimal)):
        raise TypeError(f"{name} must be a number")
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    return number


def _encode_weather_temperature(temperature_c: float | Decimal) -> bytes:
    temperature = _weather_decimal(temperature_c, "Temperature")
    if abs(temperature) > Decimal("127.9"):
        raise ValueError("Temperature must be -127.9..127.9 C")
    temperature = temperature.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    integer, decimal = divmod(int(abs(temperature) * 10), 10)
    return bytes((decimal, integer | (0x80 if temperature >= 0 else 0)))


def _encode_weather_humidity(humidity: float | Decimal) -> int:
    relative_humidity = _weather_decimal(humidity, "humidity")
    if not 1 <= relative_humidity <= 99:
        raise ValueError("humidity must be 1..99%")
    return int(relative_humidity.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _encode_weather_icon(icon: MeterProWeatherIcon | int) -> MeterProWeatherIcon:
    if isinstance(icon, MeterProWeatherIcon):
        return icon
    if isinstance(icon, bool) or not isinstance(icon, int):
        raise TypeError("Weather icon must be a MeterProWeatherIcon or integer")
    return MeterProWeatherIcon(icon)


def _get_point_five_byte(cold: float, hot: float) -> str:
    """Represents if either of the temperatures has a .5 decimal place."""
    point_five = 0x00
    if int(cold * 10) % 10 == 5:
        point_five += 0x05
    if int(hot * 10) % 10 == 5:
        point_five += 0x50
    return f"{point_five:02x}"


def _encode_comfort_temperature(temp: int) -> str:
    """Encode temperature for comfort level and alert thresholds."""
    if temp > 0:
        val = temp + 128
    else:
        val = abs(temp)
    return f"{val:02x}"


def parse_history_chunk(
    chunk: bytes, start_ts: int, step_seconds: int, base_index: int = 0
) -> list[dict[str, Any]]:
    """
    Parse an 18-byte history record chunk (containing up to 4 historical readings).

    Structure: Two 9-byte half-chunks, each encoding 2 records.
    Half-chunk layout:
      b0: temp1 integer & sign (bit 7: positive sign, bits 6-0: integer C)
      b1: humidity 1 (bits 6-0: % RH)
      b2: temp1 decimal (high nibble) and temp2 decimal (low nibble) in 0.1 C
      b3: temp2 integer & sign (bit 7: positive sign, bits 6-0: integer C)
      b4: humidity 2 (bits 6-0: % RH)
      b5-b6: co2 1 (uint16_be ppm)
      b7-b8: co2 2 (uint16_be ppm)
    """
    records = []
    for half_idx in range(len(chunk) // 9):
        sub = chunk[half_idx * 9 : (half_idx + 1) * 9]
        if len(sub) < 9:
            break

        # Record 1 in half-chunk
        sign1 = 1 if sub[0] & 0x80 else -1
        int_t1 = sub[0] & 0x7F
        dec_t1 = (sub[2] >> 4) & 0x0F
        temp1 = round(sign1 * (int_t1 + dec_t1 / 10.0), 1)
        hum1 = sub[1] & 0x7F
        co2_1 = (sub[5] << 8) | sub[6]
        rec1_idx = base_index + half_idx * 2
        ts1 = start_ts + rec1_idx * step_seconds
        records.append(
            {
                "index": rec1_idx,
                "timestamp": ts1,
                "temperature": temp1,
                "humidity": hum1,
                "co2": co2_1,
            }
        )

        # Record 2 in half-chunk
        sign2 = 1 if sub[3] & 0x80 else -1
        int_t2 = sub[3] & 0x7F
        dec_t2 = sub[2] & 0x0F
        temp2 = round(sign2 * (int_t2 + dec_t2 / 10.0), 1)
        hum2 = sub[4] & 0x7F
        co2_2 = (sub[7] << 8) | sub[8]
        rec2_idx = base_index + half_idx * 2 + 1
        ts2 = start_ts + rec2_idx * step_seconds
        records.append(
            {
                "index": rec2_idx,
                "timestamp": ts2,
                "temperature": temp2,
                "humidity": hum2,
                "co2": co2_2,
            }
        )
    return records


class SwitchbotMeterPro(SwitchbotDevice):
    """API to control Switchbot Meter Pro (and Meter Pro CO2, which shares the same base protocol)."""

    async def get_time_offset(self) -> int:
        """
        Get the current display time offset from the device.

        Returns:
            int: The time offset in seconds. Max 24 bits.

        """
        result = await self._send_command(COMMAND_GET_TIME_OFFSET)
        result = self._validate_result("get_time_offset", result, min_length=5)

        is_negative = bool(result[1] & 0b10000000)
        offset = parse_uint24_be(result, 2)
        return -offset if is_negative else offset

    async def set_time_offset(self, offset_seconds: int) -> None:
        """
        Set the display time offset on the device.

        Args:
            offset_seconds (int): 2^24 maximum, can be negative.

        """
        abs_offset = abs(offset_seconds)
        if abs_offset > MAX_TIME_OFFSET:
            raise SwitchbotOperationError(
                f"{self.name}: Requested to set_time_offset of {offset_seconds} seconds, allowed +-{MAX_TIME_OFFSET} max."
            )

        sign_byte = "80" if offset_seconds < 0 else "00"
        payload = f"{COMMAND_SET_TIME_OFFSET}{sign_byte}{abs_offset:06x}"
        result = await self._send_command(payload)
        self._validate_result("set_time_offset", result)

    async def get_datetime(self) -> dict[str, Any]:
        """
        Get the current device time and settings as it is displayed.

        Returns:
            dict: Dictionary containing:
                - 12h_mode (bool): True if 12h mode, False if 24h mode.
                - year (int)
                - month (int)
                - day (int)
                - hour (int)
                - minute (int)
                - second (int)

        """
        result = await self._send_command(COMMAND_GET_DEVICE_DATETIME)
        result = self._validate_result("get_datetime", result, min_length=13)
        return {
            "12h_mode": bool(result[5] & 0b10000000),
            "year": (result[6] << 8) + result[7],
            "month": result[8],
            "day": result[9],
            "hour": result[10],
            "minute": result[11],
            "second": result[12],
        }

    async def get_datetime_epoch(self) -> dict[str, Any]:
        """Get the internal device UTC timestamp and timezone byte."""
        result = await self._send_command(COMMAND_GET_DEVICE_DATETIME_EPOCH)
        result = self._validate_result("get_datetime_epoch", result, min_length=10)
        utc_byte = result[1]
        utc_offset_hours = utc_byte - 12
        timestamp = int.from_bytes(result[2:10], "big")
        return {
            "timestamp": timestamp,
            "utc_offset_hours": utc_offset_hours,
        }

    async def set_datetime(
        self, timestamp: int, utc_offset_hours: int = 0, utc_offset_minutes: int = 0
    ) -> None:
        """
        Set the device internal time and timezone.

        Args:
            timestamp (int): Unix timestamp in seconds.
            utc_offset_hours (int): UTC offset in hours, floor()'ed, within [-12; 14] range.
            utc_offset_minutes (int): UTC offset minutes component, always positive.

        """
        if not (-12 <= utc_offset_hours <= 14):
            raise SwitchbotOperationError(
                f"{self.name}: utc_offset_hours must be between -12 and +14 inclusive, got {utc_offset_hours}"
            )
        if not (0 <= utc_offset_minutes < 60):
            raise SwitchbotOperationError(
                f"{self.name}: utc_offset_minutes must be between 0 and 59 inclusive, got {utc_offset_minutes}"
            )

        adjusted_timestamp = timestamp + utc_offset_minutes * 60
        utc_byte = utc_offset_hours + 12

        payload = (
            f"{COMMAND_SET_DEVICE_DATETIME}{utc_byte:02x}"
            f"{adjusted_timestamp:016x}{utc_offset_minutes:02x}"
        )

        result = await self._send_command(payload)
        self._validate_result("set_datetime", result)

    async def set_time_display_format(self, is_12h_mode: bool = False) -> None:
        """Set the time display format on the device: 12h (AM/PM) or 24h."""
        mode_byte = "80" if is_12h_mode else "00"
        payload = f"{COMMAND_SET_DISPLAY_FORMAT}{mode_byte}"
        result = await self._send_command(payload)
        self._validate_result("set_time_display_format", result)

    async def set_date_display_format(self, is_month_first: bool = True) -> None:
        """Set the date display format (Month/Day vs Day/Month)."""
        mode_byte = "00" if is_month_first else "01"
        payload = f"{COMMAND_SET_DATE_FORMAT}{mode_byte}"
        result = await self._send_command(payload)
        self._validate_result("set_date_display_format", result)

    async def show_battery_level(self, show_battery: bool = True) -> None:
        """Show or hide battery percentage level on the display."""
        show_battery_byte = "01" if show_battery else "00"
        payload = f"{COMMAND_SHOW_BATTERY_LEVEL}{show_battery_byte}"
        result = await self._send_command(payload)
        self._validate_result("show_battery_level", result)

    async def set_display_data_source(self, outdoor: bool = False) -> None:
        """Set the screen data source: indoor sensor readings or outdoor network display."""
        mode_byte = "02" if outdoor else "01"
        payload = f"{COMMAND_DISPLAY_DATA_SOURCE}{mode_byte}"
        result = await self._send_command(payload)
        self._validate_result("set_display_data_source", result)

    async def set_button_function(
        self, change_unit: bool = False, change_data_source: bool = False
    ) -> None:
        """
        Sets the function of the top physical button:
        Default (both options false): Only update/refresh data.
        change_unit: switch between ℃ and ℉.
        change_data_source: switch between display of indoor and outdoor readings.
        """
        change_unit_byte = "00" if change_unit else "01"
        change_data_source_byte = "01" if change_data_source else "00"
        payload = f"{COMMAND_BUTTON_FUNCTION}{change_unit_byte}{change_data_source_byte}"
        result = await self._send_command(payload)
        self._validate_result("set_button_function", result)

    async def set_temperature_update_interval(self, seconds: int) -> None:
        """
        Sets the interval in which temperature and humidity are measured in battery powered mode.
        Original App assumes seconds in {5*60, 10*60, 30*60}.
        """
        payload = f"{COMMAND_TEMPERATURE_UPDATE_INTERVAL}{seconds:04x}"
        result = await self._send_command(payload)
        self._validate_result("set_temperature_update_interval", result)

    async def set_comfortlevel(
        self, cold: float, hot: float, dry: int, wet: int
    ) -> None:
        """
        Sets the thresholds for comfortable temperature (in °C) and humidity to display comfort-level.
        The supported values in the original App are:
          Temperature: -20°C to 80°C in 0.5°C steps.
          Humidity: 1% to 99% in 1% steps.
        """
        if cold >= hot:
            raise ValueError("Cold should be smaller than Hot")
        if dry >= wet:
            raise ValueError("Dry should be smaller than Wet")

        point_five = _get_point_five_byte(cold, hot)
        cold_byte = _encode_comfort_temperature(int(cold))
        hot_byte = _encode_comfort_temperature(int(hot))

        payload = (
            f"{COMMAND_COMFORTLEVEL}"
            f"{hot_byte}"
            f"{wet:02x}"
            f"{point_five}"
            f"{cold_byte}"
            f"{dry:02x}"
        )
        result = await self._send_command(payload)
        self._validate_result("set_comfortlevel", result)

    async def set_alert_sound(self, sound_on: bool, volume: int) -> None:
        """
        Sets the Alert Mode.
        If sound_on is False the display flashes.
        If sound_on is True the device additionally beeps.
        The volume is expected to be in {2, 3, 4} (2: low, 3: medium, 4: high).
        """
        if volume not in (2, 3, 4):
            raise ValueError("Volume must be 2 (low), 3 (medium), or 4 (high)")

        sound_on_byte = "02" if sound_on else "01"
        payload = f"{COMMAND_ALERT_SOUND}{volume:02x}{sound_on_byte}"
        result = await self._send_command(payload)
        self._validate_result("set_alert_sound", result)

    async def get_device_settings(self) -> dict[str, Any]:
        """Get the device configuration profile settings."""
        result = await self._send_command(COMMAND_GET_DEVICE_SETTINGS)
        result = self._validate_result("get_device_settings", result, min_length=1)
        data: dict[str, Any] = {
            "raw": result[1:].hex(),
        }
        if len(result) >= 18:
            data["outdoor_mode"] = bool(result[1] & 0x80)
            data["temperature_update_interval"] = int.from_bytes(result[15:17], "big")
        return data

    async def get_temperature_humidity_offsets(self) -> dict[str, Any]:
        """Get temperature and humidity calibration offsets."""
        result = await self._send_command(COMMAND_GET_TEMPERATURE_HUMIDITY_OFFSETS)
        result = self._validate_result(
            "get_temperature_humidity_offsets", result, min_length=3
        )
        temp_raw = result[1]
        temp_offset = (
            (temp_raw - 128) / 10.0 if temp_raw & 0x80 else temp_raw / 10.0
        )
        hum_offset = result[2]
        return {
            "temperature_offset": temp_offset,
            "humidity_offset": hum_offset,
            "raw": result[1:].hex(),
        }

    def _validate_result(
        self, op_name: str, result: bytes | None, min_length: int | None = None
    ) -> bytes:
        if not self._check_command_result(result, 0, {1}):
            raise SwitchbotOperationError(
                f"{self.name}: Unexpected response code for {op_name} (result={result.hex() if result else 'None'} rssi={self.rssi})"
            )
        assert result is not None
        if min_length is not None and len(result) < min_length:
            raise SwitchbotOperationError(
                f"{self.name}: Unexpected response len for {op_name}, wanted at least {min_length} (result={result.hex() if result else 'None'} rssi={self.rssi})"
            )
        return result


class SwitchbotMeterProCO2(SwitchbotMeterPro):
    """API to control Switchbot Meter Pro CO2."""

    async def force_new_co2_measurement(self) -> None:
        """Requests a new CO2 measurement, regardless of update interval."""
        result = await self._send_command(COMMAND_FORCE_NEW_CO2_MEASUREMENT)
        self._validate_result("force_new_co2_measurement", result)

    async def calibrate_co2_sensor(self) -> None:
        """
        Calibrate CO2-Sensor.
        Place your device in a well-ventilated area for 1 minute before calling this.
        After calling this the calibration runs for about 5 minutes.
        Keep the device still during this process.
        """
        result = await self._send_command(COMMAND_CALIBRATE_CO2_SENSOR)
        self._validate_result("calibrate_co2_sensor", result)

    async def set_co2_update_interval(self, seconds: int) -> None:
        """
        Sets the interval in which co2 levels are measured in battery powered mode.
        Original App assumes seconds in {5*60, 10*60, 30*60}.
        """
        result = await self._send_command(
            COMMAND_CO2_UPDATE_INTERVAL + f"{seconds:04x}"
        )
        self._validate_result("set_co2_update_interval", result)

    async def get_co2_update_interval(self) -> int:
        """Get the current CO2 update interval in seconds."""
        result = await self._send_command(COMMAND_GET_CO2_UPDATE_INTERVAL)
        result = self._validate_result("get_co2_update_interval", result, min_length=3)
        return (result[1] << 8) | result[2]

    async def set_co2_thresholds(self, lower: int, upper: int) -> None:
        """
        Sets the thresholds to define Air Quality for depiction on display as follows:
        co2 < lower => Good (Green)
        lower < co2 < upper => Moderate (Orange)
        upper < co2 => Poor (Red)

        Original App assumes:
        500 <= lower < upper <= 1900
        lower and upper are multiples of 100
        """
        if lower < 500:
            raise SwitchbotOperationError(
                "Original App assumes that lower threshold is at least 500"
            )
        if upper > 1900:
            raise SwitchbotOperationError(
                "Original App assumes that upper threshold is at most 1900"
            )
        if lower >= upper:
            raise SwitchbotOperationError(
                "Lower threshold should be smaller than upper threshold"
            )

        result = await self._send_command(
            COMMAND_CO2_THRESHOLDS + f"{lower:04x}" + f"{upper:04x}"
        )
        self._validate_result("set_co2_thresholds", result)

    async def set_alert_co2(
        self, on: bool, co2_low: int, co2_high: int, reverse: bool = False
    ) -> None:
        """
        Sets the CO2-Alert.
        on: Turn CO2-Alert on or off
        co2_low and co2_high: Range (between 400ppm and 2000ppm in 100ppm steps)
        reverse: If False: Alert if measured value is outside of provided range.
                 If True: Alert if measured value is inside of provided range.
        """
        if co2_high < co2_low:
            raise ValueError(
                "Upper value should bigger than the lower value. Do you want to use reverse instead?"
            )

        mode = 0x00 if not on else (0x04 if reverse else 0x03)
        payload = f"{COMMAND_ALERT_CO2}{mode:02x}{co2_high:04x}{co2_low:04x}"
        result = await self._send_command(payload)
        self._validate_result("set_alert_co2", result)

    async def set_alert_temperature_humidity(
        self,
        temperature_alert: bool = False,
        temperature_low: float = -20.0,
        temperature_high: float = 80.0,
        temperature_reverse: bool = False,
        humidity_alert: bool = False,
        humidity_low: int = 1,
        humidity_high: int = 99,
        humidity_reverse: bool = False,
        absolute_humidity_alert: bool = False,
        absolute_humidity_low: float = 0.00,
        absolute_humidity_high: float = 99.99,
        absolute_humidity_reverse: bool = False,
        dewpoint_alert: bool = False,
        dewpoint_low: float = -60.0,
        dewpoint_high: float = 60.0,
        dewpoint_reverse: bool = False,
        vpd_alert: bool = False,
        vpd_low: float = 0.00,
        vpd_high: float = 10.00,
        vpd_reverse: bool = False,
    ) -> None:
        """
        Sets Temperature- and Humidity- related alerts.
        *_alert: enable or disable respective alert.
        *_low and *_high: The respective ranges.
        *_reverse: If False: Alert if measured value is outside of provided range.
                   If True: Alert if measured value is inside of provided range.
        """
        mode_temp_humid = 0x00
        if temperature_alert:
            mode_temp_humid += 0x04 if temperature_reverse else 0x03
        if humidity_alert:
            mode_temp_humid += 0x40 if humidity_reverse else 0x30

        mode_abshumid_dewpoint_vpd = 0x00
        if absolute_humidity_alert:
            mode_abshumid_dewpoint_vpd += 0x02 if absolute_humidity_reverse else 0x01
        if dewpoint_alert:
            mode_abshumid_dewpoint_vpd += 0x10 if dewpoint_reverse else 0x0C
        if vpd_alert:
            mode_abshumid_dewpoint_vpd += 0x80 if vpd_reverse else 0x60

        temperature_point_five = _get_point_five_byte(
            temperature_low, temperature_high
        )
        temperature_low_byte = _encode_comfort_temperature(int(temperature_low))
        temperature_high_byte = _encode_comfort_temperature(int(temperature_high))

        dewpoint_point_five = _get_point_five_byte(dewpoint_low, dewpoint_high)
        dewpoint_low_byte = _encode_comfort_temperature(int(dewpoint_low))
        dewpoint_high_byte = _encode_comfort_temperature(int(dewpoint_high))

        absolute_humidity_low_bytes = (
            f"{int(absolute_humidity_low):02x}"
            + f"{int(absolute_humidity_low * 100 % 100):02x}"
        )
        absolute_humidity_high_bytes = (
            f"{int(absolute_humidity_high):02x}"
            + f"{int(absolute_humidity_high * 100 % 100):02x}"
        )

        vpd_bytes = (
            f"{int(vpd_high * 100 % 100):02x}"
            + f"{int(vpd_low * 100 % 100):02x}"
            + f"{int(vpd_high):01x}"
        ) + f"{int(vpd_low):01x}"

        payload = (
            f"{COMMAND_ALERT_TEMPERATURE_HUMIDITY}"
            f"{mode_temp_humid:02x}"
            f"{temperature_high_byte}"
            f"{humidity_high:02x}"
            f"{temperature_point_five}"
            f"{temperature_low_byte}"
            f"{humidity_low:02x}"
            f"{dewpoint_high_byte}"
            f"{dewpoint_point_five}"
            f"{dewpoint_low_byte}"
            f"{vpd_bytes}"
            f"{mode_abshumid_dewpoint_vpd:02x}"
            f"{absolute_humidity_high_bytes}"
            f"{absolute_humidity_low_bytes}"
        )
        result = await self._send_command(payload)
        self._validate_result("set_alert_temperature_humidity", result)

    def _parse_weather(self, result: bytes | None) -> tuple[MeterProWeatherIcon, bytes]:
        result = self._validate_result("get_weather", result, min_length=5)
        try:
            icon = MeterProWeatherIcon(result[1])
        except ValueError as exc:
            raise SwitchbotOperationError(
                f"{self.name}: Unknown weather icon {result[1]} "
                f"(result={result.hex()} rssi={self.rssi})"
            ) from exc
        if result[2] & 0x0F > 9 or result[4] & 0x7F > 99:
            raise SwitchbotOperationError(
                f"{self.name}: Invalid weather measurements "
                f"(result={result.hex()} rssi={self.rssi})"
            )
        return icon, bytes(result[2:5])

    async def get_weather(self) -> dict[str, Any]:
        """Read network weather; unset humidity is None, temperature is independent."""
        icon, network_data = self._parse_weather(
            await self._send_command(COMMAND_GET_WEATHER)
        )
        magnitude = (network_data[1] & 0x7F) + (network_data[0] & 0x0F) / 10
        temperature = magnitude if network_data[1] & 0x80 else -magnitude
        return {
            "icon": icon,
            "temperature_c": temperature if magnitude else 0.0,
            "humidity": (network_data[2] & 0x7F) or None,
            "fahrenheit_display": bool(network_data[2] & 0x80),
        }

    async def _weather_command_locked(self, key: str, retry: int) -> bytes | None:
        command = bytearray.fromhex(self._commandkey(key))
        return await self._send_command_locked_with_retry(
            key, command, retry, retry + 1
        )

    async def set_weather(
        self,
        icon: MeterProWeatherIcon | int | None = None,
        *,
        temperature_c: float | Decimal | None = None,
        humidity: float | Decimal | None = None,
    ) -> None:
        """
        Set supplied weather fields and verify them; preserve omitted fields.

        This does not change display sources or paired external sensor data.
        Updates from other clients can race with the read/write sequence.
        """
        if icon is None and temperature_c is None and humidity is None:
            raise SwitchbotOperationError(
                f"{self.name}: Supply at least one weather field"
            )
        try:
            icon = _encode_weather_icon(icon) if icon is not None else None
            temperature = (
                _encode_weather_temperature(temperature_c)
                if temperature_c is not None
                else None
            )
            relative_humidity = (
                _encode_weather_humidity(humidity) if humidity is not None else None
            )
        except (ValueError, TypeError) as exc:
            raise SwitchbotOperationError(f"{self.name}: {exc}") from exc
        async with self._operation_lock:
            current_icon, network_data = self._parse_weather(
                await self._weather_command_locked(
                    COMMAND_GET_WEATHER, self._retry_count
                )
            )
            icon = current_icon if icon is None else icon
            network = bytearray(network_data)
            if temperature is not None:
                network[0] = (network[0] & 0xF0) | temperature[0]
                network[1] = temperature[1]
            if relative_humidity is not None:
                network[2] = (network[2] & 0x80) | relative_humidity
            payload = f"{COMMAND_SET_WEATHER}{icon.value:02x}{network.hex()}"
            self._validate_result(
                "set_weather", await self._weather_command_locked(payload, 0)
            )
            updated_icon, updated_network = self._parse_weather(
                await self._weather_command_locked(
                    COMMAND_GET_WEATHER, self._retry_count
                )
            )
            if updated_icon != icon or updated_network != network:
                raise SwitchbotOperationError(
                    f"{self.name}: Weather readback mismatch; no rollback attempted "
                    f"(expected_icon={icon.value} expected_network={network.hex()} "
                    f"actual_icon={updated_icon.value} actual_network={updated_network.hex()} "
                    f"rssi={self.rssi})"
                )

    async def get_history_range(
        self, stop_timestamp: int | None = None
    ) -> dict[str, Any]:
        """
        Initialize history download and query recording time range & record count.

        Args:
            stop_timestamp: Stop epoch timestamp (seconds). Defaults to current time.

        """
        if stop_timestamp is None:
            stop_timestamp = int(time.time())

        init_cmd = f"{COMMAND_INIT_HISTORY}{stop_timestamp:08x}"
        res_init = await self._send_command(init_cmd)
        self._validate_result("init_history", res_init)

        res_info = await self._send_command(COMMAND_GET_HISTORY_INFO)
        self._validate_result("get_history_info", res_info, min_length=5)

        res_range = await self._send_command(COMMAND_GET_HISTORY_RANGE)
        res_range = self._validate_result(
            "get_history_range", res_range, min_length=15
        )

        start_ts = int.from_bytes(res_range[1:5], "big")
        end_ts = int.from_bytes(res_range[5:9], "big")
        total_records = int.from_bytes(res_range[9:13], "big")
        step_seconds = int.from_bytes(res_range[13:15], "big")

        return {
            "start_time": start_ts,
            "end_time": end_ts,
            "total_records": total_records,
            "step_seconds": step_seconds,
        }

    async def get_history_page(self, page_index: int, count: int = 4) -> bytes:
        """Fetch a page chunk of historical records from the device."""
        cmd = f"{COMMAND_GET_HISTORY_PAGE}{page_index:04x}{count:02x}"
        res = await self._send_command(cmd)
        res = self._validate_result("get_history_page", res, min_length=1)
        return res[1:]

    async def get_history(
        self, max_records: int = 100, stop_timestamp: int | None = None
    ) -> list[dict[str, Any]]:
        """
        Download historical environmental records from the device.

        Args:
            max_records: Maximum number of records to retrieve.
            stop_timestamp: Epoch seconds to query up to. Defaults to now.

        """
        meta = await self.get_history_range(stop_timestamp)
        start_ts = meta["start_time"]
        step_seconds = meta["step_seconds"]
        total_records = meta["total_records"]

        records_to_read = min(total_records, max_records)
        all_records = []
        for idx in range(0, records_to_read, 4):
            count = min(4, records_to_read - idx)
            chunk = await self.get_history_page(idx, count)
            all_records.extend(
                parse_history_chunk(chunk, start_ts, step_seconds, idx)
            )
        return all_records
