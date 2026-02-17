from gi.overrides.GLib import GLib
from gi.overrides.Gio import Gio
from loguru import logger as log

import pulsectl
from GtkHelper.GenerativeUI.ExpanderRow import ExpanderRow
from GtkHelper.GenerativeUI.ScaleRow import ScaleRow
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.EventAssigner import EventAssigner

from internal.PulseHelpers import get_output, set_volume_output, get_volume_from_output
from .AudioCore import AudioCore
from ..globals import Icons
from ..internal.PulseHelpers import (get_application, get_volume_from_music_player, get_volume_from_application, set_volume_music_player,
                                     set_volume_application, Modes, mute)


class ControlVolume(AudioCore):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.icon_keys = [Icons.MUTED, Icons.UNMUTED]

        self.plugin_base.connect_to_event(event_id="com_gapls_AudioControl::PulseEvent",
                                          callback=self.on_pulse_device_change)

        self.adjust: int = 1
        self.bounds = 150

        self.create_generative_ui()

    def create_generative_ui(self):
        super().create_generative_ui()

        self.volume_adjust_row = ExpanderRow(
            action_core=self,
            var_name="adjust-expander",
            default_value=False,
            title="Volume Adjust Row",
        )

        self.volume_adjust_scale = ScaleRow(
            action_core=self,
            var_name="volume-adjust",
            default_value=1,
            min=-100,
            max=100,
            step=1,
            digits=0,
            title="Adjustment",
            draw_value=True,
            on_change=self.on_volume_adjust_change
        )

        self.volume_bound_scale = ScaleRow(
            action_core=self,
            var_name="volume-bounds",
            default_value=100,
            min=0,
            max=150,
            step=1,
            digits=0,
            title="Maximum Audio Bounds",
            draw_value=True,
            on_change=self.on_volume_bound_change
        )

        self.volume_adjust_row.add_row(self.volume_adjust_scale.widget)
        self.volume_adjust_row.add_row(self.volume_bound_scale.widget)

    def create_event_assigners(self):
        self.add_event_assigner(EventAssigner(
            id="adjust-volume-positive",
            ui_label="Volume Up",
            default_event=Input.Dial.Events.TURN_CW,
            callback=self.event_adjust_volume_positive
        ))

        self.add_event_assigner(EventAssigner(
            id="adjust-volume-negative",
            ui_label="Volume Down",
            default_event=Input.Dial.Events.TURN_CCW,
            callback=self.event_adjust_volume_negative
        ))

        self.add_event_assigner(EventAssigner(
            id="mute",
            ui_label="Mute",
            callback=self.on_mute
        ))

    def event_adjust_volume_positive(self, event):
        self.adjust_volume()

    def event_adjust_volume_negative(self, event):
        self.adjust_volume(-1)

    def on_update(self):
        self.update_mute_state()
        super().on_update()
        return

    def adjust_volume(self, modifier: int = 1):
        adjustment = self.adjust * modifier

        if self.mode == Modes.MUSIC.value:
            volume = get_volume_from_music_player(self.selected_music_player.bus_name) + adjustment

            set_volume_music_player(self.selected_music_player, volume)

        elif self.mode == Modes.OUTPUT.value:
            if self.selected_output_device is None:
                self.show_error(1)
                return

            try:
                output_device = get_output(self.selected_output_device.node_name)
                old_volume = get_volume_from_output(self.selected_output_device.node_name)
                if old_volume is None:
                    return
                new_volume = max(0, min(self.bounds, old_volume + adjustment))
                set_volume_output(output_device, new_volume)

            except Exception as e:
                log.error(e)
                self.show_error(1)

        elif self.mode == Modes.APPLICATION.value or self.mode == Modes.GAME.value:
            if self.selected_application is None:
                self.show_error(1)
                return

            try:
                device = get_application(self.selected_application.restore_id)
                old_volume = get_volume_from_application(self.selected_application.restore_id)
                if old_volume is None:
                    return
                new_volume = max(0, min(self.bounds, old_volume + adjustment))
                set_volume_application(device, new_volume)

            except Exception as e:
                log.error(e)
                self.show_error(1)

        self.display_device_info()

    def on_volume_adjust_change(self, widget, value, old):
        self.adjust = value
        self.display_device_info()

    def on_volume_bound_change(self, widget, value, old):
        self.bounds = value

    def on_mute(self, event):
        if self.mode == Modes.APPLICATION.value or self.mode == Modes.GAME.value:
            if self.selected_application is None:
                return

            try:
                device = get_application(self.selected_application.restore_id)
                if device is not None:
                    self.mute(device)
            except Exception as e:
                log.error(f"Error while muting: {e}")
                self.show_error(1)

    def on_game_change(self):
        super().on_game_change()
        self.update_mute_state()

    ########### UI STUFF ###########

    def update_mute_state(self):
        with pulsectl.Pulse(f"mute-event") as pulse:
            try:
                if self.selected_application is None:
                    self.is_muted = False
                else:
                    device = get_application(self.selected_application.restore_id)
                    if device is None:
                        self.is_muted = False
                    else:
                        if self.selected_application.mute_on_game_launch:
                            self.selected_application.mute_on_game_launch = False
                            self.is_muted = True
                            mute(device, True)
                        else:
                            self.is_muted = bool(device.mute)

                self.set_current_icon()

            except Exception as e:
                log.error(f"Error while updating mute image: {e}")
                self.show_error(1)

    def set_current_icon(self):
        if self.is_muted:
            self._current_icon = self.get_icon(Icons.MUTED)
            self._icon_name = Icons.MUTED
        else:
            self._current_icon = self.get_icon(Icons.UNMUTED)
            self._icon_name = Icons.UNMUTED

        self.display_icon()

    def display_adjustment(self):
        return f"{"+" if self.adjust > 0 else ""}{self.adjust}"

    def mute(self, device):
        self.is_muted = not device.mute

        self.set_current_icon()

        mute(device, self.is_muted)

    async def on_pulse_device_change(self, *args, **kwargs):
        await super().on_pulse_device_change(*args, **kwargs)
        self.update_mute_state()