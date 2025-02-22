import os
import sys
import pickle
import numpy as np
import vtk

from os.path import join as pjoin
import numpy.testing as npt

from fury.data import read_viz_icons, fetch_viz_icons
from fury import window, actor, ui
from fury.data import DATA_DIR
from fury.testing import assert_arrays_equal
from fury.utils import shallow_copy

import itertools
from fury.colormap import distinguishable_colormap
# Allow import, but disable doctests if we don't have dipy
from fury.optpkg import optional_package
dipy, have_dipy, _ = optional_package('dipy')

if have_dipy:
    from dipy.data import get_sphere


class EventCounter(object):
    def __init__(self, events_names=["CharEvent",
                                     "MouseMoveEvent",
                                     "KeyPressEvent",
                                     "KeyReleaseEvent",
                                     "LeftButtonPressEvent",
                                     "LeftButtonReleaseEvent",
                                     "RightButtonPressEvent",
                                     "RightButtonReleaseEvent",
                                     "MiddleButtonPressEvent",
                                     "MiddleButtonReleaseEvent"]):
        # Events to count
        self.events_counts = {name: 0 for name in events_names}

    def count(self, i_ren, _obj, _element):
        """ Simple callback that counts events occurences. """
        self.events_counts[i_ren.event.name] += 1

    def monitor(self, ui_component):
        for event in self.events_counts:
            for obj_actor in ui_component.actors:
                ui_component.add_callback(obj_actor, event, self.count)

    def save(self, filename):
        with open(filename, 'wb') as f:
            pickle.dump(self.events_counts, f, protocol=2)

    @classmethod
    def load(cls, filename):
        event_counter = cls()
        with open(filename, 'rb') as f:
            event_counter.events_counts = pickle.load(f)

        return event_counter

    def check_counts(self, expected):
        npt.assert_equal(len(self.events_counts),
                         len(expected.events_counts))

        # Useful loop for debugging.
        msg = "{}: {} vs. {} (expected)"
        for event, count in expected.events_counts.items():
            if self.events_counts[event] != count:
                print(msg.format(event, self.events_counts[event], count))

        msg = "Wrong count for '{}'."
        for event, count in expected.events_counts.items():
            npt.assert_equal(self.events_counts[event], count,
                             err_msg=msg.format(event))


def test_callback():
    events_name = ["CharEvent", "MouseMoveEvent", "KeyPressEvent",
                   "KeyReleaseEvent", "LeftButtonPressEvent",
                   "LeftButtonReleaseEvent", "RightButtonPressEvent",
                   "RightButtonReleaseEvent", "MiddleButtonPressEvent",
                   "MiddleButtonReleaseEvent"]

    class SimplestUI(ui.UI):
        def __init__(self):
            super(SimplestUI, self).__init__()

        def _setup(self):
            self.actor = vtk.vtkActor2D()

        def _set_position(self, coords):
            self.actor.SetPosition(*coords)

        def _get_size(self):
            return

        def _get_actors(self):
            return [self.actor]

        def _add_to_scene(self, _scene):
            return

    simple_ui = SimplestUI()
    current_size = (900, 600)
    scene = window.Scene()
    show_manager = window.ShowManager(scene,
                                      size=current_size,
                                      title="FURY GridUI")
    show_manager.initialize()
    scene.add(simple_ui)
    event_counter = EventCounter()
    event_counter.monitor(simple_ui)
    events_name = ["{0} 0 0 0 0 0 0 0".format(name) for name in events_name]
    show_manager.play_events("\n".join(events_name))
    npt.assert_equal(len(event_counter.events_counts), len(events_name))


def test_wrong_interactor_style():
    panel = ui.Panel2D(size=(300, 150))
    dummy_scene = window.Scene()
    dummy_show_manager = window.ShowManager(dummy_scene,
                                            interactor_style='trackball')
    npt.assert_raises(TypeError, panel.add_to_scene, dummy_scene)


def test_ui_rectangle_2d():
    window_size = (700, 700)
    show_manager = window.ShowManager(size=window_size)

    rect = ui.Rectangle2D(size=(100, 50))
    rect.position = (50, 80)
    npt.assert_equal(rect.position, (50, 80))

    rect.color = (1, 0.5, 0)
    npt.assert_equal(rect.color, (1, 0.5, 0))

    rect.opacity = 0.5
    npt.assert_equal(rect.opacity, 0.5)

    # Check the rectangle is drawn at right place.
    show_manager.scene.add(rect)
    # Uncomment this to start the visualisation
    # show_manager.start()

    colors = [rect.color]
    arr = window.snapshot(show_manager.scene, size=window_size, offscreen=True)
    report = window.analyze_snapshot(arr, colors=colors)
    npt.assert_equal(report.objects, 1)
    npt.assert_(report.colors_found)

    # Test visibility off.
    rect.set_visibility(False)
    arr = window.snapshot(show_manager.scene, size=window_size, offscreen=True)
    report = window.analyze_snapshot(arr)
    npt.assert_equal(report.objects, 0)


def test_ui_disk_2d():
    window_size = (700, 700)
    show_manager = window.ShowManager(size=window_size)

    disk = ui.Disk2D(outer_radius=20, inner_radius=5)
    disk.position = (50, 80)
    npt.assert_equal(disk.position, (50, 80))

    disk.color = (1, 0.5, 0)
    npt.assert_equal(disk.color, (1, 0.5, 0))

    disk.opacity = 0.5
    npt.assert_equal(disk.opacity, 0.5)

    # Check the rectangle is drawn at right place.
    show_manager.scene.add(disk)
    # Uncomment this to start the visualisation
    # show_manager.start()

    colors = [disk.color]
    arr = window.snapshot(show_manager.scene, size=window_size, offscreen=True)
    report = window.analyze_snapshot(arr, colors=colors)
    npt.assert_equal(report.objects, 1)
    # Should be False because of the offscreen
    npt.assert_equal(report.colors_found, [False])

    # Test visibility off.
    disk.set_visibility(False)
    arr = window.snapshot(show_manager.scene, size=window_size, offscreen=True)
    report = window.analyze_snapshot(arr)
    npt.assert_equal(report.objects, 0)


def test_ui_button_panel(recording=False):
    filename = "test_ui_button_panel"
    recording_filename = pjoin(DATA_DIR, filename + ".log.gz")
    expected_events_counts_filename = pjoin(DATA_DIR, filename + ".pkl")

    # Rectangle
    rectangle_test = ui.Rectangle2D(size=(10, 10))
    another_rectangle_test = ui.Rectangle2D(size=(1, 1))

    # Button
    fetch_viz_icons()

    icon_files = []
    icon_files.append(('stop', read_viz_icons(fname='stop2.png')))
    icon_files.append(('play', read_viz_icons(fname='play3.png')))

    button_test = ui.Button2D(icon_fnames=icon_files)
    button_test.center = (20, 20)

    def make_invisible(i_ren, _obj, button):
        # i_ren: CustomInteractorStyle
        # obj: vtkActor picked
        # button: Button2D
        button.set_visibility(False)
        i_ren.force_render()
        i_ren.event.abort()

    def modify_button_callback(i_ren, _obj, button):
        # i_ren: CustomInteractorStyle
        # obj: vtkActor picked
        # button: Button2D
        button.next_icon()
        i_ren.force_render()

    button_test.on_right_mouse_button_pressed = make_invisible
    button_test.on_left_mouse_button_pressed = modify_button_callback

    button_test.scale((2, 2))
    button_color = button_test.color
    button_test.color = button_color

    # TextBlock
    text_block_test = ui.TextBlock2D()
    text_block_test.message = 'TextBlock'
    text_block_test.color = (0, 0, 0)

    # Panel
    panel = ui.Panel2D(size=(300, 150),
                       position=(290, 15),
                       color=(1, 1, 1), align="right")
    panel.add_element(rectangle_test, (290, 135))
    panel.add_element(button_test, (0.1, 0.1))
    panel.add_element(text_block_test, (0.7, 0.7))
    npt.assert_raises(ValueError, panel.add_element, another_rectangle_test,
                      (10., 0.5))
    npt.assert_raises(ValueError, panel.add_element, another_rectangle_test,
                      (-0.5, 0.5))

    # Assign the counter callback to every possible event.
    event_counter = EventCounter()
    event_counter.monitor(button_test)
    event_counter.monitor(panel.background)

    current_size = (600, 600)
    show_manager = window.ShowManager(size=current_size, title="FURY Button")

    show_manager.scene.add(panel)

    if recording:
        show_manager.record_events_to_file(recording_filename)
        print(list(event_counter.events_counts.items()))
        event_counter.save(expected_events_counts_filename)

    else:
        show_manager.play_events_from_file(recording_filename)
        expected = EventCounter.load(expected_events_counts_filename)
        event_counter.check_counts(expected)


def test_ui_textbox(recording=False):
    filename = "test_ui_textbox"
    recording_filename = pjoin(DATA_DIR, filename + ".log.gz")
    expected_events_counts_filename = pjoin(DATA_DIR, filename + ".pkl")

    # TextBox
    textbox_test = ui.TextBox2D(height=3, width=10, text="Text")

    another_textbox_test = ui.TextBox2D(height=3, width=10, text="Enter Text")
    another_textbox_test.set_message("Enter Text")

    # Assign the counter callback to every possible event.
    event_counter = EventCounter()
    event_counter.monitor(textbox_test)

    current_size = (600, 600)
    show_manager = window.ShowManager(size=current_size, title="FURY TextBox")

    show_manager.scene.add(textbox_test)

    if recording:
        show_manager.record_events_to_file(recording_filename)
        print(list(event_counter.events_counts.items()))
        event_counter.save(expected_events_counts_filename)

    else:
        show_manager.play_events_from_file(recording_filename)
        expected = EventCounter.load(expected_events_counts_filename)
        event_counter.check_counts(expected)


def test_text_block_2d():
    text_block = ui.TextBlock2D()

    def _check_property(obj, attr, values):
        for value in values:
            setattr(obj, attr, value)
            npt.assert_equal(getattr(obj, attr), value)

    _check_property(text_block, "bold", [True, False])
    _check_property(text_block, "italic", [True, False])
    _check_property(text_block, "shadow", [True, False])
    _check_property(text_block, "font_size", range(100))
    _check_property(text_block, "message", ["", "Hello World", "Line\nBreak"])
    _check_property(text_block, "justification", ["left", "center", "right"])
    _check_property(text_block, "position", [(350, 350), (0.5, 0.5)])
    _check_property(text_block, "color", [(0., 0.5, 1.)])
    _check_property(text_block, "background_color", [(0., 0.5, 1.), None])
    _check_property(text_block, "vertical_justification",
                    ["top", "middle", "bottom"])
    _check_property(text_block, "font_family", ["Arial", "Courier"])

    with npt.assert_raises(ValueError):
        text_block.font_family = "Verdana"

    with npt.assert_raises(ValueError):
        text_block.justification = "bottom"

    with npt.assert_raises(ValueError):
        text_block.vertical_justification = "left"


def test_text_block_2d_justification():
    window_size = (700, 700)
    show_manager = window.ShowManager(size=window_size)

    # To help visualize the text positions.
    grid_size = (500, 500)
    bottom, middle, top = 50, 300, 550
    left, center, right = 50, 300, 550
    line_color = (1, 0, 0)

    grid_top = (center, top), (grid_size[0], 1)
    grid_bottom = (center, bottom), (grid_size[0], 1)
    grid_left = (left, middle), (1, grid_size[1])
    grid_right = (right, middle), (1, grid_size[1])
    grid_middle = (center, middle), (grid_size[0], 1)
    grid_center = (center, middle), (1, grid_size[1])
    grid_specs = [grid_top, grid_bottom, grid_left, grid_right,
                  grid_middle, grid_center]
    for spec in grid_specs:
        line = ui.Rectangle2D(size=spec[1], color=line_color)
        line.center = spec[0]
        show_manager.scene.add(line)

    font_size = 60
    bg_color = (1, 1, 1)
    texts = []
    texts += [ui.TextBlock2D("HH", position=(left, top),
                             font_size=font_size,
                             color=(1, 0, 0), bg_color=bg_color,
                             justification="left",
                             vertical_justification="top")]
    texts += [ui.TextBlock2D("HH", position=(center, top),
                             font_size=font_size,
                             color=(0, 1, 0), bg_color=bg_color,
                             justification="center",
                             vertical_justification="top")]
    texts += [ui.TextBlock2D("HH", position=(right, top),
                             font_size=font_size,
                             color=(0, 0, 1), bg_color=bg_color,
                             justification="right",
                             vertical_justification="top")]

    texts += [ui.TextBlock2D("HH", position=(left, middle),
                             font_size=font_size,
                             color=(1, 1, 0), bg_color=bg_color,
                             justification="left",
                             vertical_justification="middle")]
    texts += [ui.TextBlock2D("HH", position=(center, middle),
                             font_size=font_size,
                             color=(0, 1, 1), bg_color=bg_color,
                             justification="center",
                             vertical_justification="middle")]
    texts += [ui.TextBlock2D("HH", position=(right, middle),
                             font_size=font_size,
                             color=(1, 0, 1), bg_color=bg_color,
                             justification="right",
                             vertical_justification="middle")]

    texts += [ui.TextBlock2D("HH", position=(left, bottom),
                             font_size=font_size,
                             color=(0.5, 0, 1), bg_color=bg_color,
                             justification="left",
                             vertical_justification="bottom")]
    texts += [ui.TextBlock2D("HH", position=(center, bottom),
                             font_size=font_size,
                             color=(1, 0.5, 0), bg_color=bg_color,
                             justification="center",
                             vertical_justification="bottom")]
    texts += [ui.TextBlock2D("HH", position=(right, bottom),
                             font_size=font_size,
                             color=(0, 1, 0.5), bg_color=bg_color,
                             justification="right",
                             vertical_justification="bottom")]

    show_manager.scene.add(*texts)

    # Uncomment this to start the visualisation
    # show_manager.start()

    window.snapshot(show_manager.scene, size=window_size, offscreen=True)


def test_ui_line_slider_2d(recording=False):
    filename = "test_ui_line_slider_2d"
    recording_filename = pjoin(DATA_DIR, filename + ".log.gz")
    expected_events_counts_filename = pjoin(DATA_DIR, filename + ".pkl")

    line_slider_2d_test = ui.LineSlider2D(initial_value=-2,
                                          min_value=-5, max_value=5)
    line_slider_2d_test.center = (300, 300)

    # Assign the counter callback to every possible event.
    event_counter = EventCounter()
    event_counter.monitor(line_slider_2d_test)

    current_size = (600, 600)
    show_manager = window.ShowManager(size=current_size,
                                      title="FURY Line Slider")

    show_manager.scene.add(line_slider_2d_test)

    if recording:
        show_manager.record_events_to_file(recording_filename)
        print(list(event_counter.events_counts.items()))
        event_counter.save(expected_events_counts_filename)

    else:
        show_manager.play_events_from_file(recording_filename)
        expected = EventCounter.load(expected_events_counts_filename)
        event_counter.check_counts(expected)


def test_ui_line_double_slider_2d(interactive=False):
    line_double_slider_2d_test = ui.LineDoubleSlider2D(
        center=(300, 300), shape="disk", outer_radius=15, min_value=-10,
        max_value=10, initial_values=(-10, 10))
    npt.assert_equal(line_double_slider_2d_test.handles[0].size, (30, 30))
    npt.assert_equal(line_double_slider_2d_test.left_disk_value, -10)
    npt.assert_equal(line_double_slider_2d_test.right_disk_value, 10)

    if interactive:
        show_manager = window.ShowManager(size=(600, 600),
                                          title="FURY Line Double Slider")
        show_manager.scene.add(line_double_slider_2d_test)
        show_manager.start()

    line_double_slider_2d_test = ui.LineDoubleSlider2D(
        center=(300, 300), shape="square", handle_side=5,
        initial_values=(50, 40))
    npt.assert_equal(line_double_slider_2d_test.handles[0].size, (5, 5))
    npt.assert_equal(line_double_slider_2d_test._values[0], 39)
    npt.assert_equal(line_double_slider_2d_test.right_disk_value, 40)

    if interactive:
        show_manager = window.ShowManager(size=(600, 600),
                                          title="FURY Line Double Slider")
        show_manager.scene.add(line_double_slider_2d_test)
        show_manager.start()


def test_ui_ring_slider_2d(recording=False):
    filename = "test_ui_ring_slider_2d"
    recording_filename = pjoin(DATA_DIR, filename + ".log.gz")
    expected_events_counts_filename = pjoin(DATA_DIR, filename + ".pkl")

    ring_slider_2d_test = ui.RingSlider2D()
    ring_slider_2d_test.center = (300, 300)
    ring_slider_2d_test.value = 90

    # Assign the counter callback to every possible event.
    event_counter = EventCounter()
    event_counter.monitor(ring_slider_2d_test)

    current_size = (600, 600)
    show_manager = window.ShowManager(size=current_size,
                                      title="FURY Ring Slider")

    show_manager.scene.add(ring_slider_2d_test)

    if recording:
        # Record the following events
        # 1. Left Click on the handle and hold it
        # 2. Move to the left the handle and make 1.5 tour
        # 3. Release the handle
        # 4. Left Click on the handle and hold it
        # 5. Move to the right the handle and make 1 tour
        # 6. Release the handle
        show_manager.record_events_to_file(recording_filename)
        print(list(event_counter.events_counts.items()))
        event_counter.save(expected_events_counts_filename)

    else:
        show_manager.play_events_from_file(recording_filename)
        expected = EventCounter.load(expected_events_counts_filename)
        event_counter.check_counts(expected)


def test_ui_range_slider(interactive=False):
    range_slider_test = ui.RangeSlider(shape="square")

    if interactive:
        show_manager = window.ShowManager(size=(600, 600),
                                          title="FURY Line Double Slider")
        show_manager.scene.add(range_slider_test)
        show_manager.start()


def test_ui_option(interactive=False):
    option_test = ui.Option(label="option 1", position=(10, 10))

    npt.assert_equal(option_test.checked, False)

    if interactive:
        showm = window.ShowManager(size=(600, 600))
        showm.scene.add(option_test)
        showm.start()


def test_ui_checkbox(interactive=False):
    filename = "test_ui_checkbox"
    recording_filename = pjoin(DATA_DIR, filename + ".log.gz")
    expected_events_counts_filename = pjoin(DATA_DIR, filename + ".pkl")

    checkbox_test = ui.Checkbox(labels=["option 1", "option 2\nOption 2",
                                        "option 3", "option 4"],
                                position=(10, 10))

    old_positions = []
    for option in checkbox_test.options:
        old_positions.append(option.position)
    old_positions = np.asarray(old_positions)
    checkbox_test.position = (100, 100)
    new_positions = []
    for option in checkbox_test.options:
        new_positions.append(option.position)
    new_positions = np.asarray(new_positions)
    npt.assert_allclose(new_positions - old_positions,
                        90.0 * np.ones((4, 2)))

    # Collect the sequence of options that have been checked in this list.
    selected_options = []

    def _on_change(checkbox):
        selected_options.append(list(checkbox.checked))

    # Set up a callback when selection changes
    checkbox_test.on_change = _on_change

    event_counter = EventCounter()
    event_counter.monitor(checkbox_test)

    # Create a show manager and record/play events.
    show_manager = window.ShowManager(size=(600, 600),
                                      title="FURY Checkbox")
    show_manager.scene.add(checkbox_test)

    # Recorded events:
    #  1. Click on button of option 1.
    #  2. Click on button of option 2.
    #  3. Click on button of option 1.
    #  4. Click on text of option 3.
    #  5. Click on text of option 1.
    #  6. Click on button of option 4.
    #  7. Click on text of option 1.
    #  8. Click on text of option 2.
    #  9. Click on text of option 4.
    #  10. Click on button of option 3.
    show_manager.play_events_from_file(recording_filename)
    expected = EventCounter.load(expected_events_counts_filename)
    event_counter.check_counts(expected)

    # Check if the right options were selected.
    expected = [['option 1'], ['option 1', 'option 2\nOption 2'],
                ['option 2\nOption 2'], ['option 2\nOption 2', 'option 3'],
                ['option 2\nOption 2', 'option 3', 'option 1'],
                ['option 2\nOption 2', 'option 3', 'option 1', 'option 4'],
                ['option 2\nOption 2', 'option 3', 'option 4'],
                ['option 3', 'option 4'], ['option 3'], []]
    npt.assert_equal(len(selected_options), len(expected))
    assert_arrays_equal(selected_options, expected)
    del show_manager

    if interactive:
        checkbox_test = ui.Checkbox(labels=["option 1", "option 2\nOption 2",
                                            "option 3", "option 4"],
                                    position=(100, 100))
        showm = window.ShowManager(size=(600, 600))
        showm.scene.add(checkbox_test)
        showm.start()


def test_ui_radio_button(interactive=False):
    filename = "test_ui_radio_button"
    recording_filename = pjoin(DATA_DIR, filename + ".log.gz")
    expected_events_counts_filename = pjoin(DATA_DIR, filename + ".pkl")

    radio_button_test = ui.RadioButton(
        labels=["option 1", "option 2\nOption 2", "option 3", "option 4"],
        position=(10, 10))

    old_positions = []
    for option in radio_button_test.options:
        old_positions.append(option.position)
    old_positions = np.asarray(old_positions)
    radio_button_test.position = (100, 100)
    new_positions = []
    for option in radio_button_test.options:
        new_positions.append(option.position)
    new_positions = np.asarray(new_positions)
    npt.assert_allclose(new_positions - old_positions,
                        90 * np.ones((4, 2)))

    selected_option = []

    def _on_change(radio_button):
        selected_option.append(radio_button.checked)

    # Set up a callback when selection changes
    radio_button_test.on_change = _on_change

    event_counter = EventCounter()
    event_counter.monitor(radio_button_test)

    # Create a show manager and record/play events.
    show_manager = window.ShowManager(size=(600, 600),
                                      title="FURY Checkbox")
    show_manager.scene.add(radio_button_test)

    # Recorded events:
    #  1. Click on button of option 1.
    #  2. Click on button of option 2.
    #  3. Click on button of option 2.
    #  4. Click on text of option 2.
    #  5. Click on button of option 1.
    #  6. Click on text of option 3.
    #  7. Click on button of option 4.
    #  8. Click on text of option 4.
    show_manager.play_events_from_file(recording_filename)
    expected = EventCounter.load(expected_events_counts_filename)
    event_counter.check_counts(expected)

    # Check if the right options were selected.
    expected = [['option 1'], ['option 2\nOption 2'], ['option 2\nOption 2'],
                ['option 2\nOption 2'], ['option 1'], ['option 3'],
                ['option 4'], ['option 4']]
    npt.assert_equal(len(selected_option), len(expected))
    assert_arrays_equal(selected_option, expected)
    del show_manager

    if interactive:
        radio_button_test = ui.RadioButton(
            labels=["option 1", "option 2\nOption 2", "option 3", "option 4"],
            position=(100, 100))
        showm = window.ShowManager(size=(600, 600))
        showm.scene.add(radio_button_test)
        showm.start()


def test_ui_listbox_2d(interactive=False):

    filename = "test_ui_listbox_2d"
    recording_filename = pjoin(DATA_DIR, filename + ".log.gz")
    expected_events_counts_filename = pjoin(DATA_DIR, filename + ".pkl")

    # Values that will be displayed by the listbox.
    values = list(range(1, 42 + 1))

    if interactive:
        listbox = ui.ListBox2D(values=values,
                               size=(500, 500),
                               multiselection=True,
                               reverse_scrolling=False)
        listbox.center = (300, 300)

        show_manager = window.ShowManager(size=(600, 600),
                                          title="FURY ListBox")
        show_manager.scene.add(listbox)
        show_manager.start()

    # Recorded events:
    #  1. Click on 1
    #  2. Ctrl + click on 2,
    #  3. Ctrl + click on 2.
    #  4. Use scroll bar to scroll to the bottom.
    #  5. Click on 42.
    #  6. Use scroll bar to scroll to the top.
    #  7. Click on 1
    #  8. Use mouse wheel to scroll down.
    #  9. Shift + click on 42.
    # 10. Use mouse wheel to scroll back up.

    listbox = ui.ListBox2D(values=values,
                           size=(500, 500),
                           multiselection=True,
                           reverse_scrolling=False)
    listbox.center = (300, 300)

    # We will collect the sequence of values that have been selected.
    selected_values = []

    def _on_change():
        selected_values.append(list(listbox.selected))

    # Set up a callback when selection changes.
    listbox.on_change = _on_change

    # Assign the counter callback to every possible event.
    event_counter = EventCounter()
    event_counter.monitor(listbox)

    show_manager = window.ShowManager(size=(600, 600),
                                      title="FURY ListBox")
    show_manager.scene.add(listbox)
    show_manager.play_events_from_file(recording_filename)
    expected = EventCounter.load(expected_events_counts_filename)
    event_counter.check_counts(expected)

    # Check if the right values were selected.
    expected = [[1], [1, 2], [1], [42], [1], values]
    npt.assert_equal(len(selected_values), len(expected))
    assert_arrays_equal(selected_values, expected)

    # Test without multiselection enabled.
    listbox.multiselection = False
    del selected_values[:]  # Clear the list.
    show_manager.play_events_from_file(recording_filename)

    # Check if the right values were selected.
    expected = [[1], [2], [2], [42], [1], [42]]
    npt.assert_equal(len(selected_values), len(expected))
    assert_arrays_equal(selected_values, expected)


def test_ui_image_container_2d(interactive=False):
    fetch_viz_icons()
    image_test = ui.ImageContainer2D(
        img_path=read_viz_icons(fname='home3.png'))

    image_test.center = (300, 300)
    npt.assert_equal(image_test.size, (100, 100))

    image_test.scale((2, 2))
    npt.assert_equal(image_test.size, (200, 200))

    current_size = (600, 600)
    show_manager = window.ShowManager(size=current_size, title="FURY Button")
    show_manager.scene.add(image_test)
    if interactive:
        show_manager.start()


@npt.dec.skipif(not have_dipy)
def test_timer():
    """Testing add a timer and exit window and app from inside timer."""
    xyzr = np.array([[0, 0, 0, 10], [100, 0, 0, 50], [300, 0, 0, 100]])
    xyzr2 = np.array([[0, 200, 0, 30], [100, 200, 0, 50], [300, 200, 0, 100]])
    colors = np.array([[1, 0, 0, 0.3], [0, 1, 0, 0.4], [0, 0, 1., 0.45]])

    scene = window.Scene()

    sphere_actor = actor.sphere(centers=xyzr[:, :3], colors=colors[:],
                                radii=xyzr[:, 3])

    sphere = get_sphere('repulsion724')

    sphere_actor2 = actor.sphere(centers=xyzr2[:, :3], colors=colors[:],
                                 radii=xyzr2[:, 3], vertices=sphere.vertices,
                                 faces=sphere.faces.astype('i8'))

    scene.add(sphere_actor)
    scene.add(sphere_actor2)

    tb = ui.TextBlock2D()

    cnt = 0

    showm = window.ShowManager(scene,
                               size=(1024, 768), reset_camera=False,
                               order_transparent=True)

    showm.initialize()

    def timer_callback(_obj, _event):
        timer_callback.cnt += 1
        timer_callback.tb.message = "Let's count to 10 and exit :" + \
            str(timer_callback.cnt)
        timer_callback.showm.render()
        if timer_callback.cnt > 9:
            timer_callback.showm.exit()
            timer_callback.showm.destroy_timers()

    scene.add(tb)

    # abuse of function attribute
    timer_callback.tb = tb
    timer_callback.cnt = cnt
    timer_callback.showm = showm

    # Run every 200 milliseconds
    showm.add_timer_callback(True, 200, timer_callback)
    showm.start()

    arr = window.snapshot(scene)

    npt.assert_(np.sum(arr) > 0)


def test_ui_file_menu_2d(interactive=False):
    filename = "test_ui_file_menu_2d"
    recording_filename = pjoin(DATA_DIR, filename + ".log.gz")
    expected_events_counts_filename = pjoin(DATA_DIR, filename + ".pkl")

    # Create temporary directory and files
    os.mkdir(os.path.join(os.getcwd(), "testdir"))
    os.chdir("testdir")
    os.mkdir(os.path.join(os.getcwd(), "tempdir"))
    for i in range(10):
        open(os.path.join(os.getcwd(), "tempdir", "test" + str(i) + ".txt"),
             'wt').close()
    open("testfile.txt", 'wt').close()

    filemenu = ui.FileMenu2D(size=(500, 500), extensions=["txt"],
                             directory_path=os.getcwd())

    # We will collect the sequence of files that have been selected.
    selected_files = []

    def _on_change():
        selected_files.append(list(filemenu.listbox.selected))

    # Set up a callback when selection changes.
    filemenu.listbox.on_change = _on_change

    # Assign the counter callback to every possible event.
    event_counter = EventCounter()
    event_counter.monitor(filemenu)

    # Create a show manager and record/play events.
    show_manager = window.ShowManager(size=(600, 600),
                                      title="FURY FileMenu")
    show_manager.scene.add(filemenu)

    # Recorded events:
    #  1. Click on 'testfile.txt'
    #  2. Click on 'tempdir/'
    #  3. Click on 'test0.txt'.
    #  4. Shift + Click on 'test6.txt'.
    #  5. Click on '../'.
    #  2. Click on 'testfile.txt'.
    show_manager.play_events_from_file(recording_filename)
    expected = EventCounter.load(expected_events_counts_filename)
    event_counter.check_counts(expected)

    # Check if the right files were selected.
    expected = [["testfile.txt"], ["tempdir"], ["test0.txt"],
                ["test0.txt", "test1.txt", "test2.txt", "test3.txt",
                 "test4.txt", "test5.txt", "test6.txt"],
                ["../"], ["testfile.txt"]]
    npt.assert_equal(len(selected_files), len(expected))
    assert_arrays_equal(selected_files, expected)

    # Remove temporary directory and files
    os.remove("testfile.txt")
    for i in range(10):
        os.remove(os.path.join(os.getcwd(), "tempdir",
                               "test" + str(i) + ".txt"))
    os.rmdir(os.path.join(os.getcwd(), "tempdir"))
    os.chdir("..")
    os.rmdir("testdir")

    if interactive:
        filemenu = ui.FileMenu2D(size=(500, 500), directory_path=os.getcwd())
        show_manager = window.ShowManager(size=(600, 600),
                                          title="FURY FileMenu")
        show_manager.scene.add(filemenu)
        show_manager.start()


def test_grid_ui(interactive=False):

    vol1 = np.zeros((100, 100, 100))
    vol1[25:75, 25:75, 25:75] = 100

    colors = distinguishable_colormap(nb_colors=3)
    contour_actor1 = actor.contour_from_roi(vol1, np.eye(4),
                                            colors[0], 1.)

    vol2 = np.zeros((100, 100, 100))
    vol2[25:75, 25:75, 25:75] = 100

    contour_actor2 = actor.contour_from_roi(vol2, np.eye(4),
                                            colors[1], 1.)

    vol3 = np.zeros((100, 100, 100))
    vol3[25:75, 25:75, 25:75] = 100

    contour_actor3 = actor.contour_from_roi(vol3, np.eye(4),
                                            colors[2], 1.)

    scene = window.Scene()
    actors = []
    texts = []

    actors.append(contour_actor1)
    text_actor1 = actor.text_3d('cube 1', justification='center')
    texts.append(text_actor1)

    actors.append(contour_actor2)
    text_actor2 = actor.text_3d('cube 2', justification='center')
    texts.append(text_actor2)

    actors.append(contour_actor3)
    text_actor3 = actor.text_3d('cube 3', justification='center')
    texts.append(text_actor3)

    actors.append(shallow_copy(contour_actor1))
    text_actor1 = actor.text_3d('cube 4', justification='center')
    texts.append(text_actor1)

    actors.append(shallow_copy(contour_actor2))
    text_actor2 = actor.text_3d('cube 5', justification='center')
    texts.append(text_actor2)

    actors.append(shallow_copy(contour_actor3))
    text_actor3 = actor.text_3d('cube 6', justification='center')
    texts.append(text_actor3)

    actors.append(shallow_copy(contour_actor1))
    text_actor1 = actor.text_3d('cube 7', justification='center')
    texts.append(text_actor1)

    actors.append(shallow_copy(contour_actor2))
    text_actor2 = actor.text_3d('cube 8', justification='center')
    texts.append(text_actor2)

    actors.append(shallow_copy(contour_actor3))
    text_actor3 = actor.text_3d('cube 9', justification='center')
    texts.append(text_actor3)

    counter = itertools.count()
    show_m = window.ShowManager(scene)
    show_m.initialize()

    def timer_callback(_obj, _event):
        cnt = next(counter)
        show_m.scene.zoom(1)
        show_m.render()
        if cnt == 10:
            show_m.exit()
            show_m.destroy_timers()

    # show the grid with the captions
    grid_ui = ui.GridUI(actors=actors, captions=texts,
                        caption_offset=(0, -50, 0),
                        cell_padding=(60, 60), dim=(3, 3),
                        rotation_axis=(1, 0, 0))

    scene.add(grid_ui)

    show_m.add_timer_callback(True, 200, timer_callback)
    show_m.start()

    arr = window.snapshot(scene)
    report = window.analyze_snapshot(arr)
    npt.assert_equal(report.objects > 9, True)

    # this needs to happen automatically when start() ends.
    for act in actors:
        act.RemoveAllObservers()

    filename = "test_grid_ui"
    recording_filename = pjoin(DATA_DIR, filename + ".log.gz")
    expected_events_counts_filename = pjoin(DATA_DIR, filename + ".pkl")

    current_size = (900, 600)
    scene = window.Scene()
    show_manager = window.ShowManager(scene,
                                      size=current_size,
                                      title="FURY GridUI")
    show_manager.initialize()

    grid_ui2 = ui.GridUI(actors=actors, captions=texts,
                         caption_offset=(0, -50, 0),
                         cell_padding=(60, 60), dim=(3, 3),
                         rotation_axis=None)

    scene.add(grid_ui2)

    event_counter = EventCounter()
    event_counter.monitor(grid_ui2)

    if interactive:
        show_manager.start()
    recording = False

    if recording:
        # Record the following events
        # 1. Left click on top left box (will rotate the box)
        show_manager.record_events_to_file(recording_filename)
        print(list(event_counter.events_counts.items()))
        event_counter.save(expected_events_counts_filename)

    else:
        show_manager.play_events_from_file(recording_filename)
        expected = EventCounter.load(expected_events_counts_filename)
        event_counter.check_counts(expected)


if __name__ == "__main__":
    # test_callback()
    test_timer()
    exit()

    if len(sys.argv) <= 1 or sys.argv[1] == "test_ui_button_panel":
        test_ui_button_panel(recording=False)

    if len(sys.argv) <= 1 or sys.argv[1] == "test_ui_textbox":
        test_ui_textbox(recording=False)

    if len(sys.argv) <= 1 or sys.argv[1] == "test_ui_line_slider_2d":
        test_ui_line_slider_2d(recording=False)

    if len(sys.argv) <= 1 or sys.argv[1] == "test_ui_line_double_slider_2d":
        test_ui_line_double_slider_2d(interactive=False)

    if len(sys.argv) <= 1 or sys.argv[1] == "test_ui_ring_slider_2d":
        test_ui_ring_slider_2d(recording=False)

    if len(sys.argv) <= 1 or sys.argv[1] == "test_ui_range_slider":
        test_ui_range_slider(interactive=False)

    if len(sys.argv) <= 1 or sys.argv[1] == "test_ui_option":
        test_ui_option(interactive=False)

    if len(sys.argv) <= 1 or sys.argv[1] == "test_ui_checkbox":
        test_ui_checkbox(interactive=False)

    if len(sys.argv) <= 1 or sys.argv[1] == "test_ui_radio_button":
        test_ui_radio_button(interactive=False)

    if len(sys.argv) <= 1 or sys.argv[1] == "test_ui_listbox_2d":
        test_ui_listbox_2d(interactive=False)

    if len(sys.argv) <= 1 or sys.argv[1] == "test_ui_image_container_2d":
        test_ui_image_container_2d(interactive=False)

    if len(sys.argv) <= 1 or sys.argv[1] == "test_timer":
        test_timer()

    if len(sys.argv) <= 1 or sys.argv[1] == "test_ui_file_menu_2d":
        test_ui_file_menu_2d(interactive=False)

    if len(sys.argv) <= 1 or sys.argv[1] == "test_grid_ui":
        test_grid_ui(interactive=False)

    if len(sys.argv) <= 1 or sys.argv[1] == "test_ui_disk_2d":
        test_ui_disk_2d()
