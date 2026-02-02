import enum
import time
try:
    from gi.repository import Playerctl
except Exception:
    Playerctl = None

from loguru import logger as log

from GtkHelper.ComboRow import SimpleComboRowItem, BaseComboRowItem
from GtkHelper.GenerativeUI.ComboRow import ComboRow
from GtkHelper.GenerativeUI.EntryRow import EntryRow
from GtkHelper.GenerativeUI.ExpanderRow import ExpanderRow
from GtkHelper.GenerativeUI.SwitchRow import SwitchRow
from src.backend.PluginManager.ActionCore import ActionCore
from ..internal.PulseHelpers import DeviceFilter, get_device, get_device_list, filter_proplist, get_volumes_from_device, \
    get_standard_device


class InfoContent(enum.Enum):
    VOLUME = SimpleComboRowItem("volume", "Volume")
    ADJUSTMENT = SimpleComboRowItem("adjustment", "Adjustment")


class Device(BaseComboRowItem):
    def __init__(self, pulse_name, pulse_index, device_name, player_name_obj=None, proc_bin=None, media_name=None):
        super().__init__()
        self.pulse_name: str = pulse_name
        self.pulse_index: int = pulse_index
        self.device_name: str = device_name
        # Optional Playerctl.PlayerName kept separately; pulse_name stays a string for persistence
        self.player_name_obj = player_name_obj
        # Extra fingerprint for sink-input matching (e.g., chromium-based apps)
        self.proc_bin = proc_bin
        self.media_name = media_name

    def __str__(self):
        return self.device_name

    def get_value(self):
        return self.pulse_name


class AudioCore(ActionCore):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.has_configuration = True

        self.plugin_base.asset_manager.icons.add_listener(self.icon_changed)

        self.plugin_base.connect_to_event(event_id="com_gapls_AudioControl::PulseEvent",
                                          callback=self.on_pulse_device_change)

        # Settings

        self.selected_device: Device = None

        self.device_filter: DeviceFilter = None
        self.info_content = InfoContent.VOLUME.value

        self.show_device_name = True
        self.device_nick = ""

        self.show_info_content = True

        self.use_standard_device = False

        self.loaded_devices: list[Device] = []

        # Icon

        self.icon_keys = []

        self._current_icon = None
        self._icon_name = ""

        # Throttle for periodic volume refresh (primarily for Playerctl players)
        self._last_volume_refresh = 0.0
        self._player_volume_handler_id = None
        self._player_object = None
        # Prevent repeated clears when a sink-input disappears
        self._sink_input_lost = False
        self._suppress_device_changed = False
        self._block_selection_until = 0.0

        self.create_event_assigners()

    def create_generative_ui(self):
        self.device_expander = ExpanderRow(
            action_core=self,
            var_name="device-expander",
            default_value=False,
            title="Device Row",
            show_enable_switch=False
        )

        self.standard_device_switch = SwitchRow(
            action_core=self,
            var_name="use-standard",
            default_value=False,
            title="Standard Device",
            complex_var_name=False,
            on_change=self.show_standard_device_changed
        )

        self.device_filter_combo_row = ComboRow(
            action_core=self,
            var_name="device-filter",
            default_value=DeviceFilter.SINK.value,
            items=[device_filter.value for device_filter in DeviceFilter],
            title="base-filter-dropdown",
            complex_var_name=False,
            on_change=self.device_filter_changed
        )

        self.device_combo_row = ComboRow(
            action_core=self,
            var_name="pulse-name",
            default_value="",
            items=[],
            title="base-device-dropdown",
            complex_var_name=False,
            on_change=self.device_changed
        )

        self.device_expander.add_row(self.standard_device_switch.widget)
        self.device_expander.add_row(self.device_filter_combo_row.widget)
        self.device_expander.add_row(self.device_combo_row.widget)

        # Use Standard Device Toggle/Switch

        self.info_expander = ExpanderRow(
            action_core=self,
            var_name="info-expander",
            default_value=False,
            title="Info Row",
            show_enable_switch=False
        )

        self.info_content_switch = SwitchRow(
            action_core=self,
            var_name="show-info",
            default_value=True,
            title="base-info-toggle",
            complex_var_name=False,
            on_change=self.show_info_content_changed
        )

        self.info_content_combo_row = ComboRow(
            action_core=self,
            var_name="info-content",
            default_value=InfoContent.VOLUME.value,
            items=[info_content.value for info_content in InfoContent],
            title="base-info-content",
            complex_var_name=False,
            on_change=self.info_content_changed
        )

        self.info_expander.add_row(self.info_content_switch.widget)
        self.info_expander.add_row(self.info_content_combo_row.widget)

        self.device_name_expander = ExpanderRow(
            action_core=self,
            var_name="device-name-expander",
            default_value=False,
            title="Device Name Row",
            show_enable_switch=False
        )

        self.device_name_switch = SwitchRow(
            action_core=self,
            var_name="show-device-name",
            default_value=True,
            title="base-name-toggle",
            on_change=self.show_device_nick_changed
        )

        self.device_nick_entry = EntryRow(
            action_core=self,
            var_name="nick",
            default_value="",
            title="base-nick",
            complex_var_name=False,
            on_change=self.device_nick_changed
        )

        self.device_name_expander.add_row(self.device_name_switch.widget)
        self.device_name_expander.add_row(self.device_nick_entry.widget)

        self.device_filter = self.device_filter_combo_row.get_selected_item()

    def create_event_assigners(self):
        pass

    def on_update(self):
        self.display_device_name()
        self.display_device_info()
        self.display_icon()
        return

    def on_tick(self):
        self.check_standard_device()
        # Playerctl volumes have no Pulse events; poll periodically to update the UI
        if self.show_info_content and self.info_content == InfoContent.VOLUME.value and \
                self.device_filter == DeviceFilter.MUSIC.value and self.selected_device:
            now = time.monotonic()
            # Faster polling when no signal handler is attached
            interval = 0.25 if self._player_volume_handler_id is None else 1.0
            if now - self._last_volume_refresh >= interval:
                self._last_volume_refresh = now
                self.display_device_info()

    def load_devices(self):
        try:
            device_list = get_device_list(self.device_filter)

            self.loaded_devices = []

            for device in device_list:
                # Logic to skip Monitors (only applies to Pulse objects usually)
                if hasattr(device, 'description') and "Monitor" in str(device.description):
                    continue

                device_name = filter_proplist(device.proplist)

                if device_name is None:
                    continue

                # --- IDENTIFIER LOGIC ---
                proc_bin = None
                media_name = None
                if self.device_filter == DeviceFilter.SINK_INPUT.value:
                    # Prefer stable identifiers for applications; fallback to index
                    proc_bin = device.proplist.get('application.process.binary') if hasattr(device, 'proplist') else None
                    app_name = device.proplist.get('application.name') if hasattr(device, 'proplist') else None
                    media_name = device.proplist.get('media.name') if hasattr(device, 'proplist') else None
                    # Build a stable identifier; prefer binary+media combo to separate chromium-based apps
                    if proc_bin and media_name:
                        pulse_identifier = f"{proc_bin}|{media_name}"
                    else:
                        pulse_identifier = proc_bin or app_name or str(device.index)
                elif self.device_filter == DeviceFilter.MUSIC.value:
                    # Store string identifier for persistence; keep PlayerName separately if present
                    pulse_identifier = str(device.name)
                else:
                    # Pulse Sinks/Sources use Name
                    pulse_identifier = device.name
                # ------------------------

                new_device = Device(
                    pulse_name=pulse_identifier,
                    pulse_index=device.index,
                    device_name=device_name,
                    player_name_obj=getattr(device, "player_name", None),
                    proc_bin=proc_bin,
                    media_name=media_name,
                )
                self.loaded_devices.append(new_device)
        except Exception as e:
            log.error(f"Error while populating device list: {e}")
            return

        # Avoid auto-selecting first item if none selected
        current_value = self.device_combo_row.get_value()
        self.device_combo_row.populate(self.loaded_devices, current_value if current_value else "")
        if not current_value:
            self.device_combo_row.set_selected_item(None)
        self.display_device_info()

    # UI Events

    def show_standard_device_changed(self, widget, value, old):
        self.use_standard_device = value
        self.device_combo_row.widget.set_sensitive(not self.use_standard_device)
        self.check_standard_device()

    def device_filter_changed(self, widget, value, old):
        self.device_filter = value
        # Disconnect old player signals when leaving MUSIC filter
        if self.device_filter != DeviceFilter.MUSIC.value:
            self._disconnect_player_signal()
        self.load_devices()

    def device_changed(self, widget, value, old):
        if self._suppress_device_changed:
            log.debug("device_changed suppressed (value={}, old={})", value, old)
            # Drop suppression once a real selection comes in
            if value not in (None, ""):
                self._suppress_device_changed = False
            return
        if self._block_selection_until and time.monotonic() < self._block_selection_until and value not in (None, ""):
            log.debug("device_changed: debouncing re-selection (value={}, old={})", value, old)
            self._suppress_device_changed = True
            self.device_combo_row.set_selected_item(None)
            self._suppress_device_changed = False
            return
        # When selection is cleared (e.g., app vanished), do not auto-select another
        if value is None or value == "":
            log.debug("device_changed: selection cleared (old={})", old)
            self.selected_device = None
            self._sink_input_lost = True
            self._block_selection_until = time.monotonic() + 0.5
            self.display_device_info()
            return
        log.debug("device_changed: selected {} (old={})", getattr(value, "device_name", value), getattr(old, "device_name", old))
        self.selected_device = value
        self._sink_input_lost = False
        self._block_selection_until = 0.0

        self.display_device_name()
        self.display_device_info()
        # Connect to Playerctl volume signals when a music player is selected
        if self.device_filter == DeviceFilter.MUSIC.value:
            self._connect_player_signal()
        else:
            self._disconnect_player_signal()

    def show_info_content_changed(self, widget, value, old):
        self.show_info_content = value
        self.display_device_info()

    def info_content_changed(self, widget, value, old):
        self.info_content = value
        self.display_device_info()

    def show_device_nick_changed(self, widget, value, old):
        self.show_device_name = value
        self.display_device_name()

    def device_nick_changed(self, widget, value, old):
        self.device_nick = value
        self.display_device_name()

    ############ DISPLAY #############

    def display_device_name(self):
        if not self.show_device_name:
            self.set_top_label("")
            return

        if not self.device_nick and not self.selected_device:
            return

        if self.device_nick and self.device_nick != "":
            self.set_top_label(self.device_nick)
        else:
            self.set_top_label(self.selected_device.device_name)

    def display_device_info(self):
        if not self.show_info_content:
            self.set_bottom_label("")
            return

        if self.info_content == InfoContent.VOLUME.value:
            self.set_bottom_label(self.display_volume())
        elif self.info_content == InfoContent.ADJUSTMENT.value:
            self.set_bottom_label(self.display_adjustment())
        else:
            self.set_bottom_label("")

    def display_volume(self):
        if not self.device_filter or not self.selected_device:
            return

        # If we already have a player object, use it directly for fresher values
        if Playerctl and self.device_filter == DeviceFilter.MUSIC.value and isinstance(self._player_object, Playerctl.Player):
            try:
                return str(int(round(self._player_object.props.volume * 100)))
            except Exception as e:
                log.debug(f"Could not read volume from connected player: {e}")

        fallback_name = self.selected_device.device_name if self.device_filter == DeviceFilter.SINK_INPUT.value else None
        fallback_index = self.selected_device.pulse_index if self.device_filter == DeviceFilter.SINK_INPUT.value else None
        fallback_proc = self.selected_device.proc_bin if self.device_filter == DeviceFilter.SINK_INPUT.value else None
        fallback_media = self.selected_device.media_name if self.device_filter == DeviceFilter.SINK_INPUT.value else None
        volumes = get_volumes_from_device(
            self.device_filter,
            self.selected_device.pulse_name,
            fallback_name,
            fallback_index,
            fallback_proc,
            fallback_media,
        )

        if len(volumes) > 0:
            return str(int(volumes[0]))
        return "N/A"

    def display_adjustment(self):
        return "B"

    async def icon_changed(self, event: str, key: str, asset):
        if not key in self.icon_keys:
            return

        if key != self._icon_name:
            return

        self._current_icon = asset
        self._icon_name = key

        self.display_icon()

    async def on_pulse_device_change(self, *args, **kwargs):
        if len(args) < 2 or self.selected_device is None:
            return

        event = args[1]
        index = self.selected_device.pulse_index

        if event.index == index:
            log.debug("pulse_event: matched current index {}", index)
            self.display_icon()
            self.display_device_info()
        elif self.device_filter == DeviceFilter.SINK_INPUT.value:
            log.debug("pulse_event: selected app disappeared or changed (event.index={}, selected.index={}); clearing selection", event.index, index)
            if self._sink_input_lost:
                return
            self._sink_input_lost = True
            self._suppress_device_changed = True
            self._block_selection_until = time.monotonic() + 0.5
            self.selected_device = None
            self.device_combo_row.set_selected_item(None)
            # Do not repopulate to avoid auto-selecting another item
            self._suppress_device_changed = False
            self.display_device_info()

    def display_icon(self):
        if not self._current_icon:
            return

        _, rendered = self._current_icon.get_values()

        if rendered or None:
            self.set_media(image=rendered)

    def set_current_icon(self):
        pass

    def check_standard_device(self):
        if self.use_standard_device:
            standard_device = get_standard_device(self.device_filter)

            if self.selected_device is None or self.selected_device.pulse_name == standard_device.name:
                return

            for device in self.loaded_devices:
                if device.pulse_name != standard_device.name:
                    continue

                self.selected_device = device
                self.device_combo_row.set_selected_item(device)
                break

    def _connect_player_signal(self):
        if not Playerctl or self.selected_device is None:
            return
        try:
            player = get_device(self.device_filter, self.selected_device.pulse_name)
            if not isinstance(player, Playerctl.Player):
                return
            self._disconnect_player_signal()
            self._player_object = player

            handler_id = None
            # Try native volume signal first
            try:
                handler_id = player.connect("volume", self._on_player_volume_changed)
                used_signal = "volume"
            except Exception:
                handler_id = None
                used_signal = None
            # Fallback to property notify signal
            if handler_id is None:
                try:
                    handler_id = player.connect("notify::volume", self._on_player_volume_changed)
                    used_signal = "notify::volume"
                except Exception:
                    handler_id = None
                    used_signal = None
            self._player_volume_handler_id = handler_id
        except Exception as e:
            log.debug(f"Could not connect to player signals: {e}")

    def _disconnect_player_signal(self):
        if self._player_object and self._player_volume_handler_id:
            try:
                self._player_object.disconnect(self._player_volume_handler_id)
            except Exception as e:
                log.debug(f"Could not disconnect player signal: {e}")
        self._player_object = None
        self._player_volume_handler_id = None

    def _on_player_volume_changed(self, player, *args):
        # Update immediately on signal, then throttle subsequent polls
        self._last_volume_refresh = time.monotonic()
        self.display_device_info()
