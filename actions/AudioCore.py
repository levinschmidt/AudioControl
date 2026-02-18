import enum
import os
import time

import pulsectl
from gi.overrides.Gio import Gio
from loguru import logger as log

from GtkHelper.ComboRow import SimpleComboRowItem, BaseComboRowItem
from GtkHelper.GenerativeUI.ComboRow import ComboRow
from GtkHelper.GenerativeUI.EntryRow import EntryRow
from GtkHelper.GenerativeUI.ExpanderRow import ExpanderRow
from GtkHelper.GenerativeUI.SwitchRow import SwitchRow
from src.backend.PluginManager.ActionCore import ActionCore

from ..internal.PulseHelpers import (Modes, get_sinks_list, get_volume_from_application, get_volume_from_music_player, get_volume_from_output, get_default_output)
from ..globals import GameFilter


class InfoContent(enum.Enum):
    VOLUME = SimpleComboRowItem("volume", "Volume")
    ADJUSTMENT = SimpleComboRowItem("adjustment", "Adjustment")

class MusicPlayer(BaseComboRowItem):
    def __init__(self, bus_name):
        super().__init__()

        self.bus_name: str = bus_name
        self.name: str = bus_name.split(".")[-1]

        self.proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            None,
            bus_name,
            "/org/mpris/MediaPlayer2",
            "org.mpris.MediaPlayer2.Player",
            None
        )

    def __str__(self):
        return self.name

    def get_value(self):
        return self.name

class Application(BaseComboRowItem):
    def __init__(self, application_name: str, restore_id: str, index: int, mute_on_game_launch: bool = False):
        super().__init__()
        self.application_name = application_name
        self.restore_id = restore_id
        self.index = index
        self.mute_on_game_launch = mute_on_game_launch

    def __str__(self):
        return self.application_name

    def get_value(self):
        return self.restore_id

class OutputDevice(BaseComboRowItem):
    def __init__(self, name: str, node_name: str, index: int, sink=None, volume=None):
        super().__init__()
        self.name = name
        self.node_name = node_name
        self.index = index
        self.sink = sink
        self.volume = None

    def __str__(self):
        return self.name

    def get_value(self):
        return self.node_name

class AudioCore(ActionCore):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.has_configuration = True

        self.plugin_base.asset_manager.icons.add_listener(self.icon_changed)

        self.pulse_client = pulsectl.Pulse("streamcontroller-AudioControlPlus")

        self.plugin_base.connect_to_event(event_id="com_gapls_AudioControl::PulseEvent",
                                          callback=self.on_pulse_device_change)


        # Settings
        self.selected_music_player: MusicPlayer = None
        self.selected_application: Application = None
        self.selected_output_device: OutputDevice = None

        self.mode: Modes = None
        self.info_content = InfoContent.VOLUME.value

        self.show_device_name = True
        self.device_nick = ""

        self.show_info_content = True

        self.loaded_music_players: list[MusicPlayer] = []
        self.loaded_applications: list[Application] = []
        self.loaded_output_devices: list[OutputDevice] = []

        self._last_volume_refresh_music_player = 0.0
        self._last_game_update_time = 0.0

        # Icon
        self.icon_keys = []

        self._current_icon = None
        self._icon_name = ""

        self.create_event_assigners()

    def create_generative_ui(self):
        self.device_expander = ExpanderRow(
            action_core=self,
            var_name="device-expander",
            default_value=False,
            title="Device Row",
            show_enable_switch=False
        )

        self.device_filter_combo_row = ComboRow(
            action_core=self,
            var_name="device-filter",
            default_value=Modes.APPLICATION.value,
            items=[device_filter.value for device_filter in Modes],
            title="base-filter-dropdown",
            complex_var_name=False,
            on_change=self.mode_changed
        )

        self.device_combo_row = ComboRow(
            action_core=self,
            var_name="sink-id",
            default_value="",
            items=[],
            title="base-device-dropdown",
            complex_var_name=False,
            on_change=self.target_selection_changed
        )

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

    def create_event_assigners(self):
        pass

    def update_default_output(self):
        if self.mode == Modes.OUTPUT_DEFAULT.value:
            default_output_device = get_default_output()
            if default_output_device:
                name = default_output_device.proplist['device.description']
                node_name = default_output_device.proplist.get('node.name', None)
                index = default_output_device.index
                volume = round(default_output_device.volume.value_flat * 100)
                self.selected_output_device = OutputDevice(name=name, node_name=node_name, index=index, sink=default_output_device, volume=volume)
                self.display_device_name()
                self.display_device_info()

    def update_game_application(self):
        now = time.monotonic()
        if now - self._last_game_update_time > 1.0:
            if self.mode == Modes.GAME.value:
                self._last_game_update_time = now

                application_list = get_sinks_list(self, self.mode)

                for application in application_list:
                    if 'application.name' in application.proplist:
                        # Blacklist
                        if any(element.lower() in application.proplist['application.name'].lower() for element in GameFilter.blacklist):
                            continue

                        # Whitelist
                        if any(element.lower() in application.proplist['application.name'].lower() for element in GameFilter.whitelist):
                            application_name = application.proplist['application.name']
                            restore_id = application.proplist.get('module-stream-restore.id', None)
                            index = application.index
                            if any(element.lower() in application.proplist['application.name'].lower() for element in GameFilter.auto_mute):
                                mute_on_game_launch = True
                            else:
                                mute_on_game_launch = False
                            self.selected_application = Application(application_name=application_name, restore_id=restore_id, index=index,
                                                                    mute_on_game_launch=mute_on_game_launch)
                            return

                    if 'application.process.binary' in application.proplist:
                        if os.getenv('AUDIO_CONTROL_PLUS_DEBUG_GAMES') == 'true':
                            log.debug(f"{application.proplist['application.name']} - {application.proplist['application.process.binary']}")
                        if any(element.lower() in application.proplist['application.process.binary'].lower() for element in GameFilter.binary):
                            application_name = application.proplist['application.name']
                            restore_id = application.proplist.get('module-stream-restore.id', None)
                            index = application.index
                            if any(element.lower() in application_name.lower() for element in GameFilter.auto_mute):
                                mute_on_game_launch = True
                            else:
                                mute_on_game_launch = False
                            self.selected_application = Application(application_name=application_name, restore_id=restore_id, index=index, mute_on_game_launch=mute_on_game_launch)
                            return

                self.selected_application = Application(application_name='Game', restore_id=None, index=None)
                self.on_game_change()

    def update_application_index(self):
        if self.selected_application and self.selected_application.restore_id:
            application_list = get_sinks_list(self, self.mode)

            for application in application_list:
                if 'module-stream-restore.id' in application.proplist and application.proplist['module-stream-restore.id'] == self.selected_application.restore_id:
                    index = application.index
                    self.selected_application.index = index
                    return

    def on_game_change(self):
        pass

    def on_update(self):
        self.update_default_output()
        self.update_game_application()
        self.display_device_name()
        self.display_device_info()
        self.display_icon()
        return

    def on_tick(self):
        if not self.mode == Modes.MUSIC.value or not self.selected_music_player:
            return

        now = time.monotonic()
        # Poll more frequently for Music, less for others (safety poll)
        interval = 1 #0.25 if self.device_filter == DeviceFilter.MUSIC.value else 1.0

        if now - self._last_volume_refresh_music_player >= interval:
            self._last_volume_refresh_music_player = now
            self.display_device_info()

    def load_sinks(self):
        try:
            sink_list = get_sinks_list(self, self.mode)

            if self.mode == Modes.MUSIC.value:
                self.loaded_music_players = []

                for device in sink_list:
                    self.loaded_music_players.append(MusicPlayer(bus_name=device))

                self.device_combo_row.populate(self.loaded_music_players, self.device_combo_row.get_value())

            elif self.mode == Modes.OUTPUT.value:
                self.loaded_output_devices = []

                for sink in sink_list:
                    if 'device.description' in sink.proplist:
                        device_description = sink.proplist['device.description']
                        node_name = sink.proplist.get('node.name', None)
                        index = sink.index
                        volume = round(sink.volume.value_flat * 100)
                        self.loaded_output_devices.append(OutputDevice(name=device_description, node_name=node_name, index=index, sink=sink, volume=volume))

                self.device_combo_row.populate(self.loaded_output_devices, self.device_combo_row.get_value())

            elif self.mode == Modes.APPLICATION.value:
                settings = self.get_settings()
                saved_restore_id = settings.get("restore-id", "")
                saved_restore_name = settings.get("restore-name", "")

                self.loaded_applications = []

                for application in sink_list:
                    if 'application.name' in application.proplist:
                        application_name = application.proplist['application.name']
                        restore_id = application.proplist.get('module-stream-restore.id', None)
                        index = application.index
                        self.loaded_applications.append(Application(application_name=application_name, restore_id=restore_id, index=index))

                self.loaded_applications.append(Application(application_name=saved_restore_name, restore_id=saved_restore_id, index=None))
                self.device_combo_row.populate(self.loaded_applications, saved_restore_id)
            else:
                self.device_combo_row.populate([], "")

        except Exception as e:
            log.error(f"Error while populating device list: {e}")
            return
        self.display_device_info()

    # UI Events

    def mode_changed(self, widget, value, old):
        self.mode = value
        self.load_sinks()

    def target_selection_changed(self, widget, value, old):
        if self.mode == Modes.MUSIC.value:
            self.selected_music_player = value
        elif self.mode == Modes.APPLICATION.value:
            self.selected_application = value
            settings = self.get_settings()
            settings["restore-id"] = value.restore_id
            settings["restore-name"] = value.application_name
            self.set_settings(settings)
        elif self.mode == Modes.OUTPUT.value:
            self.selected_output_device = value
            settings = self.get_settings()
            settings["node_name"] = value.node_name
            settings["name"] = value.name
            self.set_settings(settings)
        elif self.mode == Modes.GAME.value:
            pass


        self.display_device_name()
        self.display_device_info()

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

        if not self.device_nick and not self.selected_application and not self.selected_music_player and not self.selected_output_device:
            return

        if self.device_nick and self.device_nick != "":
            self.set_top_label(self.device_nick)
        else:
            if self.mode == Modes.MUSIC.value:
                self.set_top_label(self.selected_music_player.name)
            elif self.mode == Modes.APPLICATION.value or self.mode == Modes.GAME.value:
                self.set_top_label(self.selected_application.application_name)
            elif self.mode == Modes.OUTPUT.value or self.mode == Modes.OUTPUT_DEFAULT.value:
                self.set_top_label(self.selected_output_device.name)

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

    def display_volume(self) -> str:
        if not self.mode or (not self.selected_application and not self.selected_music_player and not self.selected_output_device):
            return "N/A"

        if self.mode == Modes.MUSIC.value:
            volume = get_volume_from_music_player(self.selected_music_player.bus_name)
        elif self.mode == Modes.APPLICATION.value or self.mode == Modes.GAME.value:
            volume = get_volume_from_application(self.selected_application.restore_id)
        elif self.mode == Modes.OUTPUT.value or self.mode == Modes.OUTPUT_DEFAULT.value:
            volume = get_volume_from_output(self)
        else:
            volume = None

        if volume is not None:
            return str(int(volume))
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

    #TODO: Update volume on change
    async def on_pulse_device_change(self, *args, **kwargs):
        if len(args) < 2 or self.mode == Modes.MUSIC.value:
            return

        event = args[1]
        event_type = event.t._value
        if self.mode == Modes.APPLICATION.value or self.mode == Modes.GAME.value:
            if self.selected_application:
                index = self.selected_application.index
            else:
                index = None
        elif self.mode == Modes.OUTPUT.value or self.mode == Modes.OUTPUT_DEFAULT.value:
            if self.selected_output_device:
                index = self.selected_output_device.index
            else:
                index = None
        else:
            index = None

        if event_type == 'new':
            if index is None:
                if self.mode == Modes.APPLICATION.value or self.mode == Modes.GAME.value:
                    self.update_application_index()
            if self.mode == Modes.GAME.value:
                self.update_game_application()
                self.display_device_name()
                self.display_icon()
                self.display_device_info()
        elif event_type == 'remove':
            if event.index == index:
                self.selected_application.index = None
                if self.mode == Modes.GAME.value:
                    self.update_game_application()
                    self.display_device_name()
        elif event_type == 'change':
            if event.index == index:
                if self.mode == Modes.APPLICATION.value or self.mode == Modes.GAME.value:
                    pass
                elif self.mode == Modes.OUTPUT.value or self.mode == Modes.OUTPUT_DEFAULT.value:
                    self.selected_output_device.volume = get_volume_from_output(self)
            if event.facility == 'server':
                self.update_default_output()

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
