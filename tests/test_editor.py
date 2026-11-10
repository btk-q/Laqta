from laqta.ui.editor import MIN_LENGTH, Selection, stamp


def test_handles_cannot_cross_or_leave_the_video():
    s = Selection(60.0)
    s.set_start(20)
    s.set_end(50)
    assert (s.start, s.end) == (20, 50)
    s.set_start(70)  # dragged past the end handle
    assert s.start == 50 - MIN_LENGTH
    s.set_start(-9)
    assert s.start == 0
    s.set_end(-9)  # dragged past the start handle
    assert s.end == MIN_LENGTH
    s.set_end(999)
    assert s.end == 60


def test_pixels_and_seconds_convert_both_ways_and_clamp():
    s = Selection(100.0)
    assert s.time_at(450, 900) == 50 and s.x_at(50, 900) == 450
    assert s.time_at(-30, 900) == 0 and s.time_at(5000, 900) == 100
    assert s.time_at(10, 0) == 100  # a zero-width strip must not divide by zero


def test_the_nearer_handle_is_picked_and_only_within_reach():
    s = Selection(100.0)
    s.set_start(40)
    s.set_end(42)  # handles 18 px apart on a 900 px strip
    assert s.handle_near(362, 900) == "start" and s.handle_near(376, 900) == "end"
    assert s.handle_near(600, 900) is None


def test_markers_are_found_under_the_pointer():
    s = Selection(100.0)
    markers = [{"type": "kill", "offset": 10.0}, {"type": "ace", "offset": 12.0}, {"type": "death", "offset": 80.0}]
    assert s.marker_near(107, 900, markers)["type"] == "ace"  # 12 s is at 108 px
    assert s.marker_near(91, 900, markers)["type"] == "kill"
    assert s.marker_near(400, 900, markers) is None and s.marker_near(10, 900, []) is None


def test_selecting_around_an_event_stays_inside_the_video():
    s = Selection(100.0)
    s.around(50)
    assert (s.start, s.end) == (40, 55)
    s.around(3)  # event right at the beginning
    assert (s.start, s.end) == (0, 8)
    s.around(99)
    assert (s.start, s.end) == (89, 100)


def test_zoom_shows_the_selection_with_room_either_side_and_keeps_conversions_consistent():
    s = Selection(1000.0)
    s.set_start(400)
    s.set_end(415)
    s.zoom()
    assert s.view == (385, 430) and s.zoomed
    assert s.x_at(385, 900) == 0 and s.x_at(430, 900) == 900 and s.x_at(400, 900) == 300
    assert s.time_at(300, 900) == 400 and s.time_at(-50, 900) == 385 and s.time_at(5000, 900) == 430
    assert s.x_at(100, 900) < 0 and s.x_at(900, 900) > 900  # markers outside the view fall off the strip
    s.unzoom()
    assert s.view == (0, 1000) and not s.zoomed and s.x_at(500, 900) == 450


def test_zoom_near_the_edges_and_on_tiny_selections():
    s = Selection(60.0)
    s.set_end(1.0)  # one second at the very start
    s.zoom()
    assert s.view == (0, 4.0)  # at least three seconds of context each side, clamped to the video
    s.unzoom()
    s.set_end(60)
    s.set_start(59)
    s.zoom()
    assert s.view == (56.0, 60.0)


def test_a_broken_duration_still_gives_a_usable_selection():
    s = Selection(0.0)
    assert s.duration == MIN_LENGTH and (s.start, s.end) == (0.0, MIN_LENGTH)


def test_time_stamps():
    assert [stamp(x) for x in (0, 7.46, 75, 754.25, 3725)] == ["0:00.0", "0:07.5", "1:15.0", "12:34.2", "1:02:05.0"]
