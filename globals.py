from enum import StrEnum

class Icons(StrEnum):
    MAIN = "main-icon"
    MUTED = "mute"
    UNMUTED = "audio"
    VOLUME_UP = "vol-up"
    VOLUME_DOWN = "vol-down"
    SPEAKER_DEFAULT = "speaker-default"
    HEADPHONE_DEFAULT = "headphone-default"
    NONE_DEFAULT = "none-default"

class Colors(StrEnum):
    VOLUME_OK = "volume-ok"
    VOLUME_WARNING = "volume-warning"

class GameFilter:
    blacklist = [
        # GTA V Enhanced
        "SocialClubHelper.exe",
        "Launcher.exe",
        "Rockstar Games Launcher",
        "GTA5_Enhanced.exe",
        # Deep Rock Galactic
        "FSD-Win64-Shipping.exe",
        # Rocket League
        "RocketLeague.exe"
    ]

    whitelist = [

    ]

    binary =[
        "wine"
    ]

    # Automatically mute game with following application names
    auto_mute = [
    ]