"""
Reporte de triage. HTML autocontenido (funciona sin red, sin CDN) + CSV.
Diseñado para leerse rápido: el revisor busca las filas que necesitan su
atención, no un informe bonito.
"""

from __future__ import annotations

import csv
import html
import json
from datetime import datetime
from pathlib import Path

STATUS_LABEL = {
    "VERIFIED": "Verificada",
    "METADATA_MISMATCH": "Metadatos no coinciden",
    "POSSIBLE_MATCH": "Coincidencia parcial",
    "NOT_FOUND": "No encontrada",
    "UNPARSEABLE": "No se pudo leer",
}
NEEDS_REVIEW = {"METADATA_MISMATCH", "POSSIBLE_MATCH", "NOT_FOUND", "UNPARSEABLE"}

CSS = """
:root{
  --ink:#16181d; --dim:#6b7280; --rule:#dcdfe4; --bg:#f7f7f5; --card:#fff;
  --ok:#2f7d5c; --warn:#b06d15; --bad:#b0342c; --idle:#7c7f87;
}
*{box-sizing:border-box}
body{margin:0;padding:32px 24px 64px;background:var(--bg);color:var(--ink);
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:21px;margin:0 0 4px;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:13px;margin-bottom:28px}
.tally{display:flex;gap:28px;padding:16px 20px;background:var(--card);
  border:1px solid var(--rule);border-radius:6px;margin-bottom:32px;flex-wrap:wrap}
.tally div{min-width:92px}
.tally .n{font:600 24px/1.1 ui-monospace,SFMono-Regular,Menlo,monospace;
  font-variant-numeric:tabular-nums}
.tally .k{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--dim)}
.paper{background:var(--card);border:1px solid var(--rule);border-radius:6px;
  margin-bottom:20px;overflow:hidden}
.paper > header{padding:13px 18px;border-bottom:1px solid var(--rule);
  display:flex;justify-content:space-between;align-items:baseline;gap:16px}
.paper h2{font-size:14px;margin:0;font-weight:600;word-break:break-word}
.meta{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--dim);white-space:nowrap}
table{width:100%;border-collapse:collapse}
td{padding:10px 14px;border-top:1px solid var(--rule);vertical-align:top}
tr:first-child td{border-top:none}
td.num{width:44px;font:12px ui-monospace,Menlo,monospace;color:var(--dim);text-align:right}
td.st{width:190px;font-size:12px;font-weight:600;white-space:nowrap}
.ref{font-size:13px}
.raw{color:var(--dim);font-size:12px;margin-top:5px;word-break:break-word}
.issue{margin-top:6px;font-size:12px;color:var(--bad)}
.found{margin-top:6px;font-size:12px;color:var(--dim)}
.found b{font-weight:600;color:var(--ink)}
tr.VERIFIED td.st{color:var(--ok)}
tr.METADATA_MISMATCH td.st{color:var(--bad)}
tr.NOT_FOUND td.st{color:var(--bad)}
tr.POSSIBLE_MATCH td.st{color:var(--warn)}
tr.UNPARSEABLE td.st{color:var(--idle)}
tr.VERIFIED{background:transparent}
tr.METADATA_MISMATCH,tr.NOT_FOUND{background:#fdf6f5}
tr.POSSIBLE_MATCH{background:#fdfaf3}
.overlap td{font-size:13px}
.sev-ALTO{color:var(--bad);font-weight:600}
.sev-MEDIO{color:var(--warn);font-weight:600}
.sev-BAJO{color:var(--dim)}
footer{color:var(--dim);font-size:12px;margin-top:36px;border-top:1px solid var(--rule);
  padding-top:14px;max-width:70ch}
@media (max-width:640px){td.st{width:auto}body{padding:20px 12px}}
"""

DISCLAIMER = (
    "Este reporte señala referencias que requieren verificación manual; no "
    "determina que una referencia sea falsa. Las bases consultadas son "
    "incompletas: reportes técnicos, libros, normas, actas locales, trabajos "
    "muy recientes y publicaciones en español suelen faltar o estar indexados "
    "con metadatos parciales. Confirme cada caso antes de comunicarlo a los "
    "autores. La verificación de plagio corresponde al portal IEEE CrossCheck "
    "y no está cubierta por esta herramienta."
)


def _e(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def render_html(results: list[dict], overlaps: list, out: Path) -> None:
    tally = {k: 0 for k in STATUS_LABEL}
    for paper in results:
        for r in paper["references"]:
            tally[r["verdict"]["status"]] = tally.get(r["verdict"]["status"], 0) + 1
    total = sum(tally.values()) or 1

    parts = [
        "<!doctype html><html lang=es><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        f"<title>Revisión de referencias</title><style>{CSS}</style>",
        "<div class=wrap><h1>Revisión de referencias — fase 1</h1>",
        (f"<p class=sub>{len(results)} manuscritos · {total} referencias · "
         f"{datetime.now().astimezone():%d/%m/%Y %H:%M}</p>"),
        "<div class=tally>",
    ]
    for k, label in STATUS_LABEL.items():
        parts.append(f"<div><div class=n>{tally.get(k,0)}</div>"
                     f"<div class=k>{_e(label)}</div></div>")
    parts.append("</div>")

    for paper in results:
        refs = paper["references"]
        flagged = sum(1 for r in refs if r["verdict"]["status"] in NEEDS_REVIEW)
        parts.append(
            f"<section class=paper><header><h2>{_e(paper['file'])}</h2>"
            f"<span class=meta>{flagged}/{len(refs)} por revisar</span></header><table>"
        )
        for r in refs:
            v = r["verdict"]
            parts.append(f"<tr class={v['status']}><td class=num>[{r['index']}]</td>")
            parts.append(f"<td class=st>{_e(STATUS_LABEL.get(v['status'], v['status']))}")
            if v.get("source"):
                parts.append(f"<div class=meta style='font-weight:400'>{_e(v['source'])}</div>")
            parts.append("</td><td>")
            parts.append(f"<div class=ref>{_e(r.get('title') or '—')}</div>")
            if r.get("raw"):
                parts.append(f"<div class=raw>{_e(r['raw'][:260])}</div>")
            m = v.get("matched")
            if m:
                au = ", ".join(m.get("authors", [])[:4]) or "—"
                parts.append(
                    f"<div class=found>Registro hallado: <b>{_e(m.get('title'))}</b> · "
                    f"{_e(au)} · {_e(m.get('year') or '—')} · {_e(m.get('venue') or '—')}</div>"
                )
            for issue in v.get("issues", []):
                parts.append(f"<div class=issue>▸ {_e(issue)}</div>")
            parts.append("</td></tr>")
        parts.append("</table></section>")

    if overlaps:
        parts.append("<section class=paper><header><h2>Solapamiento entre "
                     "manuscritos del lote</h2></header><table class=overlap>")
        for p in overlaps:
            parts.append(
                f"<tr><td class='sev-{p.severity}'>{p.severity}</td>"
                f"<td>{_e(p.a)}<br>{_e(p.b)}</td>"
                f"<td class=meta>containment {p.containment:.1%} · "
                f"jaccard {p.jaccard:.1%} · {p.shared_shingles} bloques</td></tr>"
            )
        parts.append("</table></section>")

    parts.append(f"<footer>{DISCLAIMER}</footer></div></html>")
    out.write_text("".join(parts), encoding="utf-8")


def render_csv(results: list[dict], out: Path) -> None:
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["archivo", "ref_n", "estado", "confianza", "fuente",
                    "titulo_citado", "titulo_hallado", "problemas", "raw"])
        for paper in results:
            for r in paper["references"]:
                v = r["verdict"]
                m = v.get("matched") or {}
                w.writerow([
                    paper["file"], r["index"], v["status"],
                    f"{v.get('confidence', 0):.2f}", v.get("source") or "",
                    r.get("title") or "", m.get("title") or "",
                    " | ".join(v.get("issues", [])), r.get("raw", "")[:400],
                ])


def render_json(results: list[dict], out: Path) -> None:
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
