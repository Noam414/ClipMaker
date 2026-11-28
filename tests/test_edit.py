"""M5: edit a clip's caption text / title / trim and re-render it (D-031)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clipmaker.config import Settings
from clipmaker.edit import EditError, check_trim, rerender_clip, retime_words


def _words(text, t0=0.0, step=1.0):
    return [{"word": w, "start": t0 + i * step, "end": t0 + i * step + 0.8} for i, w in enumerate(text.split())]


def test_retime_keeps_matching_words_and_spreads_changes():
    old = _words("so this is the best idea ever")
    same = retime_words(old, "So this is the BEST idea ever!")
    assert [(w["start"], w["end"]) for w in same] == [(w["start"], w["end"]) for w in old]
    assert same[-1]["word"] == "ever!"
    new = retime_words(old, "this is the greatest idea ever")  # delete + replace
    assert [w["word"] for w in new][3] == "greatest" and new[3]["start"] == 4.0 and new[3]["end"] == 4.8
    ins = retime_words(old, "Wow so this is the best idea ever folks")  # inserts at both ends
    assert [w["word"] for w in ins] == "Wow so this is the best idea ever folks".split()
    assert ins[0]["start"] == 0.0 and ins[-1]["end"] == 6.8
    assert all(a["end"] <= b["start"] + 1e-6 for a, b in zip(ins, ins[1:]))  # in order, no overlap
    words = retime_words(old, "a completely different sentence")  # nothing matches
    assert words[0]["start"] == 0.0 and words[-1]["end"] == 6.8
    with pytest.raises(EditError):
        retime_words(old, "   ")


def test_retime_hebrew_text():
    old = _words("זה הרעיון הכי טוב")
    new = retime_words(old, "זה הרעיון הכי טוב בעולם")
    assert [w["word"] for w in new] == "זה הרעיון הכי טוב בעולם".split()
    assert new[0]["start"] == 0.0 and new[-1]["end"] == 3.8


def test_check_trim_limits():
    assert check_trim(1, 10, 0, 20) == (1.0, 10.0)
    for bad in [(-1, 10), (5, 25), (5, 6), (0, 100), ("x", 4)]:
        with pytest.raises(EditError):
            check_trim(*bad, 0, 200 if bad == (0, 100) else 20)


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    """A project dir with a 20 s source video, a hand-made transcript and one rendered clip."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    pdir = tmp_path_factory.mktemp("proj")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=20",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=20", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(pdir / "source.mp4")], check=True)
    text = "here is the secret nobody tells you about saving money every single month"
    words = _words(text, t0=2.0, step=0.9)
    seg = {"start": words[0]["start"], "end": words[-1]["end"], "text": text, "words": words}
    clip = {"id": "clip_01", "start": 2.0, "end": 15.5, "text": text, "score": 0.8}
    from clipmaker.media import probe
    from clipmaker.pipeline import render_clip_entry

    out = pdir / "clips"
    out.mkdir()
    opts = {"reframe": False, "zoom": False}
    render_clip_entry(clip, pdir / "source.mp4", 0.0, probe(pdir / "source.mp4"), words, [seg], [], out, None, opts)
    (out / "clips.json").write_text(json.dumps({"transcript": {"segments": [seg]}, "clips": [clip]}))
    return pdir, clip, opts


def _ass_text(path: Path) -> str:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith("Dialogue:")]
    return " ".join(re.sub(r"\{[^}]*\}", "", ln.split(",", 9)[-1]) for ln in lines)


def test_rerender_with_new_text_title_and_trim(project):
    from clipmaker.media import probe

    pdir, clip, opts = project
    new = rerender_clip(pdir, "source.mp4", dict(clip), opts, start=4.0, end=12.0,
                        text="Here is THE secret nobody tells you about investing money every single month",
                        title="My own title")
    assert new["file"] == "clip_01_e2.mp4" and new["edit"]["version"] == 2
    assert (new["start"], new["end"], new["title"]) == (4.0, 12.0, "My own title")
    out = pdir / "clips"
    assert not (out / clip["file"]).exists()  # the old render is replaced
    info = probe(out / new["file"])
    assert (info.width, info.height) == (1080, 1920) and 7.0 < info.duration < 9.0
    ass = _ass_text(out / new["captions_file"])
    assert "investing" in ass and "saving" not in ass and "My own title" in ass
    assert "here" not in new["text"].split()[:1] and "investing" in new["text"]  # trimmed text
    saved = json.loads((out / "clips.json").read_text())["clips"][0]
    assert saved["file"] == new["file"]
    # a second edit (title only) keeps the edited words and bumps the version
    again = rerender_clip(pdir, "source.mp4", new, opts, title="Second")
    assert again["file"] == "clip_01_e3.mp4" and again["title"] == "Second"
    assert "investing" in _ass_text(out / again["captions_file"])
    with pytest.raises(EditError):
        rerender_clip(pdir, "source.mp4", again, opts, start=10, end=30)  # past the video's end


def test_web_edit_flow(tmp_path, project):
    """Upload -> clips -> POST /api/clips/<id>/edit -> the worker re-renders -> new file served."""
    from clipmaker.jobs import project_dir

    src_dir, clip, opts = project

    def runner(src, out, progress=None, on_clip=None, **o):
        shutil.copytree(src_dir / "clips", out, dirs_exist_ok=True)
        data = json.loads((out / "clips.json").read_text())
        c = {**clip, "file": "clip_01.mp4"}
        from clipmaker.media import probe
        from clipmaker.pipeline import render_clip_entry

        seg = data["transcript"]["segments"]
        render_clip_entry(c, src, 0.0, probe(src), seg[0]["words"], seg, [], out, None, opts)
        data["clips"] = [c]
        (out / "clips.json").write_text(json.dumps(data))
        return data

    settings = Settings(database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}", data_dir=tmp_path,
                        cache_dir=tmp_path / "cache", host="127.0.0.1", port=0, worker=False)
    from clipmaker.web.app import create_app

    app = create_app(settings, runner=runner)
    client = TestClient(app)
    with open(src_dir / "source.mp4", "rb") as fh:
        pid = client.post("/api/projects", files={"file": ("talk.mp4", fh, "video/mp4")}).json()["project_id"]
    row = client.get(f"/api/projects/{pid}").json()
    assert row["clips"] == []
    assert app.state.worker.run_once() == "done"
    clip_row = client.get(f"/api/projects/{pid}").json()["clips"][0]
    page = client.get(f"/projects/{pid}").text
    assert 'class="edit"' in page and f'data-clip="{clip_row["id"]}"' in page

    bad = client.post(f"/api/clips/{clip_row['id']}/edit", json={"start": 0, "end": 99})
    assert bad.status_code == 400 and "between" in bad.json()["detail"]
    r = client.post(f"/api/clips/{clip_row['id']}/edit", json={"text": "here is the big secret", "start": 2, "end": 10})
    assert r.status_code == 202, r.text
    dup = client.post(f"/api/clips/{clip_row['id']}/edit", json={"title": "x"})
    assert dup.status_code == 409  # one re-render per clip at a time
    assert app.state.worker.run_once() == "done"
    assert client.get(f"/api/jobs/{r.json()['job_id']}").json()["status"] == "done"
    after = client.get(f"/api/projects/{pid}").json()
    assert len(after["clips"]) == 1  # edited in place, not a new clip
    c = after["clips"][0]
    assert c["file"] == "clips/clip_01_e2.mp4" and c["end"] == 10.0 and "big secret" in c["text"]
    assert client.get(c["url"]).status_code == 200
    page = client.get(f"/projects/{pid}").text
    assert "clip_01_e2.mp4" in page  # the gallery still shows the project's clips (edit jobs don't count)
    assert client.get(f"/projects/{pid}/clips.zip").status_code == 200
    assert (project_dir(tmp_path, pid) / "clips" / "clip_01_e2.mp4").is_file()


def test_web_edit_limit_and_unknown(tmp_path, monkeypatch):
    from clipmaker.web.app import create_app

    settings = Settings(database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}", data_dir=tmp_path,
                        cache_dir=tmp_path / "cache", host="127.0.0.1", port=0, worker=False)
    client = TestClient(create_app(settings))
    assert client.post("/api/clips/999/edit", json={"title": "x"}).status_code == 404
