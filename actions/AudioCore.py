import enum
import time

from gi.overrides.Gio import Gio
from loguru import logger as log

from GtkHelper.ComboRow import SimpleComboRowItem, BaseComboRowItem
from GtkHelper.GenerativeUI.ComboRow import ComboRow
from GtkHelper.GenerativeUI.EntryRow import EntryRow
from GtkHelper.GenerativeUI.ExpanderRow import ExpanderRow
from GtkHelper.GenerativeUI.SwitchRow import SwitchRow
from src.backend.PluginManager.ActionCore import ActionCore
from ..internal.PulseHelpers import (DeviceFilter, get_application_list, get_volume_from_application, get_volume_from_music_player)
from ..internal.PulseEventListener import PulseEvent


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
    def __init__(self, application_name: str, restore_id: str,):
        super().__init__()
        self.application_name = application_name
        self.restore_id = restore_id

    def __str__(self):
        return self.application_name

    def get_value(self):
        return self.restore_id


class AudioCore(ActionCore):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.has_configuration = True

        self.plugin_base.asset_manager.icons.add_listener(self.icon_changed)

        self.plugin_base.connect_to_event(event_id="com_gapls_AudioControl::PulseEvent",
                                          callback=self.on_pulse_device_change)


        # Settings
        self.selected_music_player: MusicPlayer = None
        self.selected_application: Application = None

        self.device_filter: DeviceFilter = None
        self.info_content = InfoContent.VOLUME.value

        self.show_device_name = True
        self.device_nick = ""

        self.show_info_content = True

        self.loaded_music_players: list[MusicPlayer] = []
        self.loaded_applications: list[Application] = []

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
            default_value=DeviceFilter.APPLICATION.value,
            items=[device_filter.value for device_filter in DeviceFilter],
            title="base-filter-dropdown",
            complex_var_name=False,
            on_change=self.device_filter_changed
        )

        self.device_combo_row = ComboRow(
            action_core=self,
            var_name="restore-id",
            default_value="",
            items=[],
            title="base-device-dropdown",
            complex_var_name=False,
            on_change=self.application_changed
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

    def update_game_application(self):
        now = time.monotonic()
        if now - self._last_game_update_time > 5.0:
            if self.device_filter == DeviceFilter.GAME.value:
                self._last_game_update_time = now

                application_list = get_application_list(self.device_filter)

                for application in application_list:
                    if 'application.name' in application.proplist:
                        # Blacklist
                        blacklist = ["SocialClubHelper.exe", "Launcher.exe", "Rockstar Games Launcher", "GTA5_Enhanced.exe", "FSD-Win64-Shipping.exe"]
                        if any(element.lower() in application.proplist['application.name'].lower() for element in blacklist):
                            continue

                        # Whitelist
                        whitelist = []
                        if any(element in application.proplist['application.name'].lower() for element in whitelist):
                            application_name = application.proplist['application.name']
                            restore_id = application.proplist.get('module-stream-restore.id', None)
                            self.selected_application = Application(application_name=application_name, restore_id=restore_id)
                            return

                    if 'application.process.binary' in application.proplist:
                        application_process_binary = application.proplist['application.process.binary']
                        if "wine" in application_process_binary.lower():
                            application_name = application.proplist['application.name']
                            restore_id = application.proplist.get('module-stream-restore.id', None)
                            self.selected_application = Application(application_name=application_name, restore_id=restore_id)
                            return

                self.selected_application = Application(application_name='Game', restore_id=None)


    def on_update(self):
        self.update_game_application()
        self.display_device_name()
        self.display_device_info()
        self.display_icon()
        return

    def on_tick(self):
        if not self.device_filter == DeviceFilter.MUSIC.value or not self.selected_music_player:
            return

        now = time.monotonic()
        # Poll more frequently for Music, less for others (safety poll)
        interval = 1 #0.25 if self.device_filter == DeviceFilter.MUSIC.value else 1.0

        if now - self._last_volume_refresh_music_player >= interval:
            self._last_volume_refresh_music_player = now
            self.display_device_info()

    def load_application(self):
        try:
            application_list = get_application_list(self.device_filter)

            if self.device_filter == DeviceFilter.MUSIC.value:
                self.loaded_music_players = []

                for device in application_list:
                    self.loaded_music_players.append(MusicPlayer(bus_name=device))

                self.device_combo_row.populate(self.loaded_music_players, self.device_combo_row.get_value())
            elif self.device_filter == DeviceFilter.APPLICATION.value:
                settings = self.get_settings()
                saved_restore_id = settings.get("restore-id", "")
                saved_restore_name = settings.get("restore-name", "")

                self.loaded_applications = []

                for application in application_list:
                    if 'application.name' in application.proplist:
                        application_name = application.proplist['application.name']
                        restore_id = application.proplist.get('module-stream-restore.id', None)
                        self.loaded_applications.append(Application(application_name=application_name, restore_id=restore_id))

                self.loaded_applications.append(Application(application_name=saved_restore_name, restore_id=saved_restore_id))
                self.device_combo_row.populate(self.loaded_applications, saved_restore_id)
            else:
                self.device_combo_row.populate([], "")

        except Exception as e:
            log.error(f"Error while populating device list: {e}")
            return
        self.display_device_info()

    # UI Events

    def device_filter_changed(self, widget, value, old):
        self.device_filter = value
        self.load_application()

    def application_changed(self, widget, value, old):
        if self.device_filter == DeviceFilter.MUSIC.value:
            self.selected_music_player = value
        elif self.device_filter == DeviceFilter.APPLICATION.value:
            self.selected_application = value
            settings = self.get_settings()
            settings["restore-id"] = value.restore_id
            settings["restore-name"] = value.application_name
            self.set_settings(settings)
        elif self.device_filter == DeviceFilter.GAME.value:
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

        if not self.device_nick and not self.selected_application and not self.selected_music_player:
            return

        if self.device_nick and self.device_nick != "":
            self.set_top_label(self.device_nick)
        else:
            if self.device_filter == DeviceFilter.MUSIC.value:
                self.set_top_label(self.selected_music_player.name)
            elif self.device_filter == DeviceFilter.APPLICATION.value or self.device_filter == DeviceFilter.GAME.value:
                self.set_top_label(self.selected_application.application_name)

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
        if not self.device_filter or (not self.selected_application and not self.selected_music_player):
            return "N/A"

        if self.device_filter == DeviceFilter.MUSIC.value:
            volume = get_volume_from_music_player(self.selected_music_player.bus_name)
        elif self.device_filter == DeviceFilter.APPLICATION.value or self.device_filter == DeviceFilter.GAME.value:
            volume = get_volume_from_application(self.selected_application.restore_id)
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

    async def on_pulse_device_change(self, *args, **kwargs):
        if len(args) < 2:
            return

        self.update_game_application()
        self.display_device_name()
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
