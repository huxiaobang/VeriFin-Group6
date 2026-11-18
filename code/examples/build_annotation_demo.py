"""Rebuild only the synthetic annotation-editor preview; no gold is created."""
from pathlib import Path
import json

from verifin.annotation_form import render_annotation_form


def main():
    folder = Path(__file__).resolve().parent
    visible = json.loads((folder / "annotation-demo-input.json").read_text(encoding="utf-8"))
    draft = json.loads((folder / "annotation-demo-draft.json").read_text(encoding="utf-8"))
    if draft["independent"] is not False or draft["completed_at"] is not None:
        raise ValueError("The demo must remain an unsigned, incomplete fixture")
    for phase in ("baseline", "candidate"):
        answer = draft["assessment"][phase]
        if answer and any(row["verdict"] is not None for row in answer["units"] + answer["assertions"]):
            raise ValueError("Do not put completed judgments in the demo")
    html = render_annotation_form(visible, draft)
    html = html.replace("<title>VeriFin · 独立人工标注</title>", "<title>DEMO · VeriFin 标注编辑器 · 非人工标注</title>")
    html = html.replace("<header><h1>VeriFin · 独立人工标注</h1>",
                        "<header><h1>DEMO · VeriFin 标注编辑器</h1><p><strong>仅供功能预览：虚构材料，所有人工判断均为空；不是人工标注、真实财报或实验结果。</strong></p>")
    output = folder / "annotation-demo.html"
    output.write_text(html, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
