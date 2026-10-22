from unittest.mock import AsyncMock

import pytest
from bleak.backends.device import BLEDevice

from switchbot import SwitchbotOperationError
from switchbot.devices.meter_pro import (
    MAX_TIME_OFFSET,
    SwitchbotMeterPro,
    SwitchbotMeterProCO2,
)


def create_device(cls=SwitchbotMeterProCO2):
    ble_device = BLEDevice(
        address="aa:bb:cc:dd:ee:ff", name="any", details={"rssi": -80}
    )
    device = cls(ble_device)
    device._send_command = AsyncMock()
    return device


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "device_response",
        "expected_offset",
    ),
    [
        ("0100101bc9", 1055689),  # 01 (success) 00 (plus offset) 10 1b c9 (1055689)
        ("0180101bc9", -1055689),  # 01 (success) 80 (minus offset) 10 1b c9 (1055689)
    ],
)
async def test_get_time_offset(device_response: str, expected_offset: int):
    device = create_device()
    device._send_command.return_value = bytes.fromhex(device_response)

    offset = await device.get_time_offset()
    device._send_command.assert_called_with("570f690506")
    assert offset == expected_offset


@pytest.mark.asyncio
async def test_get_time_offset_failure():
    device = create_device()
    # Invalid 1st byte
    device._send_command.return_value = bytes.fromhex("0080101bc9")

    with pytest.raises(SwitchbotOperationError):
        await device.get_time_offset()
    device._send_command.assert_called_with("570f690506")


@pytest.mark.asyncio
async def test_get_time_offset_wrong_response():
    device = create_device()
    # Response too short (only status byte returned)
    device._send_command.return_value = bytes.fromhex("01")

    with pytest.raises(SwitchbotOperationError):
        await device.get_time_offset()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "offset_sec",
        "expected_payload",
    ),
    [
        (1055689, "00101bc9"),  # "00" for positive offset, 101bc9 for 1055689
        (-4096, "80001000"),  # "80" for negative offset, 001000 for 4096
        (0, "00000000"),
        (-0, "00000000"),  # -0 == 0 in Python
    ],
)
async def test_set_time_offset(offset_sec: int, expected_payload: str):
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    await device.set_time_offset(offset_sec)
    device._send_command.assert_called_with("570f680506" + expected_payload)


@pytest.mark.asyncio
async def test_set_time_offset_too_large():
    device = create_device()
    with pytest.raises(SwitchbotOperationError):
        await device.set_time_offset(MAX_TIME_OFFSET + 1)

    with pytest.raises(SwitchbotOperationError):
        await device.set_time_offset(-(MAX_TIME_OFFSET + 1))


@pytest.mark.asyncio
async def test_set_time_offset_failure():
    device = create_device()
    device._send_command.return_value = bytes.fromhex("00")

    with pytest.raises(SwitchbotOperationError):
        await device.set_time_offset(100)


@pytest.mark.asyncio
async def test_get_datetime_success():
    device = create_device()
    # Mock response:
    # byte 0: 01 (success)
    # bytes 1-4: e4 02 94 23 (temp, ignored)
    # byte 5: 00 (24h mode)
    # bytes 6-7: 07 e9 (year 2025)
    # byte 8: 0c (Dec)
    # byte 9: 1e (30)
    # byte 10: 08 (Hour)
    # byte 11: 37 (Minute = 55)
    # byte 12: 01 (Second)
    response_hex = "01e40294230007e90c1e083701"
    device._send_command.return_value = bytes.fromhex(response_hex)

    result = await device.get_datetime()
    device._send_command.assert_called_with("570f6901")

    assert result["12h_mode"] is False
    assert result["year"] == 2025
    assert result["month"] == 12
    assert result["day"] == 30
    assert result["hour"] == 8
    assert result["minute"] == 55
    assert result["second"] == 1


@pytest.mark.asyncio
async def test_get_datetime_12h_mode():
    device = create_device()
    # byte 5: 80 (12h mode)
    # Time: 12:00:00
    response_hex = "010000000080000001010c0000"
    device._send_command.return_value = bytes.fromhex(response_hex)

    result = await device.get_datetime()
    device._send_command.assert_called_with("570f6901")

    assert result["12h_mode"] is True
    assert result["year"] == 0
    assert result["month"] == 1
    assert result["day"] == 1
    assert result["hour"] == 12
    assert result["minute"] == 0
    assert result["second"] == 0


@pytest.mark.asyncio
async def test_get_datetime_failure():
    device = create_device()
    device._send_command.return_value = bytes.fromhex("00")

    with pytest.raises(SwitchbotOperationError):
        await device.get_datetime()


@pytest.mark.asyncio
async def test_get_datetime_wrong_response():
    device = create_device()
    device._send_command.return_value = bytes.fromhex("0100")

    with pytest.raises(SwitchbotOperationError):
        await device.get_datetime()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "timestamp",
        "utc_offset_hours",
        "utc_offset_minutes",
        "expected_ts",
        "expected_utc",
        "expected_min",
    ),
    [
        (1709251200, 0, 0, "65e11a80", "0c", "00"),  # 2024-03-01T00:00:00+00:00
        (1709251200, 1, 0, "65e11a80", "0d", "00"),  # 2024-03-01T00:00:00+01:00
        (1709251200, 5, 45, "65e1250c", "11", "2d"),  # 2024-03-01T00:00:00+05:45
        (1709251200, -6, 15, "65e11e04", "06", "0f"),  # 2024-03-01T00:00:00-05:45
    ],
)
async def test_set_datetime(  # noqa: PLR0913
    *,
    timestamp: int,
    utc_offset_hours: int,
    utc_offset_minutes: int,
    expected_ts: str,
    expected_utc: str,
    expected_min: str,
):
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    await device.set_datetime(
        timestamp,
        utc_offset_hours=utc_offset_hours,
        utc_offset_minutes=utc_offset_minutes,
    )

    expected_ts = expected_ts.zfill(16)
    expected_payload = "57000503" + expected_utc + expected_ts + expected_min
    device._send_command.assert_called_with(expected_payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_hour",
    [-13, 15],
)
async def test_set_datetime_invalid_utc_offset_hours(bad_hour: int):
    device = create_device()
    with pytest.raises(SwitchbotOperationError):
        await device.set_datetime(1709251200, utc_offset_hours=bad_hour)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_min",
    [-1, 60],
)
async def test_set_datetime_invalid_utc_offset_minutes(bad_min: int):
    device = create_device()
    with pytest.raises(SwitchbotOperationError):
        await device.set_datetime(1709251200, utc_offset_minutes=bad_min)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_12h_mode", "expected_payload"),
    [
        (True, "80"),
        (False, "00"),
    ],
)
async def test_set_time_display_format(is_12h_mode: bool, expected_payload: str):
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    await device.set_time_display_format(is_12h_mode=is_12h_mode)
    device._send_command.assert_called_with("570f680505" + expected_payload)


@pytest.mark.asyncio
async def test_set_time_display_format_failure():
    device = create_device()
    device._send_command.return_value = bytes.fromhex("00")

    with pytest.raises(SwitchbotOperationError):
        await device.set_time_display_format(is_12h_mode=True)


@pytest.mark.asyncio
async def test_meter_pro_shares_datetime_protocol_with_co2_variant():
    """The plain Meter Pro (no CO2 sensor) uses the same BLE commands as the CO2 variant."""
    device = create_device(SwitchbotMeterPro)
    device._send_command.return_value = bytes.fromhex("01e40294230007e90c1e083701")

    result = await device.get_datetime()
    device._send_command.assert_called_with("570f6901")
    assert result["year"] == 2025

    device._send_command.return_value = bytes.fromhex("01")
    await device.set_datetime(1709251200)
    device._send_command.assert_called_with(
        "57000503" + "0c" + "65e11a80".zfill(16) + "00"
    )


@pytest.mark.asyncio
async def test_force_new_co2_measurement():
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    await device.force_new_co2_measurement()
    device._send_command.assert_called_with("570f680b04")


@pytest.mark.asyncio
async def test_calibrate_co2_sensor():
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    await device.calibrate_co2_sensor()
    device._send_command.assert_called_with("570f680b02")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("minutes", "expected_payload"),
    [
        (5, "012c"),
        (10, "0258"),
        (30, "0708"),
    ],
)
async def test_set_co2_update_interval(minutes: int, expected_payload: str):
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    await device.set_co2_update_interval(minutes * 60)
    device._send_command.assert_called_with("570f680b06" + expected_payload)


@pytest.mark.asyncio
async def test_set_co2_thresholds():
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    await device.set_co2_thresholds(lower=500, upper=1000)
    device._send_command.assert_called_with("570f6802030201f403e8")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lower", "upper", "expected_error"),
    [
        (600, 500, "Lower threshold should be smaller than upper threshold"),
        (300, 500, "Original App assumes that lower threshold is at least 500"),
        (600, 5000, "Original App assumes that upper threshold is at most 1900"),
    ],
)
async def test_set_co2_thresholds_throws_on_invalid_input(
    lower: int, upper: int, expected_error: str
):
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    # Error if lower >= upper
    with pytest.raises(
        SwitchbotOperationError,
        match=expected_error,
    ):
        await device.set_co2_thresholds(lower=lower, upper=upper)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_month_first", "expected_payload"),
    [
        (True, "00"),
        (False, "01"),
    ],
)
async def test_set_date_display_format(is_month_first: bool, expected_payload: str):
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    await device.set_date_display_format(is_month_first=is_month_first)
    device._send_command.assert_called_with("570f68070107" + expected_payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outdoor", "expected_payload"),
    [
        (True, "02"),
        (False, "01"),
    ],
)
async def test_set_display_data_source(outdoor: bool, expected_payload: str):
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    await device.set_display_data_source(outdoor=outdoor)
    device._send_command.assert_called_with("570f680701030f" + expected_payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change_unit", "change_data_source", "expected_payload"),
    [
        (True, True, "0001"),
        (True, False, "0000"),
        (False, True, "0101"),
        (False, False, "0100"),
    ],
)
async def test_set_button_function(
    change_unit: bool, change_data_source: bool, expected_payload: str
):
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    await device.set_button_function(change_unit, change_data_source)
    device._send_command.assert_called_with("570f68070106" + expected_payload)


@pytest.mark.asyncio
async def test_set_button_toggle_unit_and_data_source():
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    await device.set_button_toggle_unit(True)
    device._send_command.assert_called_with("570f680701060000")

    await device.set_button_toggle_data_source(True)
    device._send_command.assert_called_with("570f680701060001")

    await device.set_button_toggle_unit(False)
    device._send_command.assert_called_with("570f680701060101")

    await device.set_button_toggle_data_source(False)
    device._send_command.assert_called_with("570f680701060100")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("minutes", "expected_payload"),
    [
        (5, "012c"),
        (10, "0258"),
        (30, "0708"),
    ],
)
async def test_set_temperature_update_interval(minutes: int, expected_payload: str):
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    await device.set_temperature_update_interval(minutes * 60)
    device._send_command.assert_called_with("570f68070105" + expected_payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cold", "hot", "dry", "wet", "expected_payload"),
    [
        (10.0, 20.0, 40, 80, "9450008a28"),
        (-20.0, -10.0, 40, 80, "0a50001428"),
        (-20.0, 70, 40, 80, "c650001428"),
        (0.5, 22, 40, 82, "9652050028"),
        (0, 22, 40, 82, "9652000028"),
        (14, 37.5, 30, 70, "a546508e1e"),
    ],
)
async def test_set_comfortlevel(
    cold: float, hot: float, dry: int, wet: int, expected_payload: str
):
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    await device.set_comfortlevel(cold, hot, dry, wet)
    device._send_command.assert_called_with("570f68020188" + expected_payload)


@pytest.mark.asyncio
async def test_set_comfortlevel_throws_on_invalid_input():
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    with pytest.raises(ValueError, match="Cold should be smaller than Hot"):
        await device.set_comfortlevel(16, 15, 10, 20)

    with pytest.raises(ValueError, match="Dry should be smaller than Wet"):
        await device.set_comfortlevel(15, 16, 20, 10)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sound_on", "volume", "expected_payload"),
    [
        (False, 4, "0401"),
        (True, 2, "0202"),
        (True, 3, "0302"),
        (True, 4, "0402"),
    ],
)
async def test_set_alert_sound(sound_on: bool, volume: int, expected_payload: str):
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    await device.set_alert_sound(sound_on, volume)
    device._send_command.assert_called_with("570f680204" + expected_payload)


@pytest.mark.asyncio
async def test_set_alert_sound_invalid_volume():
    device = create_device()
    with pytest.raises(ValueError, match="Volume must be 2"):
        await device.set_alert_sound(True, 5)


@pytest.mark.asyncio
async def test_get_device_settings():
    device = create_device()
    device._send_command.return_value = bytes.fromhex(
        "01f1050000000000000000000000000708000001"
    )

    settings = await device.get_device_settings()
    device._send_command.assert_called_with("570f690701")
    assert settings["outdoor_mode"] is True
    assert settings["temperature_update_interval"] == 1800
    assert settings["external_sensor_mac_1"] is None
    assert settings["external_sensor_mac_2"] is None


@pytest.mark.asyncio
async def test_set_external_sensor_mac():
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    # Slot 1
    await device.set_external_sensor_mac("AA:BB:CC:DD:EE:FF", slot=1)
    device._send_command.assert_called_with("570f680701010000aabbccddeeff")

    # Slot 2
    await device.set_external_sensor_mac("11-22-33-44-55-66", slot=2)
    device._send_command.assert_called_with("570f680701020000112233445566")

    # Clear slot 1
    await device.set_external_sensor_mac("", slot=1)
    device._send_command.assert_called_with("570f680701010000000000000000")

    # Invalid slot
    with pytest.raises(ValueError, match="Slot must be 1 or 2"):
        await device.set_external_sensor_mac("AA:BB:CC:DD:EE:FF", slot=3)

    # Invalid MAC
    with pytest.raises(ValueError, match="Invalid MAC address format"):
        await device.set_external_sensor_mac("AA:BB", slot=1)


@pytest.mark.asyncio
async def test_show_battery_level():
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    await device.show_battery_level(True)
    device._send_command.assert_called_with("570f6807010801")

    await device.show_battery_level(False)
    device._send_command.assert_called_with("570f6807010800")


@pytest.mark.asyncio
async def test_get_temperature_humidity_offsets():
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01808101")

    offsets = await device.get_temperature_humidity_offsets()
    device._send_command.assert_called_with("570f6903")
    assert offsets["temperature_offset"] == 0.1
    assert offsets["humidity_offset"] == 1


@pytest.mark.asyncio
async def test_set_temperature_humidity_offsets():
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    # Temp +0.2 C, Humidity +1% (matches recording2.pklg: 570f6803808102)
    await device.set_temperature_humidity_offsets(
        temperature_offset=0.2, humidity_offset=1
    )
    device._send_command.assert_called_with("570f6803808102")

    # Temp 0.0 C, Humidity +2% (matches recording2.pklg: 570f6803008200)
    await device.set_temperature_humidity_offsets(
        temperature_offset=0.0, humidity_offset=2
    )
    device._send_command.assert_called_with("570f6803008200")

    # Zero offsets (matches recording2.pklg: 570f6803000000)
    await device.set_temperature_humidity_offsets(
        temperature_offset=0.0, humidity_offset=0
    )
    device._send_command.assert_called_with("570f6803000000")

    # Multi-degree offset (+1.5 C, 0%)
    await device.set_temperature_humidity_offsets(
        temperature_offset=1.5, humidity_offset=0
    )
    device._send_command.assert_called_with("570f6803810005")

    # Negative offsets (-1.2 C, -5%)
    await device.set_temperature_humidity_offsets(
        temperature_offset=-1.2, humidity_offset=-5
    )
    device._send_command.assert_called_with("570f6803010502")


@pytest.mark.asyncio
async def test_get_co2_update_interval():
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01012c")

    interval = await device.get_co2_update_interval()
    device._send_command.assert_called_with("570f690b04")
    assert interval == 300


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("on", "co2_low", "co2_high", "reverse", "expected_payload"),
    [
        (False, 1000, 2000, False, "0007d003e8"),
        (True, 1000, 2000, False, "0307d003e8"),
        (True, 700, 2000, False, "0307d002bc"),
        (True, 700, 1500, False, "0305dc02bc"),
        (True, 700, 1500, True, "0405dc02bc"),
    ],
)
async def test_set_alert_co2(
    on: bool, co2_low: int, co2_high: int, reverse: bool, expected_payload: str
):
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    await device.set_alert_co2(on, co2_low, co2_high, reverse)
    device._send_command.assert_called_with("570f68020301" + expected_payload)


@pytest.mark.asyncio
async def test_set_alert_co2_throws_on_invalid_input():
    device = create_device()
    with pytest.raises(
        ValueError,
        match=r"Upper value should bigger than the lower value. Do you want to use reverse instead\?",
    ):
        await device.set_alert_co2(True, 500, 400, True)


@pytest.mark.asyncio
async def test_set_alert_temperature_humidity():
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    await device.set_alert_temperature_humidity(
        temperature_alert=True,
        temperature_low=10.0,
        temperature_high=30.0,
        temperature_reverse=False,
        humidity_alert=True,
        humidity_low=30,
        humidity_high=70,
        humidity_reverse=False,
    )
    # Checks command was dispatched with 570f44 header
    called_cmd = device._send_command.call_args[0][0]
    assert called_cmd.startswith("570f44")


def test_parse_history_chunk():
    from switchbot.devices.meter_pro import parse_history_chunk

    chunk = bytes.fromhex("953599953501980198953599953501980198")
    records = parse_history_chunk(chunk, start_ts=1000, step_seconds=120, base_index=0)
    assert len(records) == 4
    assert records[0] == {
        "index": 0,
        "timestamp": 1000,
        "temperature": 21.9,
        "humidity": 53,
        "co2": 408,
    }
    assert records[1] == {
        "index": 1,
        "timestamp": 1120,
        "temperature": 21.9,
        "humidity": 53,
        "co2": 408,
    }
    assert records[2] == {
        "index": 2,
        "timestamp": 1240,
        "temperature": 21.9,
        "humidity": 53,
        "co2": 408,
    }
    assert records[3] == {
        "index": 3,
        "timestamp": 1360,
        "temperature": 21.9,
        "humidity": 53,
        "co2": 408,
    }


@pytest.mark.asyncio
async def test_get_history():
    device = create_device()
    # Mock responses for init, info, range, page
    device._send_command.side_effect = [
        bytes.fromhex("01"),  # init
        bytes.fromhex("01510100040302"),  # info
        bytes.fromhex("016aa2108e6ad76d56000000040078"),  # range: 4 records, 120s step
        bytes.fromhex("01953599953501980198953599953501980198"),  # page chunk
    ]

    history = await device.get_history(max_records=4, stop_timestamp=1792503126)
    assert len(history) == 4
    assert history[0]["temperature"] == 21.9
    assert history[0]["humidity"] == 53
    assert history[0]["co2"] == 408


@pytest.mark.asyncio
async def test_get_basic_info():
    device = create_device()
    # 01 (status), 64 (100% battery), 15 (v2.1 firmware = 21)
    device._send_command.return_value = bytes.fromhex("016415")

    info = await device.get_basic_info()
    device._send_command.assert_called_with(key="5702", retry=device._retry_count)
    assert info["battery"] == 100
    assert info["firmware"] == 2.1


@pytest.mark.asyncio
async def test_refresh_measurements():
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    await device.refresh_measurements()
    device._send_command.assert_called_with("5701")


@pytest.mark.asyncio
async def test_get_display_data_meter_pro():
    device = create_device()
    # 01 (status), 64 (100% battery, no Type-C), 05 (comfort=0, dec=5), 97 (+23 C), 36 (humidity 54%),
    # 01 (24h, sunny), 07ea (2026), 0a (Oct), 16 (22), 0a (10), 1e (30), 00 (00 sec)
    device._send_command.return_value = bytes.fromhex(
        "01640597360107ea0a160a1e00"
    )

    data = await device.get_display_data()
    device._send_command.assert_called_with("570f6901")
    assert data["battery"] == 100
    assert data["power_type_c"] is False
    assert data["comfort_status"] == "comfort"
    assert data["temperature"] == 23.5
    assert data["humidity"] == 54
    assert data["year"] == 2026
    assert data["month"] == 10
    assert data["day"] == 22


@pytest.mark.asyncio
async def test_get_display_data_meter_pro_co2():
    device = create_device()
    # 20 bytes full payload:
    # 01, e4 (Type-C plugged + 100%), 12 (dry=1, dec=2), 95 (+21 C), 2c (44%),
    # 82 (12h, partly cloudy), 07ea (2026), 0a (10), 16 (22), 0e (14), 2d (45), 15 (21s),
    # 0352 (850 ppm), 00 (good), 05 (5 min interval), 03 (ext dec=3), 8e (+14 C), 52 (82%)
    device._send_command.return_value = bytes.fromhex(
        "01e412952c8207ea0a160e2d1503520005038e52"
    )

    data = await device.get_display_data()
    assert data["power_type_c"] is True
    assert data["battery"] == 100
    assert data["comfort_status"] == "dry"
    assert data["temperature"] == 21.2
    assert data["humidity"] == 44
    assert data["co2"] == 850
    assert data["co2_level"] == "good"
    assert data["co2_update_interval_min"] == 5
    assert data["secondary_temperature"] == 14.3
    assert data["secondary_humidity"] == 82


@pytest.mark.asyncio
async def test_get_button_function():
    device = create_device()
    # 01 (status), 00 (unit enabled), 01 (source enabled)
    device._send_command.return_value = bytes.fromhex("010001")

    btn = await device.get_button_function()
    device._send_command.assert_called_with("570f690704")
    assert btn["change_unit"] is True
    assert btn["change_data_source"] is True


@pytest.mark.asyncio
async def test_set_button_function_preserve():
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    # Preserve unit toggle, set source toggle to True
    await device.set_button_function(change_unit=None, change_data_source=True)
    device._send_command.assert_called_with("570f68070106ff01")

    # Set unit toggle to False, preserve source toggle
    await device.set_button_function(change_unit=False, change_data_source=None)
    device._send_command.assert_called_with("570f6807010601ff")


@pytest.mark.asyncio
async def test_alarm_management():
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    # Silence alarm: mute + stop flash
    await device.silence_alarm(mute=True, stop_flash=True)
    device._send_command.assert_called_with("570f68020501")

    # Silence alarm: mute only
    await device.silence_alarm(mute=True, stop_flash=False)
    device._send_command.assert_called_with("570f68020502")

    # Clear alarm state
    await device.clear_alarm_state()
    device._send_command.assert_called_with("570f680206")

    # Get active alarm status (01: beep + flash active)
    device._send_command.return_value = bytes.fromhex("0101")
    status = await device.get_active_alarm_status()
    device._send_command.assert_called_with("570f690205")
    assert status["buzzer_active"] is True
    assert status["flash_active"] is True


@pytest.mark.asyncio
async def test_get_alert_sound():
    device = create_device()
    # 01, 03 (medium vol), 02 (sound + flash)
    device._send_command.return_value = bytes.fromhex("010302")

    sound = await device.get_alert_sound()
    device._send_command.assert_called_with("570f690204")
    assert sound["volume"] == 3
    assert sound["sound_on"] is True
    assert sound["flash_on"] is True


@pytest.mark.asyncio
async def test_get_power_status():
    device = create_device()
    # Plugged in (Type-C)
    device._send_command.return_value = bytes.fromhex("0101")
    pwr = await device.get_power_status()
    device._send_command.assert_called_with("570f690b02")
    assert pwr["plugged_in"] is True

    # Battery
    device._send_command.return_value = bytes.fromhex("0100")
    pwr = await device.get_power_status()
    assert pwr["plugged_in"] is False


@pytest.mark.asyncio
async def test_co2_intervals_and_thresholds():
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    # Plugged-in interval write
    await device.set_co2_update_interval_plugged(60)
    device._send_command.assert_called_with("570f680b01003c")

    # Plugged-in interval read
    device._send_command.return_value = bytes.fromhex("01003c")
    val = await device.get_co2_update_interval_plugged()
    device._send_command.assert_called_with("570f690b03")
    assert val == 60

    # CO2 report threshold write & read
    device._send_command.return_value = bytes.fromhex("01")
    await device.set_co2_report_threshold(50)
    device._send_command.assert_called_with("570f680b050032")

    device._send_command.return_value = bytes.fromhex("010032")
    val_th = await device.get_co2_report_threshold()
    device._send_command.assert_called_with("570f69020303")
    assert val_th == 50

    # Read CO2 air quality boundary thresholds
    device._send_command.return_value = bytes.fromhex("01025804b0")  # 600, 1200
    th = await device.get_co2_thresholds()
    device._send_command.assert_called_with("570f69020302")
    assert th["lower"] == 600
    assert th["upper"] == 1200

    # Read CO2 alarm info
    device._send_command.return_value = bytes.fromhex("010307d003e800")
    alarm_info = await device.get_co2_alarm_info()
    device._send_command.assert_called_with("570f69020301")
    assert alarm_info["enabled"] is True
    assert alarm_info["co2_high"] == 2000
    assert alarm_info["co2_low"] == 1000
    assert alarm_info["alarm_state"] == 0


@pytest.mark.asyncio
async def test_factory_temp_unit_and_erase():
    device = create_device()
    device._send_command.return_value = bytes.fromhex("01")

    await device.set_factory_temperature_unit(fahrenheit=True)
    device._send_command.assert_called_with("570f6807010901")

    await device.set_factory_temperature_unit(fahrenheit=False)
    device._send_command.assert_called_with("570f6807010900")

    await device.erase_history()
    device._send_command.assert_called_with("57000402000101")


@pytest.mark.asyncio
async def test_get_co2_data():
    device = create_device()
    # Raw frame: 01 0195 00 04b0 0190 03e8 0578 0708
    device._send_command.return_value = bytes.fromhex("0101950004b0019003e805780708")

    data = await device.get_co2_data()
    device._send_command.assert_called_with("570f69020300")
    assert data["co2"] == 405
    assert data["alarm_enabled"] is False
    assert data["co2_high"] == 1200
    assert data["co2_low"] == 400
    assert data["quality_green_yellow"] == 1000
    assert data["quality_yellow_red"] == 1400
    assert data["store_interval"] == 1800


@pytest.mark.asyncio
async def test_refresh_measurements_accepts_05():
    device = create_device()
    device._send_command.return_value = bytes.fromhex("05")
    await device.refresh_measurements()
    device._send_command.assert_called_with("5701")


def test_process_wosensorth_c():
    from switchbot.adv_parsers.meter import process_wosensorth_c

    # mfr_data: MAC (6) + seq (1) + battery/power (1: 0x80|50 = 0xb2) + temp (3) + extra (2) + co2 (2: 0x03e8 = 1000)
    mfr_data = bytes.fromhex("b0e9fe576185fa b2 08943a 001a 03e8".replace(" ", ""))
    parsed = process_wosensorth_c(None, mfr_data)
    assert parsed["co2"] == 1000
    assert parsed["power_type_c"] is True
    assert parsed["battery"] == 50
    assert parsed["temperature"] == 20.8
    assert parsed["humidity"] == 58
