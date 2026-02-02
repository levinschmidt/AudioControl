import enum
import pulsectl
from loguru import logger as log
from GtkHelper.ComboRow import SimpleComboRowItem

# --- NEW IMPORTS FOR PLAYERCTL ---
try:
    import gi

    gi.require_version('Playerctl', '2.0')
    from gi.repository import Playerctl
except ImportError:
    log.error("Playerctl or PyGObject not found. Music control will not work.")
    Playerctl = None
except ValueError:
    log.error("Playerctl version 2.0 not found.")
    Playerctl = None


# ---------------------------------

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


# --- HELPER CLASS FOR PLAYERS ---
class PlayerWrapper:
    """Makes a Playerctl name look like a Pulse device for compatibility"""

    def __init__(self, name):
        self.name = name
        self.index = 0  # Not used for players
        # Create a fake proplist so filter_proplist can find a name
        self.proplist = {"application.name": name.capitalize(), "device.description": name}


# --------------------------------

def get_device(filter: DeviceFilter, identifier, fallback_name=None, fallback_index=None, fallback_proc=None, fallback_media=None):
    """
    Returns a Pulse object OR a Playerctl.Player object depending on filter.
    """
    filter_value = _filter_value(filter)
    # 1. Handle Music Players via Playerctl
    if filter_value == DeviceFilter.MUSIC.get_value():
        if Playerctl:
            try:
                # FIX: 'new_from_name' requires a PlayerName object, not a string.
                # We iterate through current players to find the matching object.
                for name_obj in Playerctl.list_players():
                    if name_obj.name == identifier:
                        return Playerctl.Player.new_from_name(name_obj)
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
                for sink_input in pulse.sink_input_list():
                    proc_bin = sink_input.proplist.get('application.process.binary')
                    app_name = sink_input.proplist.get('application.name')
                    media_name = sink_input.proplist.get('media.name')
                    idx_str = str(sink_input.index)

                    rank = None
                    if identifier == proc_bin or fallback_proc == proc_bin:
                        rank = 4
                    elif identifier == app_name or fallback_name == app_name:
                        rank = 3
                    elif identifier == media_name or fallback_media == media_name:
                        rank = 2
                    elif identifier == idx_str or (fallback_name and fallback_name == idx_str):
                        rank = 1

                    if rank is not None:
                        candidate = (rank, sink_input)
                        if best_candidate is None or candidate[0] > best_candidate[0]:
                            best_candidate = candidate
                        if rank >= 4:
                            break

                if best_candidate:
                    device = best_candidate[1]

                # If we have fingerprints but no match, avoid picking a random numeric index
                allow_numeric_fallback = not (fallback_proc or fallback_media)

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


def get_volumes_from_device(device_filter: DeviceFilter, identifier: str, fallback_name: str | None = None, fallback_index: int | None = None, fallback_proc: str | None = None, fallback_media: str | None = None):
    try:
        device = get_device(device_filter, identifier, fallback_name, fallback_index, fallback_proc, fallback_media)

        # --- FIX: Safety Check ---
        if device is None:
            return []
        # -------------------------

        # Check for Playerctl (if you kept the music player code)
        # If you removed playerctl, you can delete this 'if' block and just keep the 'else' logic
        if 'Playerctl' in globals() and Playerctl and isinstance(device, Playerctl.Player):
            return [round(device.props.volume * 100)]

        # Standard PulseAudio Logic
        device_volumes = device.volume.values
        return [round(vol * 100) for vol in device_volumes]

    except Exception as e:
        log.error(f"Error while getting volumes from device: {identifier} with filter: {device_filter}. Error: {e}")
        return []


def change_volume(device, adjust):
    # Helper: Check if it is a Playerctl object
    is_player = Playerctl and isinstance(device, Playerctl.Player)

    if device is None:
        log.error("change_volume called with no device")
        return

    if is_player:
        try:
            # Convert integer adjust (e.g. 5) to float (0.05)
            current = device.props.volume
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
    is_player = Playerctl and isinstance(device, Playerctl.Player)

    if device is None:
        log.error("set_volume called with no device")
        return

    if is_player:
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


# ... (Keep mute, set_default_device, get_standard_device as they were) ...
def mute(device, state):
    # (If you want playerctl mute support, add it here too, otherwise keep existing)
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
    if filter.get_value() == DeviceFilter.MUSIC.get_value():
        if not Playerctl:
            return []
        try:
            player_names = Playerctl.list_players()
            return [PlayerWrapper(name.name) for name in player_names]
        except Exception as e:
            log.error(f"Error listing players: {e}")
            return []

    with pulsectl.Pulse("device-list-getter") as pulse:
        switch = {
            DeviceFilter.SINK.get_value(): pulse.sink_list(),
            DeviceFilter.SOURCE.get_value(): pulse.source_list(),
            DeviceFilter.SINK_INPUT.get_value(): pulse.sink_input_list(),
        }
        return switch.get(filter.get_value(), {})
