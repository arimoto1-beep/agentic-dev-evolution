"""繰り返しの継ぎ目と、広げた動きの倍率を調べる道具(Gen39)。"""

import importlib.util
import json
import os

from PIL import Image, ImageDraw

_PATH = os.path.join(os.path.dirname(__file__), "..", "tools", "motion_check.py")
_SPEC = importlib.util.spec_from_file_location("motion_check", _PATH)
motion_check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(motion_check)


def bar_at(path, x):
    image = Image.new("RGB", (480, 270), (255, 255, 255))
    ImageDraw.Draw(image).rectangle((x, 0, x + 60, 270), fill=(0, 0, 0))
    image.save(path)


def write_loop(folder, positions):
    directory = folder / "fig@1" / "loop"
    directory.mkdir(parents=True)
    for index, x in enumerate(positions):
        bar_at(directory / f"{index:03d}.png", x)


def test_jump_at_the_seam_is_found(tmp_path):
    write_loop(tmp_path, [10 * k for k in range(30)])  # 右へ進み、最後に左端へ飛ぶ
    count, jumps = motion_check.seams(str(tmp_path))
    assert count == 1
    assert len(jumps) == 1


def test_closed_loop_has_no_jump(tmp_path):
    write_loop(tmp_path, [10 * k for k in range(15)] + [150 - 10 * k for k in range(15)])
    assert motion_check.seams(str(tmp_path))[1] == []


def test_stretch_ratio_is_listed_from_the_largest(tmp_path):
    listing = tmp_path / "video.json"
    listing.write_text(json.dumps({"slides": [{
        "index": 3, "duration": 10.0,
        "shots": [{"at": 0.0, "cue": "0"}, {"at": 1.0, "cue": "1", "fit": True, "motion_frames": 30},
                  {"at": 4.0, "cue": "2", "fit": True, "motion_frames": 360}],
    }]}), encoding="utf-8")
    assert motion_check.stretches(str(listing)) == [(3.0, 3, "1"), (0.5, 3, "2")]
