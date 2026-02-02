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

def get_device(filter: DeviceFilter, identifier):
    """
    Returns a Pulse object OR a Playerctl.Player object depending on filter.
    """
    # 1. Handle Music Players via Playerctl
    if filter == DeviceFilter.MUSIC.get_value():
        if Playerctl:
            try:
                # 'identifier' here will be the player name (e.g., 'spotify')
                player = Playerctl.Player.new_from_name(identifier)
                return player
            except Exception as e:
                log.error(f"Could not create player from name {identifier}: {e}")
        return None

    # 2. Handle Standard Pulse Devices
    with pulsectl.Pulse("device-getter") as pulse:
        try:
            device = None
            if filter == DeviceFilter.SINK.get_value():
                device = pulse.get_sink_by_name(identifier)
            elif filter == DeviceFilter.SOURCE.get_value():
                device = pulse.get_source_by_name(identifier)
            elif filter == DeviceFilter.SINK_INPUT.get_value():
                # identifier is a string ID, convert to int
                device = pulse.sink_input_info(int(identifier))
            return device
        except Exception as e:
            log.error(f"Error while getting device: {identifier} with filter: {filter}. Error: {e}")
    return None


def get_device_list(filter: DeviceFilter):
    # 1. List Music Players
    if filter.get_value() == DeviceFilter.MUSIC.get_value():
        if not Playerctl:
            return []
        try:
            player_names = Playerctl.list_players()
            # Wrap them so they look like Pulse devices to the rest of the app
            return [PlayerWrapper(name.name) for name in player_names]
        except Exception as e:
            log.error(f"Error listing players: {e}")
            return []

    # 2. List Pulse Devices
    with pulsectl.Pulse("device-list-getter") as pulse:
        switch = {
            DeviceFilter.SINK.get_value(): pulse.sink_list(),
            DeviceFilter.SOURCE.get_value(): pulse.source_list(),
            DeviceFilter.SINK_INPUT.get_value(): pulse.sink_input_list(),
        }
        return switch.get(filter.get_value(), {})


def get_volumes_from_device(device_filter: DeviceFilter, identifier: str):
    try:
        device = get_device(device_filter, identifier)

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