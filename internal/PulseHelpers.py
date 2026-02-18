import enum

import pulsectl
from loguru import logger as log
from gi.repository import Gio, GLib

from GtkHelper.ComboRow import SimpleComboRowItem


class Modes(enum.Enum):
    APPLICATION = SimpleComboRowItem("application", "Application")
    OUTPUT = SimpleComboRowItem("output", "Output")
    OUTPUT_DEFAULT = SimpleComboRowItem("output_default", "Default Output")
    MUSIC = SimpleComboRowItem("music", "Music")
    GAME = SimpleComboRowItem("game", "Game")

    def get_value(self):
        return self.value.get_value()

def get_default_output():
    with pulsectl.Pulse("restore-volume-getter") as pulse:
        try:
            default_sink_name = pulse.server_info().default_sink_name
            saved_entries = pulse.sink_list()
            for sink in saved_entries:
                if sink.name == default_sink_name:
                    return sink
        except Exception as e:
            log.error(f"Error while getting default output device with filter: Error: {e}")
    return None

def get_output(core, node_name):
    #with pulsectl.Pulse("restore-volume-getter") as pulse:
    try:
        saved_entries = core.pulse_client.sink_list()
        for sink in saved_entries:
            if sink.name == node_name:
                return sink
    except Exception as e:
        log.error(f"Error while getting device with node_name: {node_name} with filter: Error: {e}")
    return None

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


def get_sinks_list(core, mode: Modes):
    if mode.get_value() == Modes.MUSIC.get_value():
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

    elif mode.get_value() == Modes.APPLICATION.get_value() or mode.get_value() == Modes.GAME.get_value():
        return core.pulse_client.sink_input_list()

    elif mode.get_value() == Modes.OUTPUT.get_value():
        return core.pulse_client.sink_list()

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

def get_volume_from_output(core):
    try:
        volume = core.selected_output_device.sink.volume.value_flat
        return round(volume * 100)
    except Exception as e:
        log.error(f"Error while getting volume from output: {core.selected_output_device.sink.node_name}. Error: {e}")
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

def set_volume_output(core, sink, volume):
    with pulsectl.Pulse("volume-setter") as pulse:
        try:
            pulse.volume_set_all_chans(sink, volume * 0.01)
        except Exception as e:
            log.error(f"Error while setting volume on device with node_name: {sink.name}, volume is {volume}. Error: {e}")

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