import enum
import time

from loguru import logger as log

# 1. Safer Import: Explicitly require Playerctl version to prevent warnings/crashes
try:
    import gi

    gi.require_version('Playerctl', '2.0')
    from gi.repository import Playerctl
except Exception as e:
    log.warning(f"Playerctl could not be imported: {e}")
    Playerctl = None

from GtkHelper.ComboRow import SimpleComboRowItem, BaseComboRowItem
from GtkHelper.GenerativeUI.ComboRow import ComboRow
from GtkHelper.GenerativeUI.EntryRow import EntryRow
from GtkHelper.GenerativeUI.ExpanderRow import ExpanderRow
from GtkHelper.GenerativeUI.SwitchRow import SwitchRow
from src.backend.PluginManager.ActionCore import ActionCore
from ..internal.PulseHelpers import DeviceFilter, get_device, get_device_list, filter_proplist, get_volumes_from_device, \
    get_standard_device, MprisPlayer


class InfoContent(enum.Enum):
    VOLUME = SimpleComboRowItem("volume", "Volume")
    ADJUSTMENT = SimpleComboRowItem("adjustment", "Adjustment")


class Device(BaseComboRowItem):
    def __init__(self, pulse_name, pulse_index, device_name, player_name_obj=None, proc_bin=None, media_name=None, node_name=None):
        super().__init__()
        self.pulse_name = pulse_name
        self.pulse_index = pulse_index
        self.device_name = device_name
        self.player_name_obj = player_name_obj
        self.proc_bin = proc_bin
        self.media_name = media_name
        self.node_name = node_name

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
        self.selected_device = None
        self._saved_pulse_id = None

        self.device_filter = None
        self.info_content = InfoContent.VOLUME.value

        self.show_device_name = True
        self.device_nick = ""

        self.show_info_content = True

        self.use_standard_device = False

        self.loaded_devices = []

        # Icon
        self.icon_keys = []
        self._current_icon = None
        self._icon_name = ""

        # Internal State
        self._last_volume_refresh = 0.0
        self._player_volume_handler_id = None
        self._player_object = None
        self._sink_input_lost = False
        self._suppress_device_changed = False

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

        if not self.selected_device or not self.show_info_content or self.info_content != InfoContent.VOLUME.value:
            return

        now = time.monotonic()
        # Poll more frequently for Music, less for others (safety poll)
        interval = 0.25 if self.device_filter == DeviceFilter.MUSIC.value else 1.0

        if now - self._last_volume_refresh >= interval:
            self._last_volume_refresh = now
            self.display_device_info()

    def load_devices(self):
        try:
            device_list = get_device_list(self.device_filter)

            self.loaded_devices = []

            for device in device_list:
                # --- FIX: Handle Music Players First ---
                if isinstance(device, MprisPlayer):
                    # Music players don't have 'proplist', so we handle them separately
                    pulse_identifier = device.name  # e.g. "spotify"
                    device_name = device.name.capitalize()

                    self.loaded_devices.append(Device(
                        pulse_name=pulse_identifier,
                        pulse_index=0,  # Players don't use numeric indexes
                        device_name=device_name
                    ))
                    continue
                # ---------------------------------------

                # Standard PulseAudio Logic (Sinks, Sources, Apps)
                if hasattr(device, 'description') and "Monitor" in str(device.description):
                    continue

                device_name = filter_proplist(device.proplist)

                if device_name is None:
                    continue

                if self.device_filter == DeviceFilter.SINK_INPUT.value:
                    pulse_identifier = str(device.index)
                else:
                    pulse_identifier = device.name

                self.loaded_devices.append(Device(
                    pulse_name=pulse_identifier,
                    pulse_index=device.index,
                    device_name=device_name
                ))
        except Exception as e:
            log.error(f"Error while populating device list: {e}")
            return

        self.device_combo_row.populate(self.loaded_devices, self.device_combo_row.get_value())
        self.display_device_info()

    # UI Events

    def show_standard_device_changed(self, widget, value, old):
        self.use_standard_device = value
        self.device_combo_row.widget.set_sensitive(not self.use_standard_device)
        self.check_standard_device()

    def device_filter_changed(self, widget, value, old):
        self.device_filter = value
        self._saved_pulse_id = None
        if self.device_filter != DeviceFilter.MUSIC.value:
            self._disconnect_player_signal()
        self.load_devices()

    def device_changed(self, widget, value, old):
        if value:
            self._saved_pulse_id = value.pulse_name

        if self._suppress_device_changed:
            if value not in (None, ""):
                self._suppress_device_changed = False
            return

        if value is None or value == "":
            self.selected_device = None
            self._sink_input_lost = True
            self.display_device_info()
            return

        self.selected_device = value
        self._sink_input_lost = False

        self.display_device_name()
        self.display_device_info()

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

        if Playerctl and self.device_filter == DeviceFilter.MUSIC.value and isinstance(self._player_object, Playerctl.Player):
            try:
                return str(int(round(self._player_object.props.volume * 100)))
            except Exception:
                pass

        fallback_name = self.selected_device.device_name if self.device_filter == DeviceFilter.SINK_INPUT.value else None
        fallback_index = self.selected_device.pulse_index if self.device_filter == DeviceFilter.SINK_INPUT.value else None
        fallback_proc = self.selected_device.proc_bin if self.device_filter == DeviceFilter.SINK_INPUT.value else None
        fallback_media = self.selected_device.media_name if self.device_filter == DeviceFilter.SINK_INPUT.value else None
        fallback_node = self.selected_device.node_name if self.device_filter == DeviceFilter.SINK_INPUT.value else None

        volumes = get_volumes_from_device(
            self.device_filter,
            self.selected_device.pulse_name,
            fallback_name,
            fallback_index,
            fallback_proc,
            fallback_media,
            fallback_node,
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
        if len(args) < 2:
            return

        event = args[1]

        # Always reload devices if we are in a 'Lost' state or no device is selected.
        if self._sink_input_lost or self.selected_device is None:
            # Wrap in try/except to prevent async crash
            try:
                self.load_devices()
            except Exception as e:
                log.error(f"Failed to reload devices on pulse event: {e}")
            return

        index = self.selected_device.pulse_index
        if event.index == index:
            self.display_icon()
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
            try:
                handler_id = player.connect("volume", self._on_player_volume_changed)
            except Exception:
                handler_id = None
            if handler_id is None:
                try:
                    handler_id = player.connect("notify::volume", self._on_player_volume_changed)
                except Exception:
                    handler_id = None
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
        self._last_volume_refresh = time.monotonic()
        self.display_device_info()