"""人が動画を、説明のまとまりごとに短く確かめるための束を作る。**見たことにはならない(見るのは人)。**

    .venv/Scripts/python.exe tools/review_pack.py 動画の一覧.json 出力フォルダ [--notes 確認メモ.json]
        [--scenario 台本.md] [--width 1280] [--full] [--only 番号 ...]

* まとまりは章(`章:`)。章は場面の境目で始まるので、文の途中では切れない
* 各まとまりの前後に、前の章の最後の 1 文と次の章の最初の 1 文を「確認対象外」として付ける(流れを判断するため)。
  付けるのは字幕の切れ目で、文末(。？！)に届くまで。文の途中から始まらない
* 字幕は絵の下の帯に焼き込む。絵の上には重ならない(外部字幕は、再生する道具によって絵の下部に重なる。gen39c)
* `index.html` を開くと、まとまりごとに再生・判定・メモができる。判定・メモ・再生位置はブラウザに残るので、
  中断しても「続きから」で戻れる。メモは Markdown にまとめてコピーできる

確認メモ(任意)は、場面ごとに何を変えたか・何を見てほしいかを書いた JSON:

    {"title": "...", "general": ["..."],
     "scenes": {"10b_knock": {"changed": "...", "check": ["...", "..."]}}}

場面の名前は絵のファイル名(`10b_knock@1.png` の `10b_knock`)。
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from note2slides.captions import chapter_time  # noqa: E402
from note2slides.console import use_utf8_output  # noqa: E402

SENTENCE_END = ("。", "？", "！", "?", "!", "」")
LEAD_MAX_CUES = 4  # 前後に付ける字幕の数の上限(長い文でも、ここで打ち切る)
FPS = 30


@dataclass
class Cue:
    start: float
    end: float
    text: str


@dataclass
class Scene:
    index: int  # 動画の中の何枚目か(1 から)
    name: str  # 絵の名前
    title: str
    start: float
    duration: float


@dataclass
class Unit:
    number: int
    title: str
    start: float  # 確かめる範囲(元の動画の秒)
    end: float
    scenes: List[Scene]
    lead_in: float = 0.0  # 前に付けた秒数
    lead_out: float = 0.0
    notes: List[dict] = field(default_factory=list)

    @property
    def clip_start(self) -> float:
        return self.start - self.lead_in

    @property
    def clip_end(self) -> float:
        return self.end + self.lead_out

    @property
    def slug(self) -> str:
        return f"{self.number:02d}"


def parse_time(text: str) -> float:
    h, m, rest = text.strip().split(":")
    s, ms = rest.replace(".", ",").split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_srt(text: str) -> List[Cue]:
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = block.split("\n")
        at = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if at is None:
            continue
        start, end = (parse_time(part) for part in lines[at].split("-->"))
        cues.append(Cue(start, end, "\n".join(lines[at + 1 :]).strip()))
    return cues


def scene_titles(scenario: Optional[str], count: int) -> List[str]:
    """台本の場面の題(`## 題`)。数が合わなければ使わない(空を返す)。"""
    if not scenario:
        return []
    text = open(scenario, encoding="utf-8").read()
    text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)
    titles = re.findall(r"^## (.+?)\s*$", text, flags=re.M)
    return titles if len(titles) == count else []


def scene_name(slide: dict) -> str:
    shots = slide.get("shots") or []
    image = shots[0]["image"] if shots else slide.get("image", "")
    return os.path.splitext(os.path.basename(image))[0].split("@")[0]


def build_units(listing: dict, titles: Sequence[str] = ()) -> List[Unit]:
    slides = listing["slides"]
    scenes = [Scene(s["index"], scene_name(s), titles[k] if titles else scene_name(s), s["start"], s["duration"])
              for k, s in enumerate(slides)]
    total = listing.get("duration") or (scenes[-1].start + scenes[-1].duration)
    chapters = listing.get("chapters") or [{"start": 0.0, "title": "全体"}]
    units = []
    for k, chapter in enumerate(chapters):
        start = chapter["start"]
        end = chapters[k + 1]["start"] if k + 1 < len(chapters) else total
        inside = [s for s in scenes if start - 0.05 <= s.start < end - 0.05]
        units.append(Unit(k + 1, chapter["title"], start, end, inside))
    return units


def _ends_sentence(text: str) -> bool:
    return text.replace("\n", "").rstrip().endswith(SENTENCE_END)


def lead_in(cues: Sequence[Cue], at: float) -> Tuple[float, List[Cue]]:
    """`at` の前に付ける字幕(文の頭から)。返すのは(付ける秒数, 字幕)。"""
    before = [c for c in cues if c.end <= at + 0.05]
    if not before:
        return 0.0, []
    picked = [before[-1]]
    k = len(before) - 1
    while k > 0 and not _ends_sentence(before[k - 1].text) and len(picked) < LEAD_MAX_CUES:
        k -= 1
        picked.insert(0, before[k])
    return max(0.0, at - picked[0].start + 0.3), picked


def lead_out(cues: Sequence[Cue], at: float, total: float) -> Tuple[float, List[Cue]]:
    """`at` のあとに付ける字幕(文末まで)。"""
    after = [c for c in cues if c.start >= at - 0.05]
    picked = []
    for cue in after:
        picked.append(cue)
        if _ends_sentence(cue.text) or len(picked) >= LEAD_MAX_CUES:
            break
    if not picked:
        return 0.0, []
    return max(0.0, min(total, picked[-1].end + 0.5) - at), picked


def attach_notes(units: Sequence[Unit], notes: dict) -> None:
    by_scene = notes.get("scenes", {})
    for unit in units:
        unit.notes = [dict(scene=s.name, title=s.title, **by_scene[s.name]) for s in unit.scenes if s.name in by_scene]


def band_segments(unit: Unit, cues: Sequence[Cue]) -> List[Tuple[float, float, str, str]]:
    """帯の時間割り(クリップの頭を 0 とする秒)。(始まり, 終わり, 字幕, 種類 = before / main / after)。
    隙間なく 0 からクリップの終わりまでを埋める。"""
    t0, t1 = unit.clip_start, unit.clip_end
    marks = {0.0, t1 - t0, unit.start - t0, unit.end - t0}
    shown = [c for c in cues if c.end > t0 and c.start < t1]
    for c in shown:
        marks.update({max(0.0, c.start - t0), min(t1 - t0, c.end - t0)})
    edges = sorted(m for m in marks if 0.0 <= m <= t1 - t0)
    segments = []
    for a, b in zip(edges, edges[1:]):
        if b - a < 1e-3:
            continue
        mid = t0 + (a + b) / 2
        text = next((c.text for c in shown if c.start <= mid < c.end), "")
        kind = "before" if mid < unit.start else "after" if mid >= unit.end else "main"
        if segments and segments[-1][2] == text and segments[-1][3] == kind:
            segments[-1] = (segments[-1][0], b, text, kind)
        else:
            segments.append((a, b, text, kind))
    return segments


def clock(seconds: float) -> str:
    """分:秒。章立て(概要欄)と同じ書き方にする(同じ時刻を指すため)。"""
    return chapter_time(seconds)


# -- 書き出す ---------------------------------------------------------------


def draw_band(path: str, width: int, height: int, header: str, text: str, kind: str) -> None:
    from note2slides.sketch import Sketch

    sk = Sketch(width, height, background=(24, 30, 42))
    scale = width / 1280
    side = {"before": "◀ 前の章の終わり(流れの確認用。確認対象外)", "after": "次の章の始まり(流れの確認用。確認対象外) ▶"}.get(kind)
    sk.text((16 * scale, 18 * scale), header, size=17 * scale, color=(150, 160, 178), anchor="lm", halo=False)
    if side:
        sk.text((width - 16 * scale, 18 * scale), side, size=17 * scale, color=(240, 196, 120), anchor="rm",
                halo=False)
    color = (255, 255, 255) if kind == "main" else (150, 160, 178)
    if text:
        sk.text((width / 2, height / 2 + 14 * scale), text, size=33 * scale, color=color, halo=False)
    sk.image().save(path)


def write_clip(ffmpeg: str, video: str, unit: Unit, cues: Sequence[Cue], folder: str, width: int, header: str,
               name: str) -> str:
    band_h = int(round(width * 0.12 / 2)) * 2
    work = os.path.join(folder, "bands", name)
    os.makedirs(work, exist_ok=True)
    cache: Dict[Tuple[str, str], str] = {}
    lines = ["ffconcat version 1.0"]
    for a, b, text, kind in band_segments(unit, cues):
        key = (text, kind)
        if key not in cache:
            cache[key] = os.path.join(work, f"band_{len(cache):03d}.png")
            draw_band(cache[key], width, band_h, header, text, kind)
        lines += [f"file '{os.path.basename(cache[key])}'", f"duration {b - a:.3f}"]
    lines.append(lines[-2])  # 最後の絵は、もう一度書かないと長さが効かない
    concat = os.path.join(work, "bands.txt")
    open(concat, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    out = os.path.join(folder, f"{name}.mp4")
    graph = (f"[0:v]scale={width}:-2,setsar=1[v];[1:v]fps={FPS},scale={width}:{band_h},setsar=1[b];"
             f"[v][b]vstack=inputs=2,format=yuv420p[o]")
    subprocess.run([ffmpeg, "-v", "error", "-y", "-ss", f"{unit.clip_start:.3f}", "-t",
                    f"{unit.clip_end - unit.clip_start:.3f}", "-i", video, "-f", "concat", "-safe", "0", "-i", concat,
                    "-filter_complex", graph, "-map", "[o]", "-map", "0:a", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "23", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-shortest", out],
                   check=True)
    return out


def unit_record(unit: Unit) -> dict:
    return {
        "number": unit.number,
        "title": unit.title,
        "clip": f"clips/{unit.slug}.mp4",
        "start": round(unit.start, 3),
        "end": round(unit.end, 3),
        "lead_in": round(unit.lead_in, 3),
        "lead_out": round(unit.lead_out, 3),
        "scenes": [{"name": s.name, "title": s.title, "index": s.index, "start": round(s.start, 3),
                    "at": round(s.start - unit.clip_start, 3)} for s in unit.scenes],
        "notes": unit.notes,
    }


def write_markdown(path: str, title: str, units: Sequence[Unit], general: Sequence[str]) -> None:
    out = [f"# 確認の手引き: {title}", "",
           "`index.html` をブラウザで開くと、まとまりごとに再生・判定・メモができます(ブラウザに残るので中断できます)。",
           "このファイルは同じ内容の文字版です。", ""]
    if general:
        out += ["## 全体について", ""] + [f"* {g}" for g in general] + [""]
    out += ["## まとまり", "", "| # | 章 | 元の動画 | 長さ | クリップ | 変えた場面 |", "| --- | --- | --- | --- | --- | --- |"]
    for u in units:
        changed = "、".join(n["title"] for n in u.notes) or "-"
        out.append(f"| {u.number} | {u.title} | {clock(u.start)} - {clock(u.end)} | {clock(u.end - u.start)} | "
                   f"`clips/{u.slug}.mp4` | {changed} |")
    out.append("")
    for u in units:
        if not u.notes:
            continue
        out += [f"## {u.number}. {u.title}", ""]
        for n in u.notes:
            out.append(f"* **{n['title']}**(元の動画 {clock(next(s.start for s in u.scenes if s.name == n['scene']))})")
            if n.get("changed"):
                out.append(f"  * 変えたこと: {n['changed']}")
            for c in n.get("check", []):
                out.append(f"  * [ ] {c}")
        out.append("")
    open(path, "w", encoding="utf-8", newline="\n").write("\n".join(out))


def write_html(path: str, title: str, units: Sequence[Unit], general: Sequence[str], pack_id: str) -> None:
    data = json.dumps({"title": title, "id": pack_id, "general": list(general),
                       "units": [unit_record(u) for u in units]}, ensure_ascii=False)
    page = _PAGE.replace("__TITLE__", html.escape(title)).replace("__DATA__", data.replace("</", "<\\/"))
    open(path, "w", encoding="utf-8", newline="\n").write(page)


_PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><title>確認: __TITLE__</title>
<style>
body{font-family:"Meiryo","Yu Gothic",sans-serif;margin:0;background:#f4f6f9;color:#1d2433}
header{background:#1d2433;color:#fff;padding:10px 18px;display:flex;gap:16px;align-items:center}
header h1{font-size:18px;margin:0;flex:1}
button{font:inherit;padding:6px 12px;border-radius:6px;border:1px solid #9aa4b5;background:#fff;cursor:pointer}
button.primary{background:#2a6fdb;color:#fff;border-color:#2a6fdb}
main{display:flex;gap:16px;padding:16px}
nav{width:300px;flex:none}
nav .u{padding:8px 10px;margin-bottom:6px;border-radius:8px;background:#fff;cursor:pointer;border:2px solid transparent}
nav .u.cur{border-color:#2a6fdb}
nav .u small{color:#6b7385;display:block}
.badge{font-size:12px;border-radius:10px;padding:1px 7px;margin-left:4px}
.ok{background:#dff3e8;color:#1d7a4e}.ng{background:#fde6e6;color:#b42d2d}.todo{background:#e8ebf0;color:#6b7385}
.chg{background:#fff1d6;color:#9a6200}
section{flex:1;min-width:0}
video{width:100%;max-width:1280px;background:#000;border-radius:6px}
.scenes button{margin:2px 4px 2px 0;font-size:13px}
.note{background:#fff;border-radius:8px;padding:10px 14px;margin:10px 0}
.note h3{margin:0 0 6px;font-size:15px}
textarea{width:100%;max-width:1280px;height:90px;font:inherit}
.verdict label{margin-right:14px}
#export{display:none;width:100%;height:200px}
</style></head><body>
<header><h1>確認: __TITLE__</h1><span id="progress"></span>
<button class="primary" id="resume">続きから</button><button id="copy">メモをまとめる</button></header>
<main><nav id="list"></nav><section>
<div id="general"></div>
<h2 id="utitle"></h2><p id="urange"></p>
<video id="player" controls preload="metadata"></video>
<p class="scenes" id="scenes"></p>
<div id="notes"></div>
<p class="verdict">判定:
<label><input type="radio" name="v" value="todo">未</label>
<label><input type="radio" name="v" value="ok">OK</label>
<label><input type="radio" name="v" value="ng">気になる</label>
<button id="next">次のまとまりへ</button></p>
<textarea id="memo" placeholder="気になったこと(時刻を書くときは、上の「元の動画」の時刻か、場面の名前を添えてください)"></textarea>
<textarea id="export" readonly></textarea>
</section></main>
<script>
const DATA=__DATA__;
const KEY="review:"+DATA.id;
let state=JSON.parse(localStorage.getItem(KEY)||"{}");
state.units=state.units||{};
let cur=null;
const $=id=>document.getElementById(id);
const clock=s=>{s=Math.max(0,Math.floor(s));return Math.floor(s/60)+":"+String(s%60).padStart(2,"0")};
const save=()=>localStorage.setItem(KEY,JSON.stringify(state));
const st=n=>state.units[n]=state.units[n]||{verdict:"todo",memo:"",pos:0};
function list(){
  $("list").innerHTML="";
  let done=0;
  DATA.units.forEach(u=>{
    const s=st(u.number);if(s.verdict!=="todo")done++;
    const d=document.createElement("div");d.className="u"+(cur===u.number?" cur":"");
    d.innerHTML=`<b>${u.number}. ${u.title}</b><span class="badge ${s.verdict}">${{todo:"未",ok:"OK",ng:"気になる"}[s.verdict]}</span>`+
      (u.notes.length?`<span class="badge chg">変更 ${u.notes.length}</span>`:"")+
      `<small>元の動画 ${clock(u.start)} - ${clock(u.end)}(${clock(u.end-u.start)})</small>`;
    d.onclick=()=>open(u.number);$("list").appendChild(d);
  });
  $("progress").textContent=`${done} / ${DATA.units.length} 確認済み`;
}
function open(n,resume){
  const u=DATA.units.find(x=>x.number===n);cur=n;state.last=n;save();
  const s=st(n),p=$("player");
  $("utitle").textContent=`${u.number}. ${u.title}`;
  $("urange").textContent=`元の動画 ${clock(u.start)} - ${clock(u.end)}。前後の ${u.lead_in.toFixed(0)} 秒 / ${u.lead_out.toFixed(0)} 秒は、流れを見るために付けた前後の章です(帯に「確認対象外」と出ます)。`;
  p.src=u.clip;p.onloadedmetadata=()=>{if(resume&&s.pos>1&&s.pos<p.duration-2)p.currentTime=s.pos;};
  $("scenes").innerHTML="場面: ";
  u.scenes.forEach(sc=>{const b=document.createElement("button");
    b.textContent=`${clock(sc.start)} ${sc.title}`;b.onclick=()=>{p.currentTime=sc.at;p.play();};$("scenes").appendChild(b);});
  $("notes").innerHTML=u.notes.map(x=>`<div class="note"><h3>変えた場面: ${x.title}</h3>`+
    (x.changed?`<p>${x.changed}</p>`:"")+(x.check&&x.check.length?"<b>見てほしいこと</b><ul>"+x.check.map(c=>`<li>${c}</li>`).join("")+"</ul>":"")+"</div>").join("");
  document.querySelectorAll("input[name=v]").forEach(r=>r.checked=r.value===s.verdict);
  $("memo").value=s.memo;list();
}
document.querySelectorAll("input[name=v]").forEach(r=>r.onchange=()=>{st(cur).verdict=r.value;save();list();});
$("memo").oninput=()=>{st(cur).memo=$("memo").value;save();};
$("player").ontimeupdate=()=>{const s=st(cur);if(Math.abs(s.pos-$("player").currentTime)>2){s.pos=$("player").currentTime;save();}};
$("player").onpause=()=>{st(cur).pos=$("player").currentTime;save();};
$("next").onclick=()=>{const k=DATA.units.findIndex(x=>x.number===cur);if(k+1<DATA.units.length)open(DATA.units[k+1].number,true);};
$("resume").onclick=()=>{const u=DATA.units.find(x=>st(x.number).verdict==="todo");open((u||DATA.units[0]).number,true);};
$("copy").onclick=()=>{
  const lines=[`# 確認メモ: ${DATA.title}`,""];
  DATA.units.forEach(u=>{const s=st(u.number);
    lines.push(`## ${u.number}. ${u.title}(元の動画 ${clock(u.start)} - ${clock(u.end)}): ${{todo:"未確認",ok:"OK",ng:"気になる"}[s.verdict]}`);
    if(s.memo.trim())lines.push("",s.memo.trim());lines.push("");});
  const e=$("export");e.style.display="block";e.value=lines.join("\\n");e.select();
  if(navigator.clipboard)navigator.clipboard.writeText(e.value).catch(()=>{});
};
if(DATA.general.length)$("general").innerHTML='<div class="note"><h3>全体について</h3><ul>'+DATA.general.map(g=>`<li>${g}</li>`).join("")+"</ul></div>";
open(state.last||DATA.units[0].number,true);
</script></body></html>
"""


def main(argv: Optional[Sequence[str]] = None) -> int:
    use_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("listing", help="動画と一緒に書き出される 名前_video.json")
    parser.add_argument("out", help="出力フォルダ")
    parser.add_argument("--notes", help="確認メモ(JSON)")
    parser.add_argument("--scenario", help="台本(場面の題を取る)")
    parser.add_argument("--width", type=int, default=1280, help="クリップの幅(既定 1280)")
    parser.add_argument("--full", action="store_true", help="全体を 1 本にした、帯つきの動画も作る")
    parser.add_argument("--only", type=int, nargs="*", help="この番号のまとまりのクリップだけを作り直す")
    args = parser.parse_args(argv)

    import imageio_ffmpeg

    listing = json.load(open(args.listing, encoding="utf-8"))
    base = os.path.dirname(os.path.abspath(args.listing))
    video = os.path.join(base, listing["file"])
    srt = next((os.path.join(base, c) for c in listing.get("captions", []) if c.endswith(".srt")), None)
    cues = parse_srt(open(srt, encoding="utf-8").read()) if srt else []
    total = listing["duration"]
    notes = json.load(open(args.notes, encoding="utf-8")) if args.notes else {}
    title = notes.get("title") or os.path.splitext(listing["file"])[0]
    units = build_units(listing, scene_titles(args.scenario, len(listing["slides"])))
    for unit in units:
        unit.lead_in = lead_in(cues, unit.start)[0] if unit.start > 0 else 0.0
        unit.lead_out = lead_out(cues, unit.end, total)[0] if unit.end < total - 0.1 else 0.0
    attach_notes(units, notes)

    clips = os.path.join(args.out, "clips")
    os.makedirs(clips, exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    for unit in units:
        if args.only and unit.number not in args.only:
            continue
        header = f"{unit.number}/{len(units)}  {unit.title}   元の動画 {clock(unit.start)} - {clock(unit.end)}"
        path = write_clip(ffmpeg, video, unit, cues, clips, args.width, header, unit.slug)
        print(f"{unit.number}. {unit.title}: {clock(unit.end - unit.start)}"
              f"(前 {unit.lead_in:.1f} 秒 / 後 {unit.lead_out:.1f} 秒を付けた) -> {os.path.relpath(path, args.out)}",
              flush=True)
    if args.full:
        whole = Unit(0, "全体", 0.0, total, [])
        path = write_clip(ffmpeg, video, whole, cues, args.out, args.width, f"全体  {clock(total)}", "full")
        print(f"全体 -> {os.path.relpath(path, args.out)}", flush=True)
    stamp = f"{listing['file']}:{total}:{listing.get('size_bytes', '')}"
    write_html(os.path.join(args.out, "index.html"), title, units, notes.get("general", []), stamp)
    write_markdown(os.path.join(args.out, "REVIEW.md"), title, units, notes.get("general", []))
    json.dump({"title": title, "units": [unit_record(u) for u in units]},
              open(os.path.join(args.out, "units.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"入口: {os.path.join(args.out, 'index.html')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
