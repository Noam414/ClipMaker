import numpy as np
import pytest

from clipmaker.reframe import (Box, Reframe, camera_path, crop_expression, detect_subjects, face_tracks,
                               pick_speaker, simplify, skin_box)
from clipmaker.render import Crop


def _frame(cx=240, cy=100, w=60, h=80, size=(270, 480)):
    img = np.zeros((*size, 3), np.uint8)
    img[:] = (56, 42, 30)  # dark blue-grey background (BGR)
    img[cy - h // 2: cy + h // 2, cx - w // 2: cx + w // 2] = (160, 201, 242)  # skin tone
    return img


def test_skin_box_finds_face_patch():
    b = skin_box(_frame(cx=100, cy=120))
    assert b is not None
    assert abs(b.cx - 100) <= 1 and abs(b.cy - 120) <= 1
    assert abs(b.w - 60) <= 1


def test_skin_box_none_on_plain_background():
    img = np.zeros((270, 480, 3), np.uint8)
    img[:] = (56, 42, 30)
    assert skin_box(img) is None


class _FakeDetector:
    def __init__(self, boxes):
        self.boxes = list(boxes)

    def detect(self, frame):
        return self.boxes.pop(0)


def test_detect_subjects_prefers_faces_and_largest():
    frames = [_frame()] * 5
    det = _FakeDetector([[Box(10, 10, 5, 5), Box(300, 100, 40, 40)], [], [Box(310, 100, 40, 40)],
                         [Box(310, 100, 40, 40)], [Box(312, 100, 40, 40)]])
    info = {}
    boxes = detect_subjects(frames, det, info)
    assert info["source"] == "face"  # the tiny one-off face is not a second person
    assert boxes[0].cx == 300 and boxes[1] is None and boxes[2].cx == 310


def test_detect_subjects_falls_back_to_skin_when_no_faces():
    info = {}
    boxes = detect_subjects([_frame(cx=100)] * 5, _FakeDetector([[]] * 5), info)
    assert info["source"] == "skin"
    assert all(b is not None and abs(b.cx - 100) <= 1 for b in boxes)


def test_camera_path_follows_and_clamps():
    times = [i / 4 for i in range(40)]
    centers = [200 + 20 * i for i in range(40)]  # moves right across a 1920 frame
    offs = camera_path(times, centers, [100] * 40, crop_len=608, frame_len=1920)
    assert min(offs) >= 0 and max(offs) <= 1920 - 608
    for c, o in zip(centers, offs):
        assert o <= c - 50 and c + 50 <= o + 608  # subject fully inside


def test_camera_path_is_smooth_under_jitter():
    rng = np.random.default_rng(0)
    centers = list(960 + rng.normal(0, 15, 60))
    offs = camera_path([i / 4 for i in range(60)], centers, [100] * 60, 608, 1920)
    assert np.std(np.diff(offs)) < 5


def test_camera_path_fills_gaps_and_resets_at_cut():
    times = [i / 4 for i in range(20)]
    centers = [400.0] * 10 + [1500.0] * 10
    centers[3] = None
    offs = camera_path(times, centers, [100] * 20, 608, 1920, cuts=[2.4])
    assert offs[3] == pytest.approx(offs[2])
    assert offs[9] == pytest.approx(400 - 304)  # no pre-pan before the cut
    assert offs[10] == pytest.approx(1500 - 304)


def test_camera_path_without_detections_centres():
    offs = camera_path([0, 0.25, 0.5], [None] * 3, [None] * 3, 608, 1920)
    assert offs == pytest.approx([656.0] * 3)


def test_simplify_keeps_corners_only():
    pts = [(t, 0.0) for t in range(5)] + [(t, float(t - 4) * 10) for t in range(5, 10)]
    out = simplify(pts, tol=1.0)
    assert out[0] == pts[0] and out[-1] == pts[-1] and (4, 0.0) in out and len(out) == 3


def test_crop_expression_static_and_piecewise():
    assert crop_expression([(10, 100.0), (12, 100.2)], 10) == "100.0"
    expr = crop_expression([(10, 0.0), (12, 100.0)], 10)
    assert "lt(t,0.000)*0.0" in expr and "gte(t,2.000)*100.0" in expr and "50.000" in expr


def test_reframe_track_and_filter():
    r = Reframe(Crop(656, 0, 608, 1080), [(5.0, 600.0), (7.0, 700.0)], "x")
    assert r.track() == [[5.0, 600, 0, 608, 1080], [7.0, 700, 0, 608, 1080]]
    x, y = r.filter_xy(5.0)
    assert x.startswith("'") and y == "0"
    static = Reframe(Crop(0, 0, 1080, 1920), [(0.0, 0.0)], "")
    assert static.filter_xy(0) == ("0", "0")


def test_yunet_detects_fixture_face(tmp_path):
    import importlib.util
    from pathlib import Path

    cv2 = pytest.importorskip("cv2")
    from clipmaker.reframe import FaceDetector

    det = FaceDetector()
    if not det.available:
        pytest.skip("YuNet model not downloadable here")
    spec = importlib.util.spec_from_file_location(
        "mtv", Path(__file__).resolve().parents[1] / "scripts" / "make_test_videos.py")
    mtv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mtv)
    png = tmp_path / "face.png"
    assert mtv.draw_face(png, 220, 280)
    canvas = np.zeros((1080, 1920, 3), np.uint8)
    canvas[:] = (56, 42, 30)
    canvas[400:680, 800:1020] = cv2.imread(str(png))
    faces = det.detect(cv2.resize(canvas, (480, 270)))
    assert len(faces) == 1
    assert abs(faces[0].cx * 4 - 910) < 20 and abs(faces[0].cy * 4 - 540) < 40


def test_face_tracks_groups_by_position():
    faces = [[Box(100, 100, 40, 40), Box(380, 100, 40, 40)]] * 4 + [[Box(104, 100, 40, 40)]]
    tracks = face_tracks(faces)
    assert len(tracks) == 2
    assert all(b is not None for b in tracks[0]) and tracks[1][4] is None


def test_pick_speaker_switches_with_hysteresis():
    a = np.array([5.0] * 12 + [0.0] * 12)
    b = np.array([0.0] * 12 + [5.0] * 12)
    b[3] = 20.0  # a single noisy spike must not switch
    who = pick_speaker([a, b])
    assert who[:10] == [0] * 10
    assert who[-8:] == [1] * 8
    assert sum(1 for i in range(1, len(who)) if who[i] != who[i - 1]) == 1


def _two_face_frames(active, n=16):
    frames = []
    for i in range(n):
        img = _frame(cx=80, cy=120)
        img[90:150, 370:430] = (160, 201, 242)
        if i % 2:  # the active speaker's mouth opens and closes
            x = 80 if active == 0 else 400
            img[135:145, x - 10:x + 10] = (20, 20, 60)
        frames.append(img)
    return frames


def test_detect_subjects_follows_active_speaker():
    frames = _two_face_frames(active=1)
    det = _FakeDetector([[Box(80, 120, 60, 60), Box(400, 120, 60, 60)]] * len(frames))
    info = {}
    boxes = detect_subjects(frames, det, info, crop_frac=0.32)
    assert info["source"] == "speakers"
    assert all(b.cx == 400 for b in boxes)


def test_detect_subjects_frames_group_that_fits():
    det = _FakeDetector([[Box(200, 120, 40, 40), Box(280, 120, 40, 40)]] * 4)
    info = {}
    boxes = detect_subjects([_frame()] * 4, det, info, crop_frac=0.32)
    assert info["source"] == "group" and boxes[0].cx == 240 and boxes[0].w == 120
