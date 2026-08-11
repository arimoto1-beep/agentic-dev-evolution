"""生成した .pptx を LibreOffice で開いて画像に変換し、見た目を確認するための開発用スクリプト。

LibreOffice で実際に開けること自体の確認も兼ねる。

    python tools/preview_pptx.py build/sample.pptx --outdir build/preview

依存: LibreOffice(soffice) と pypdfium2(開発用)。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from typing import List, Optional

_CANDIDATES = [
    os.environ.get("SOFFICE_PATH", ""),
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "soffice",
]


def find_soffice() -> Optional[str]:
    for candidate in _CANDIDATES:
        if not candidate:
            continue
        if os.path.isfile(candidate):
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    return None


def pptx_to_pdf(soffice: str, pptx_path: str, outdir: str) -> str:
    os.makedirs(outdir, exist_ok=True)
    profile = os.path.abspath(os.path.join(outdir, ".loprofile"))
    subprocess.run(
        [
            soffice,
            "--headless",
            "--norestore",
            f"-env:UserInstallation=file:///{profile.replace(os.sep, '/')}",
            "--convert-to",
            "pdf",
            "--outdir",
            outdir,
            os.path.abspath(pptx_path),
        ],
        check=True,
        capture_output=True,
    )
    pdf = os.path.join(outdir, os.path.splitext(os.path.basename(pptx_path))[0] + ".pdf")
    if not os.path.isfile(pdf):
        raise RuntimeError("LibreOffice が PDF を出力しませんでした")
    return pdf


def pdf_to_png(pdf_path: str, outdir: str, width: int = 1280) -> List[str]:
    import pypdfium2

    document = pypdfium2.PdfDocument(pdf_path)
    paths = []
    for index, page in enumerate(document, start=1):
        scale = width / page.get_width()
        image = page.render(scale=scale).to_pil()
        path = os.path.join(outdir, f"slide{index:02d}.png")
        image.save(path)
        paths.append(path)
    return paths


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="pptx を画像に変換して確認する")
    parser.add_argument("pptx")
    parser.add_argument("--outdir", default="build/preview")
    parser.add_argument("--width", type=int, default=1280)
    args = parser.parse_args(argv)

    soffice = find_soffice()
    if not soffice:
        print("LibreOffice(soffice)が見つかりません", file=sys.stderr)
        return 1

    pdf = pptx_to_pdf(soffice, args.pptx, args.outdir)
    for path in pdf_to_png(pdf, args.outdir, args.width):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
