from enum import Enum


class MeterProWeatherIcon(Enum):
    """Weather icons supported by the Meter Pro CO2 display."""

    NONE = 0
    SUNNY = 1
    PARTLY_CLOUDY = 2
    CLOUDY = 3
    RAINY = 4
    POURING = 5
    SNOWY = 6
    HEAVY_SNOW = 7


class MeterProDisplaySource(Enum):
    """Data sources for a Meter Pro display slot."""

    NONE = 0x00
    OUTDOOR_METER = 0x03
    SENSOR = 0x05
    LOCAL_WEATHER = 0x06
