import enum

import pulsectl
from loguru import logger as log
from gi.repository import Gio, GLib

from GtkHelper.ComboRow import SimpleComboRowItem


class DeviceFilter(enum.Enum):
    MUSIC = SimpleComboRowItem("music", "Music")
    APPLICATION = SimpleComboRowItem("application", "Application")
    GAME = SimpleComboRowItem("game", "Game")

    def get_value(self):
        return self.value.get_value()

def get_application(restore_id):
    with pulsectl.Pulse("restore-volume-getter") as pulse:
        try:
            saved_entries = pulse.sink_input_list()
            for stream in saved_entries:
                stream_id = stream.proplist.get('module-stream-restore.id')
                if stream_id == restore_id:
                    return stream
        except Exception as e:
            log.error(f"Error while getting device with restore_id: {restore_id} with filter: Error: {e}")
    return None


def get_application_list(filter: DeviceFilter):
    if filter.get_value() == DeviceFilter.MUSIC.get_value():
        players = []
        try:
            # Connect to DBus and list all names
            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            result = conn.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "ListNames",
                None,
                GLib.VariantType("(as)"),
                Gio.DBusCallFlags.NONE,
                -1,
                None
            )
            names = result.unpack()[0]
            # Filter for media players
            for name in names:
                if name.startswith("org.mpris.MediaPlayer2."):
                    players.append(name)
        except Exception as e:
            log.error(f"Error listing DBus players: {e}")
        return players

    elif filter.get_value() == DeviceFilter.APPLICATION.get_value():
        return pulsectl.Pulse("app-list-getter").sink_input_list()

    else:
        return []

def get_volume_from_music_player(player_bus_name: str):
    try:
        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        proxy = Gio.DBusProxy.new_sync(
            conn,
            Gio.DBusProxyFlags.NONE,
            None,
            player_bus_name,
            "/org/mpris/MediaPlayer2",
            "org.mpris.MediaPlayer2.Player",
            None
        )
        volume_variant = proxy.get_cached_property("Volume")
        if volume_variant is not None:
            volume = volume_variant.get_double()
            return round(volume * 100)
    except Exception as e:
        log.error(f"Error while getting volume from music player: {player_bus_name}. Error: {e}")
    return None

def get_volume_from_application(restore_id):
    device = get_application(restore_id)
    if device is None:
        return None
    try:
        volume = device.volume.value_flat
        return round(volume * 100)
    except Exception as e:
        log.error(f"Error while getting volumes from device: {device.name}. Error: {e}")
        return None

def set_volume_music_player(player , volume: int):
    try:
        player.proxy.call(
            "org.freedesktop.DBus.Properties.Set",
            GLib.Variant("(ssv)", (
                "org.mpris.MediaPlayer2.Player",
                "Volume",
                GLib.Variant("d", volume * 0.01)
            )),
            Gio.DBusCallFlags.NONE,
            -1,
            None,  # Cancellable
            None,  # Callback (we don't wait for it)
            None  # User data
        )
    except Exception as e:
        log.warning(f"Failed to set volume for {player.name}: {e}")

def set_volume_application(stream, volume):
      with pulsectl.Pulse("change-volume") as pulse:
        try:
            pulse.volume_set_all_chans(stream, volume * 0.01)
        except Exception as e:
            log.error(f"Error while setting volume on device with restore_id: {stream.name}, volume is {volume}. Error: {e}")

def mute(device, state):
    with pulsectl.Pulse("change-volume") as pulse:
        try:
            pulse.mute(device, state)
        except Exception as e:
            log.error(f"Error while muting device: {device.name}, state is {state}. Error: {e}")