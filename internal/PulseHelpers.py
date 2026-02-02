import enum
import pulsectl
from loguru import logger as log
from gi.repository import Gio, GLib

from GtkHelper.ComboRow import SimpleComboRowItem


# --- MPRIS / DBUS HELPERS (Replaces Playerctl) ---
class MprisPlayer:
    """
    Optimized wrapper for DBus Music Players.
    Uses local caching and async calls for instant UI feedback.
    """

    def __init__(self, bus_name):
        self.bus_name = bus_name
        self.name = bus_name.replace("org.mpris.MediaPlayer2.", "")

        self.proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            None,
            bus_name,
            "/org/mpris/MediaPlayer2",
            "org.mpris.MediaPlayer2.Player",
            None
        )

        # Local cache for instant feedback
        self._cached_volume = 0.0
        self._refresh_cache()

    def _refresh_cache(self):
        """Reads real volume from proxy into local cache."""
        try:
            v = self.proxy.get_cached_property("Volume")
            if v:
                self._cached_volume = v.get_double()
        except Exception:
            pass

    @property
    def volume(self):
        """Always return the local cache for speed."""
        return self._cached_volume

    def set_volume(self, value):
        """Sets volume instantly in cache, then sends async DBus command."""
        # 1. Optimistic Update: Update local value immediately
        self._cached_volume = value

        # 2. Async Call: Send to DBus without blocking the UI thread
        try:
            self.proxy.call(
                "org.freedesktop.DBus.Properties.Set",
                GLib.Variant("(ssv)", (
                    "org.mpris.MediaPlayer2.Player",
                    "Volume",
                    GLib.Variant("d", value)
                )),
                Gio.DBusCallFlags.NONE,
                -1,
                None,  # Cancellable
                None,  # Callback (we don't wait for it)
                None  # User data
            )
        except Exception as e:
            log.warning(f"Failed to set volume for {self.name}: {e}")


# -------------------------------------------------

class DeviceFilter(enum.Enum):
    SINK = SimpleComboRowItem("sink", "Sink")
    SOURCE = SimpleComboRowItem("source", "Source")
    SINK_INPUT = SimpleComboRowItem("sink-input", "Application")
    # Add Music Filter
    MUSIC = SimpleComboRowItem("music", "Music Player")

    def get_value(self):
        return self.value.get_value()


def _filter_value(filter_obj):
    # Normalize filter to its underlying string value
    if isinstance(filter_obj, DeviceFilter):
        return filter_obj.get_value()
    try:
        return filter_obj.get_value()
    except AttributeError:
        return filter_obj


def filter_proplist(proplist) -> str | None:
    # Existing filter logic...
    filters: list[str] = [
        "application.name",
        "alsa.card_name",
        "alsa.long_card_name",
        "node.name",
        "node.nick",
        "device.name",
        "device.nick",
        "device.description",
        "device.serial"
    ]
    # ... (Keep the rest of your existing filter_proplist function here) ...
    weights: list[(str, int)] = [
        ('.', -50),
        ('_', -10),
        (':', -25),
        (';', -100),
        ('-', -5)
    ]
    length_weight: int = -5
    minimal_weights: list[(int, str)] = []
    for filter in filters:
        out: str = proplist.get(filter)
        if out is None or len(out) < 3:
            continue
        current_weight: int = 0
        current_weight += sum(out.count(weight[0]) * weight[1] for weight in weights)
        current_weight += (len(out) * length_weight)
        minimal_weights.append((current_weight, out))
    minimal_weights.sort(key=lambda x: x[0], reverse=True)
    if len(minimal_weights) > 0:
        return minimal_weights[0][1] or None
    return None


def get_device(filter: DeviceFilter, identifier, fallback_name=None, fallback_index=None, fallback_proc=None, fallback_media=None,
               fallback_node=None):
    """
    Returns a Pulse object OR an MprisPlayer object depending on filter.
    """
    filter_value = _filter_value(filter)

    # 1. Handle Music Players via DBus (Gio)
    if filter_value == DeviceFilter.MUSIC.get_value():
        try:
            # We reconstruct the full bus name from the identifier (e.g. "spotify")
            full_name = f"org.mpris.MediaPlayer2.{identifier}"
            return MprisPlayer(full_name)
        except Exception as e:
            log.error(f"Could not create player from name {identifier}: {e}")
        return None

    # 2. Handle Standard Pulse Devices
    with pulsectl.Pulse("device-getter") as pulse:
        try:
            device = None
            if filter_value == DeviceFilter.SINK.get_value():
                device = pulse.get_sink_by_name(identifier)
            elif filter_value == DeviceFilter.SOURCE.get_value():
                device = pulse.get_source_by_name(identifier)
            elif filter_value == DeviceFilter.SINK_INPUT.get_value():
                # Prefer strict matching on process/app names to avoid grabbing the wrong stream
                best_candidate = None
                best_rank = -1
                fingerprints_provided = bool(fallback_proc or fallback_media or fallback_node or (isinstance(identifier, str) and '|' in identifier))
                id_proc = None
                id_media = None
                id_node = None
                if isinstance(identifier, str) and '|' in identifier:
                    parts = identifier.split('|', 1)
                    id_proc, id_node = parts[0], parts[1]

                for sink_input in pulse.sink_input_list():
                    proc_bin = sink_input.proplist.get('application.process.binary')
                    app_name = sink_input.proplist.get('application.name')
                    media_name = sink_input.proplist.get('media.name')
                    node_name = sink_input.proplist.get('node.name')
                    idx_str = str(sink_input.index)

                    rank = None
                    # Match on explicit proc|node fingerprint if available
                    if id_proc and proc_bin and id_proc == proc_bin and id_node and node_name and id_node == node_name:
                        rank = 6
                    elif id_proc and proc_bin and id_proc == proc_bin:
                        rank = 5
                    elif fallback_node and node_name and fallback_node == node_name and fallback_proc and proc_bin == fallback_proc:
                        rank = 5
                    elif identifier == proc_bin or fallback_proc == proc_bin:
                        rank = 4
                    elif identifier == app_name or fallback_name == app_name:
                        rank = 3
                    elif identifier == media_name or fallback_media == media_name or (id_media and media_name == id_media):
                        rank = 2
                    elif identifier == idx_str or (fallback_name and fallback_name == idx_str):
                        rank = 1

                    if rank is not None and rank > best_rank:
                        best_candidate = sink_input
                        best_rank = rank
                        if rank >= 6:
                            break

                if best_candidate:
                    # If we had fingerprints, require at least node/media/app match (>=2) to accept
                    if fingerprints_provided and best_rank < 2:
                        device = None
                    else:
                        device = best_candidate

                # If we have fingerprints but no suitable match, avoid numeric fallback
                allow_numeric_fallback = not fingerprints_provided

                if device is None and allow_numeric_fallback and str(identifier).isdigit():
                    try:
                        device = pulse.sink_input_info(int(identifier))
                    except Exception:
                        device = None
                if device is None and allow_numeric_fallback and fallback_index is not None:
                    try:
                        device = pulse.sink_input_info(int(fallback_index))
                    except Exception:
                        device = None
            return device
        except Exception as e:
            log.error(f"Error while getting device: {identifier} with filter: {filter}. Error: {e}")
    return None


def get_volumes_from_device(device_filter: DeviceFilter, identifier: str, fallback_name: str | None = None, fallback_index: int | None = None,
                            fallback_proc: str | None = None, fallback_media: str | None = None, fallback_node: str | None = None):
    try:
        device = get_device(device_filter, identifier, fallback_name, fallback_index, fallback_proc, fallback_media, fallback_node)

        if device is None:
            return []

        # Handle MprisPlayer
        if isinstance(device, MprisPlayer):
            # This now reads from our fast local variable
            return [round(device.volume * 100)]

        # Standard PulseAudio Logic
        device_volumes = device.volume.values
        return [round(vol * 100) for vol in device_volumes]

    except Exception as e:
        log.error(f"Error while getting volumes from device: {identifier} with filter: {device_filter}. Error: {e}")
        return []


def change_volume(device, adjust):
    if device is None:
        log.error("change_volume called with no device")
        return

    # Check if it is our custom MprisPlayer
    if isinstance(device, MprisPlayer):
        try:
            # Convert integer adjust (e.g. 5) to float (0.05)
            current = device.volume
            new_vol = max(0.0, min(1.0, current + (adjust / 100.0)))
            device.set_volume(new_vol)
        except Exception as e:
            log.error(f"Error changing player volume: {e}")
    else:
        # PulseAudio logic
        with pulsectl.Pulse("change-volume") as pulse:
            try:
                pulse.volume_change_all_chans(device, adjust * 0.01)
            except Exception as e:
                log.error(f"Error changing pulse volume: {e}")


def set_volume(device, volume):
    if device is None:
        log.error("set_volume called with no device")
        return

    # Check if it is our custom MprisPlayer
    if isinstance(device, MprisPlayer):
        try:
            # Volume is 0-100 coming in, needs 0.0-1.0
            device.set_volume(volume / 100.0)
        except Exception as e:
            log.error(f"Error setting player volume: {e}")
    else:
        with pulsectl.Pulse("change-volume") as pulse:
            try:
                pulse.volume_set_all_chans(device, volume * 0.01)
            except Exception as e:
                log.error(f"Error setting pulse volume: {e}")


def mute(device, state):
    with pulsectl.Pulse("change-volume") as pulse:
        try:
            pulse.mute(device, state)
        except Exception as e:
            log.error(f"Error muting: {e}")


def set_default_device(device_filter, pulse_device_name):
    # ... existing code ...
    pass


def get_standard_device(device_filter):
    # ... existing code ...
    pass


def get_device_list(filter: DeviceFilter):
    # 1. List Music Players (Via DBus / Gio)
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
                    players.append(MprisPlayer(name))
        except Exception as e:
            log.error(f"Error listing DBus players: {e}")
        return players

    # 2. List Pulse Devices
    with pulsectl.Pulse("device-list-getter") as pulse:
        switch = {
            DeviceFilter.SINK.get_value(): pulse.sink_list(),
            DeviceFilter.SOURCE.get_value(): pulse.source_list(),
            DeviceFilter.SINK_INPUT.get_value(): pulse.sink_input_list(),
        }
        return switch.get(filter.get_value(), {})