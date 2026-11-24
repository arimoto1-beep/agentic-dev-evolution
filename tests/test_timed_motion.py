"""動きの長さを音声で決める(Gen39)と、部品を描き直す下地・舞台・章の指定。"""

import io
import json
import os

import numpy as np
import pytest
from PIL import Image

from note2slides import captions, frames, narration, rig, stage, staging
from note2slides.markdown_reader import parse_article_file
from note2slides.sketch import Sketch, save_loop, save_motion

GRAY = (120, 120, 120)


def picture(path, value):
    Image.new("RGB", (4, 4), (value, value, value)).save(path)
    return str(path)


def written(plans, size=(4, 4)):
    out = io.BytesIO()
    frames.write_frames(out, plans, size)
    data = np.frombuffer(out.getvalue(), dtype=np.uint8).reshape(-1, size[1], size[0], 3)
    return [int(frame[0, 0, 0]) for frame in data]


def series(tmp_path, name, values):
    folder = tmp_path / name
    folder.mkdir()
    return tuple(picture(folder / f"{i:03d}.png", v) for i, v in enumerate(values))


def test_fit_spreads_motion_to_the_next_cue(tmp_path):
    base = picture(tmp_path / "a.png", 0)
    end = picture(tmp_path / "b.png", 200)
    motion = series(tmp_path, "m", [50, 100, 150, 200])
    shots = [staging.Shot(0.0, base), staging.Shot(1.0, end, "1", motion, fit=True), staging.Shot(3.0, base, "2")]
    plan = frames.plan_frames([(1, 4.0, shots)], 30)[0]
    change = plan.changes[1]
    assert (change.frame, change.spread) == (30, 60)


def test_fit_runs_to_the_end_of_the_slide_when_no_cue_follows(tmp_path):
    base = picture(tmp_path / "a.png", 0)
    motion = series(tmp_path, "m", [50, 100])
    shots = [staging.Shot(0.0, base), staging.Shot(1.0, base, "1", motion, fit=True)]
    plan = frames.plan_frames([(1, 3.0, shots)], 30)[0]
    assert plan.changes[1].spread == 60


def test_fit_ends_exactly_on_the_last_frame(tmp_path):
    base = picture(tmp_path / "a.png", 0)
    motion = series(tmp_path, "m", [50, 100, 150, 200])
    shots = [staging.Shot(0.0, base), staging.Shot(0.1, base, "1", motion, fit=True)]
    values = written(frames.plan_frames([(1, 1.1, shots)], 10))
    assert values == [0, 50, 50, 100, 100, 100, 150, 150, 200, 200, 200]


def test_motion_without_fit_keeps_its_length(tmp_path):
    base = picture(tmp_path / "a.png", 0)
    end = picture(tmp_path / "b.png", 200)
    motion = series(tmp_path, "m", [50, 100])
    shots = [staging.Shot(0.0, base), staging.Shot(0.2, end, "1", motion)]
    plan = frames.plan_frames([(1, 1.0, shots)], 10)[0]
    assert plan.changes[1].spread == 0
    assert written([plan]) == [0, 0, 50, 100, 200, 200, 200, 200, 200, 200]


def test_loop_repeats_until_the_next_change(tmp_path):
    base = picture(tmp_path / "a.png", 0)
    end = picture(tmp_path / "b.png", 200)
    loop = series(tmp_path, "l", [10, 20])
    shots = [staging.Shot(0.0, base), staging.Shot(0.2, base, "1", (), False, loop), staging.Shot(0.8, end, "2")]
    plan = frames.plan_frames([(1, 1.0, shots)], 10, step_fade=0.0)[0]
    assert written([plan]) == [0, 0, 10, 20, 10, 20, 10, 20, 200, 200]


def test_loop_follows_the_motion(tmp_path):
    base = picture(tmp_path / "a.png", 0)
    motion = series(tmp_path, "m", [50, 100])
    loop = series(tmp_path, "l", [10, 20])
    shots = [staging.Shot(0.0, base), staging.Shot(0.1, base, "1", motion, False, loop)]
    assert written(frames.plan_frames([(1, 0.7, shots)], 10)) == [0, 50, 100, 10, 20, 10, 20]


def test_next_slide_fades_from_the_shown_loop_frame(tmp_path):
    base = picture(tmp_path / "a.png", 0)
    white = picture(tmp_path / "b.png", 250)
    loop = series(tmp_path, "l", [100])
    first = [staging.Shot(0.0, base), staging.Shot(0.1, base, "1", (), False, loop)]
    second = [staging.Shot(0.0, white)]
    values = written(frames.plan_frames([(1, 0.3, first), (2, 0.5, second)], 10, slide_fade=0.2))
    assert values[:3] == [0, 100, 100]
    assert 100 < values[3] < 250  # 地の絵ではなく、映っていた絵から溶ける


def staged_slide():
    slide = staging.StagedSlide(index=1, images={"0": "open.png", "1": "one.png"})
    slide.motions = {"1": ["m0.png"]}
    return slide


def test_first_cue_keeps_a_fitted_motion():
    slide = staged_slide()
    slide.fits = ["1"]
    shots = staging.slide_shots("base.png", slide, [{"step": "1", "at": 0.0, "first": True}], lead=0.2)
    assert [shot.cue for shot in shots] == ["0", "1"]
    assert shots[1].fit and shots[1].motion


def test_first_cue_without_fit_shows_no_motion_as_before():
    shots = staging.slide_shots("base.png", staged_slide(), [{"step": "1", "at": 0.0, "first": True}], lead=0.2)
    assert [(shot.cue, shot.motion) for shot in shots] == [("1", ())]


def test_figure_declares_fit_and_loop(tmp_path):
    path = str(tmp_path / "fig.png")
    image = Image.new("RGB", (4, 4), GRAY)
    image.save(path)
    image.save(staging.step_path(path, 1))
    save_motion(path, 1, [image, image], fit=True)
    save_loop(path, 1, [image])
    assert os.path.isfile(staging.fit_mark(path, 1))
    assert len(os.listdir(staging.loop_dir(path, 1))) == 1
    assert len(staging._frames_in(staging.motion_dir(path, 1))) == 2


def test_motion_without_fit_leaves_no_mark(tmp_path):
    path = str(tmp_path / "fig.png")
    save_motion(path, 1, [Image.new("RGB", (4, 4), GRAY)])
    assert not os.path.isfile(staging.fit_mark(path, 1))


def test_save_steps_writes_fit_and_loop(tmp_path):
    path = str(tmp_path / "fig.png")
    sk = Sketch(40, 40)
    image = Image.new("RGB", (40, 40), GRAY)
    sk.save_steps(path, [dict(image=image, motion=[image], fit=True, loop=[image])], opening=dict(image=image))
    assert os.path.isfile(staging.fit_mark(path, 1))
    assert staging._frames_in(staging.loop_dir(path, 1))


def test_manifest_keeps_fit_and_loop(tmp_path):
    outdir = tmp_path / "slides" / staging.STEPS_DIR
    outdir.mkdir(parents=True)
    slide = staging.StagedSlide(index=2, images={"1": str(outdir / "a.png")})
    slide.fits = ["1"]
    slide.loops = {"1": [str(outdir / "l.png")]}
    manifest = {"count": 1, "slides": [slide.to_dict(str(outdir))]}
    (outdir / staging.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    loaded = staging.load_steps(str(tmp_path / "slides"))[2]
    assert loaded.fits == ["1"]
    assert [os.path.basename(p) for p in loaded.loops["1"]] == ["l.png"]


def test_rig_keeps_what_lies_under_the_ground_color():
    base = Image.new("RGB", (40, 40), (255, 255, 255))
    base.paste((10, 20, 30), (0, 0, 20, 40))

    def draw(sk, value):
        sk.rect(30, 0, 10, 40, fill=(200, 0, 0))

    frame = rig.still(base, [rig.Rig((0, 0, 40, 40), draw)], 0.0)
    assert frame.getpixel((5, 5)) == (10, 20, 30)
    assert frame.getpixel((35, 5)) == (200, 0, 0)


def test_run_reaches_the_end_and_closed_stops_short():
    base = Image.new("RGB", (20, 20), (255, 255, 255))
    seen = []
    r = [rig.Rig((0, 0, 20, 20), lambda sk, value: seen.append(value))]
    list(rig.run(base, r, 0.0, 1.0, 0.1, fps=20))
    assert seen == [0.5, 1.0]
    seen.clear()
    list(rig.run(base, r, 0.0, 1.0, 0.1, fps=20, closed=True))
    assert seen == [0.0, 0.5]


def test_run_moves_several_rigs_with_their_own_values():
    base = Image.new("RGB", (20, 20), (255, 255, 255))
    seen = {"a": [], "b": []}
    rigs = [rig.Rig((0, 0, 20, 20), lambda sk, v: seen["a"].append(v)),
            rig.Rig((0, 0, 20, 20), lambda sk, v: seen["b"].append(v))]
    list(rig.run(base, rigs, [0, 10], [1, 10], 0.05, fps=20))
    assert seen == {"a": [1.0], "b": [10.0]}


def test_stage_draws_any_number_of_chapters():
    sk = stage.stage(2, "", "題", chapters=["一", "二", "三", "四", "五", "六", "七"])
    assert sk.image().size == (1920, 1080)


def test_stage_without_chapters_has_no_strip():
    plain = np.asarray(stage.stage().image())
    assert (plain == plain[0, 0]).all()


def test_chapter_survives_the_notes():
    notes = narration.compose_notes("本文です。", chapter="第 1 章: 普通のエンジン")
    body, directives = narration.split_directives(notes, 1)
    assert body.strip() == "本文です。"
    assert directives.chapter == "第 1 章: 普通のエンジン"


def caption(index, start, title, chapter=""):
    return captions.SlideCaption(index=index, start=start, duration=60.0, title=title, chapter=chapter)


def test_chapters_use_only_the_marked_slides():
    slides = [caption(1, 0, "冒頭", "はじめに"), caption(2, 60, "場面A"), caption(3, 120, "場面B", "第一章"),
              caption(4, 180, "場面C"), caption(5, 240, "場面D", "第二章")]
    assert [c.title for c in captions.build_chapters(slides, 300)] == ["はじめに", "第一章", "第二章"]


def test_chapters_start_at_zero_when_the_first_slide_is_unmarked():
    slides = [caption(1, 0, "冒頭"), caption(2, 60, "場面A", "第一章"), caption(3, 120, "場面B", "第二章")]
    chapters = captions.build_chapters(slides, 300)
    assert [(c.start, c.title) for c in chapters] == [(0.0, "冒頭"), (60, "第一章"), (120, "第二章")]


def test_chapters_without_marks_stay_as_before():
    slides = [caption(1, 0, "冒頭"), caption(2, 60, "場面A"), caption(3, 120, "場面B")]
    assert [c.title for c in captions.build_chapters(slides, 300)] == ["冒頭", "場面A", "場面B"]


@pytest.mark.parametrize("name", ["plain.txt"])
def test_plain_text_gets_title_and_headings(tmp_path, name):
    path = tmp_path / name
    path.write_text("題の行\n\n本文です。\n\n■見出しの行\n\n続きです。\n", encoding="utf-8")
    kinds = [type(block).__name__ for block in parse_article_file(str(path)).blocks]
    assert kinds[:2] == ["Heading", "Paragraph"]
    assert kinds.count("Heading") == 2


def test_plain_conversion_leaves_markdown_alone(tmp_path):
    path = tmp_path / "plain.txt"
    path.write_text("## 見出し\n\n■これは本文\n", encoding="utf-8")
    assert sum(type(b).__name__ == "Heading" for b in parse_article_file(str(path)).blocks) == 1


def test_plain_conversion_is_only_for_txt(tmp_path):
    path = tmp_path / "plain.md"
    path.write_text("題の行\n\n■本文のまま\n", encoding="utf-8")
    assert all(type(b).__name__ != "Heading" for b in parse_article_file(str(path)).blocks)
