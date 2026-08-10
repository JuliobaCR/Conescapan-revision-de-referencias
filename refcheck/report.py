"""
Dashboard de triage. HTML autocontenido (funciona sin red, sin CDN) + CSV.
Diseñado para leerse rápido: el revisor entra buscando qué papers necesitan
su atención, no un informe bonito para archivar.

Paleta de estado fija (good/warning/serious/critical + neutral) — nunca se
reutiliza para otra cosa, y siempre va acompañada de ícono/etiqueta, nunca
solo color. El part-to-whole va en barra apilada, no en dona (más fácil de
comparar valores cercanos).
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

# good / warning / serious / critical (fijo, nunca para otra cosa) + neutral
STATUS_COLOR = {
    "VERIFIED": "#0ca30c",
    "POSSIBLE_MATCH": "#fab219",
    "NOT_FOUND": "#ec835a",
    "METADATA_MISMATCH": "#d03b3b",
    "UNPARSEABLE": "#898781",
}

PAPER_CATEGORY_LABEL = {
    "ERROR_PROCESAMIENTO": "Error de procesamiento",
    "REVISAR_MANUAL": "Revisar manualmente",
    "EXTRACCION_INCOMPLETA": "Extracción incompleta",
    "SIN_VERIFICADAS": "Ninguna verificada",
    "MIXTO": "Parcialmente verificadas",
    "SIN_REFERENCIAS": "Sin referencias detectadas",
    "OK": "Todas verificadas",
}
PAPER_CATEGORY_COLOR = {
    "ERROR_PROCESAMIENTO": "#eb6834",
    "REVISAR_MANUAL": "#4a3aa7",
    "EXTRACCION_INCOMPLETA": "#2a78d6",
    "SIN_VERIFICADAS": "#d03b3b",
    "MIXTO": "#fab219",
    "SIN_REFERENCIAS": "#898781",
    "OK": "#0ca30c",
}

CSS = """
:root{
  --ink:#0b0b0b; --dim:#52514e; --muted:#898781; --rule:#e1e0d9; --bg:#f9f9f7; --card:#fcfcfb;
  --ok:#0ca30c; --warn:#fab219; --bad:#d03b3b; --idle:#898781;
}
*{box-sizing:border-box}
body{margin:0;padding:32px 24px 64px;background:var(--bg);color:var(--ink);
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:21px;margin:0 0 4px;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:13px;margin-bottom:24px}

.kpis{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}
.stat-tile{background:var(--card);border:1px solid var(--rule);border-radius:8px;
  padding:14px 18px;min-width:140px;flex:1}
.stat-value{font:600 26px/1.1 -apple-system,sans-serif;font-variant-numeric:proportional-nums}
.stat-label{font-size:12px;color:var(--dim);margin-top:4px}
.stat-sub{font-size:11px;color:var(--muted);margin-top:2px}

.summary{background:var(--card);border:1px solid var(--rule);border-radius:8px;
  padding:16px 20px;margin-bottom:20px}
.summary h2{font-size:13px;margin:0 0 12px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim)}
.legend{display:flex;gap:16px;flex-wrap:wrap;margin-top:10px;font-size:12px;color:var(--dim)}
.legend-item{display:inline-flex;align-items:center;gap:6px}
.swatch{width:10px;height:10px;border-radius:2px;display:inline-block}

.filters{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:16px;
  background:var(--card);border:1px solid var(--rule);border-radius:8px;padding:10px 14px}
.filters input[type=text]{flex:1;min-width:180px;padding:7px 10px;border:1px solid var(--rule);
  border-radius:6px;font-size:13px}
.filters select{padding:7px 10px;border:1px solid var(--rule);border-radius:6px;font-size:13px}
.filters label{font-size:12px;color:var(--dim);display:inline-flex;align-items:center;gap:5px}
.filters .count{margin-left:auto;font-size:12px;color:var(--dim)}

details.paper{background:var(--card);border:1px solid var(--rule);border-radius:8px;
  margin-bottom:10px;overflow:hidden}
details.paper > summary{padding:12px 16px;cursor:pointer;list-style:none;
  display:flex;align-items:center;gap:14px;flex-wrap:wrap}
details.paper > summary::-webkit-details-marker{display:none}
.p-id{font:600 12px/1 ui-monospace,Menlo,monospace;color:var(--dim);min-width:34px}
.p-title{font-weight:600;flex:1;min-width:220px}
.p-badge{font-size:11px;font-weight:600;padding:3px 8px;border-radius:10px;color:#fff;white-space:nowrap}
.p-comment{width:100%;font-size:12.5px;color:var(--dim);margin-top:2px}
.p-body{padding:0 16px 16px;border-top:1px solid var(--rule)}
table{width:100%;border-collapse:collapse}
td{padding:9px 10px;border-top:1px solid var(--rule);vertical-align:top;font-size:13px}
tr:first-child td{border-top:none}
td.num{width:40px;font:12px ui-monospace,Menlo,monospace;color:var(--dim);text-align:right}
td.st{width:170px;font-size:12px;font-weight:600;white-space:nowrap}
.raw{color:var(--dim);font-size:12px;margin-top:4px;word-break:break-word}
.issue{margin-top:5px;font-size:12px;color:var(--bad)}
.found{margin-top:5px;font-size:12px;color:var(--dim)}
.found b{font-weight:600;color:var(--ink)}
.overlap-note{margin-top:4px;font-size:12px;color:#4a3aa7}

.overlap-section td{font-size:13px}
.sev-ALTO{color:var(--bad);font-weight:600}
.sev-MEDIO{color:var(--warn);font-weight:600}
.sev-BAJO{color:var(--dim)}

footer{color:var(--dim);font-size:12px;margin-top:36px;border-top:1px solid var(--rule);
  padding-top:14px;max-width:70ch}
@media (max-width:640px){body{padding:20px 12px}.p-title{min-width:0}}
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

FILTER_JS = """
(function(){
  var q = document.getElementById('search');
  var catSel = document.getElementById('cat-filter');
  var onlyProblems = document.getElementById('only-problems');
  var cards = Array.prototype.slice.call(document.querySelectorAll('.paper'));
  var countEl = document.getElementById('visible-count');

  function apply() {
    var term = (q.value || '').toLowerCase();
    var cat = catSel.value;
    var visible = 0;
    cards.forEach(function(card) {
      var text = card.dataset.search || '';
      var rowCat = card.dataset.category;
      var flagged = card.dataset.flagged === '1';
      var ok = true;
      if (term && text.indexOf(term) === -1) ok = false;
      if (cat !== 'ALL' && rowCat !== cat) ok = false;
      if (onlyProblems.checked && !flagged) ok = false;
      card.style.display = ok ? '' : 'none';
      if (ok) visible++;
    });
    countEl.textContent = visible;
  }

  q.addEventListener('input', apply);
  catSel.addEventListener('change', apply);
  onlyProblems.addEventListener('change', apply);
  apply();
})();
"""


def _e(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def status_counts(refs: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in refs:
        st = r["verdict"]["status"]
        counts[st] = counts.get(st, 0) + 1
    return counts


def paper_category(paper: dict) -> str:
    if paper.get("processing_error"):
        return "ERROR_PROCESAMIENTO"
    if (paper.get("doc_flags") or {}).get("needs_review"):
        return "REVISAR_MANUAL"
    refs = paper["references"]
    if not refs:
        return "SIN_REFERENCIAS"
    if paper.get("extraction_note"):
        return "EXTRACCION_INCOMPLETA"
    counts = status_counts(refs)
    total = len(refs)
    verified = counts.get("VERIFIED", 0)
    if verified == total:
        return "OK"
    if verified == 0:
        return "SIN_VERIFICADAS"
    return "MIXTO"


def paper_comment(paper: dict) -> str:
    if paper.get("processing_error"):
        return (f"El procesamiento de este paper falló y no se completó: "
                f"{paper['processing_error']}. Revisar el PDF a mano.")

    flags = paper.get("doc_flags") or {}
    if flags.get("needs_review"):
        return "Revisar manualmente: " + "; ".join(flags.get("reasons", [])) + "."

    refs = paper["references"]
    if not refs:
        return "No se detectaron referencias — revisar la extracción a mano."

    extraction_prefix = ""
    if paper.get("extraction_note"):
        extraction_prefix = f"⚠ {paper['extraction_note']}. "

    counts = status_counts(refs)
    total = len(refs)
    bits = [f"{counts.get('VERIFIED', 0)}/{total} verificadas."]
    if counts.get("METADATA_MISMATCH"):
        bits.append(f"{counts['METADATA_MISMATCH']} con metadatos que no coinciden "
                     "(posible cita mal transcripta o fabricada).")
    if counts.get("NOT_FOUND"):
        bits.append(f"{counts['NOT_FOUND']} no encontradas en las bases "
                     "(puede ser literatura local no indexada).")
    if counts.get("POSSIBLE_MATCH"):
        bits.append(f"{counts['POSSIBLE_MATCH']} con coincidencia parcial de título.")
    if counts.get("UNPARSEABLE"):
        bits.append(f"{counts['UNPARSEABLE']} sin texto suficiente para buscar.")
    if paper.get("llm_rescued"):
        bits.append(f"{paper['llm_rescued']} rescatadas por LLM local.")
    return extraction_prefix + " ".join(bits)


def _bar_segments(counts: dict[str, int]) -> list[tuple[str, int]]:
    return [(s, counts[s]) for s in STATUS_LABEL if counts.get(s)]


def stacked_bar_svg(counts: dict[str, int], width: int, height: int = 22,
                     gap: int = 2, radius: int = 3, show_counts: bool = False) -> str:
    """Barra apilada 100% (part-to-whole). Cada segmento lleva <title> como
    tooltip nativo del navegador — sin JS, sin CDN."""
    segs = _bar_segments(counts)
    total = sum(c for _, c in segs) or 1
    if not segs:
        return (f'<svg width="{width}" height="{height}" role="img" '
                 f'aria-label="sin referencias"></svg>')

    n = len(segs)
    usable = max(width - gap * (n - 1), 1)
    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">']
    x = 0.0
    for status, count in segs:
        w = usable * count / total
        label = f"{STATUS_LABEL.get(status, status)}: {count}"
        parts.append(
            f'<rect x="{x:.1f}" y="0" width="{w:.1f}" height="{height}" rx="{radius}" '
            f'ry="{radius}" fill="{STATUS_COLOR[status]}"><title>{_e(label)}</title></rect>'
        )
        if show_counts and w > 26:
            parts.append(
                f'<text x="{x + w / 2:.1f}" y="{height / 2 + 4}" text-anchor="middle" '
                f'font-size="11" fill="#fff" font-weight="600">{count}</text>'
            )
        x += w + gap
    parts.append("</svg>")
    return "".join(parts)


def match_link(matched: dict) -> str | None:
    """Link al registro hallado, si hay algo resoluble. DOI primero — es el
    identificador estándar y sobrevive a que una editorial cambie su URL;
    el "url" que trae cada backend (Crossref/DBLP/OpenAlex/S2/arXiv) es el
    respaldo cuando no hay DOI."""
    doi = matched.get("doi")
    if doi:
        return f"https://doi.org/{doi}"
    url = matched.get("url")
    if url and url.startswith(("http://", "https://")):
        return url
    return None


def status_legend_html() -> str:
    items = "".join(
        f'<span class="legend-item"><span class="swatch" '
        f'style="background:{STATUS_COLOR[s]}"></span>{_e(STATUS_LABEL[s])}</span>'
        for s in STATUS_LABEL
    )
    return f'<div class="legend">{items}</div>'


def _stat_tile(label: str, value, sub: str = "") -> str:
    sub_html = f'<div class="stat-sub">{_e(sub)}</div>' if sub else ""
    return (f'<div class="stat-tile"><div class="stat-value">{_e(value)}</div>'
            f'<div class="stat-label">{_e(label)}</div>{sub_html}</div>')


def render_html(results: list[dict], overlaps: list, out: Path) -> None:
    tally = {k: 0 for k in STATUS_LABEL}
    for paper in results:
        for r in paper["references"]:
            tally[r["verdict"]["status"]] = tally.get(r["verdict"]["status"], 0) + 1
    total_refs = sum(tally.values())

    cat_counts: dict[str, int] = {}
    for paper in results:
        cat = paper_category(paper)
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    # "OK" es la única categoría sin nada para revisar; todo lo demás
    # (incluidos error de procesamiento y extracción incompleta) cuenta.
    needs_review_papers = sum(1 for p in results if paper_category(p) != "OK")

    overlap_partners: dict[str, list[str]] = {}
    for pair in overlaps:
        overlap_partners.setdefault(pair.a, []).append(pair.b)
        overlap_partners.setdefault(pair.b, []).append(pair.a)

    parts = [
        "<!doctype html><html lang=es><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        f"<title>Revisión de referencias — dashboard</title><style>{CSS}</style>",
        "<div class=wrap><h1>Revisión de referencias — dashboard</h1>",
        (f"<p class=sub>{len(results)} manuscritos · {total_refs} referencias · "
         f"{datetime.now().astimezone():%d/%m/%Y %H:%M}</p>"),
    ]

    # ------------------------------------------------------------------ KPIs
    verified_pct = f"{tally.get('VERIFIED', 0) / max(total_refs, 1):.0%}"
    not_found_pct = f"{tally.get('NOT_FOUND', 0) / max(total_refs, 1):.0%}"
    parts.append('<div class=kpis>')
    parts.append(_stat_tile("Manuscritos", len(results)))
    parts.append(_stat_tile("Referencias totales", total_refs))
    parts.append(_stat_tile("Referencias verificadas", verified_pct,
                             f"{tally.get('VERIFIED', 0)} de {total_refs}"))
    parts.append(_stat_tile("No encontradas", tally.get("NOT_FOUND", 0),
                             f"{not_found_pct} del total"))
    parts.append(_stat_tile("Papers a revisar", needs_review_papers,
                             f"de {len(results)} manuscritos"))
    parts.append('</div>')

    # -------------------------------------------------------- barra resumen
    parts.append('<div class=summary><h2>Distribución de referencias por estado</h2>')
    parts.append(stacked_bar_svg(tally, width=1100, height=28, show_counts=True))
    parts.append(status_legend_html())
    parts.append('</div>')

    # ------------------------------------------------------------- filtros
    parts.append(
        '<div class=filters>'
        '<input type=text id=search placeholder="Buscar por #, título o comentario…">'
        '<select id=cat-filter>'
        '<option value=ALL>Todas las categorías</option>'
    )
    for cat, label in PAPER_CATEGORY_LABEL.items():
        parts.append(f'<option value="{cat}">{_e(label)} ({cat_counts.get(cat, 0)})</option>')
    parts.append(
        '</select>'
        '<label><input type=checkbox id=only-problems> Solo con problemas</label>'
        f'<span class=count><span id=visible-count>{len(results)}</span> de {len(results)}</span>'
        '</div>'
    )

    # -------------------------------------------------------------- papers
    for paper in results:
        refs = paper["references"]
        counts = status_counts(refs)
        cat = paper_category(paper)
        comment = paper_comment(paper)
        flagged = 1 if (cat != "OK") else 0
        search_blob = _e(
            f"{paper['submission_id']} {paper['file']} {comment}"
        ).lower()

        parts.append(
            f'<details class=paper data-category="{cat}" data-flagged="{flagged}" '
            f'data-search="{search_blob}">'
        )
        parts.append('<summary>')
        parts.append(f'<span class=p-id>#{_e(paper["submission_id"])}</span>')
        parts.append(f'<span class=p-title>{_e(paper["file"])}</span>')
        parts.append(stacked_bar_svg(counts, width=140, height=16))
        parts.append(
            f'<span class=p-badge style="background:{PAPER_CATEGORY_COLOR[cat]}">'
            f'{_e(PAPER_CATEGORY_LABEL[cat])}</span>'
        )
        parts.append(f'<span class=p-comment>{_e(comment)}</span>')
        if paper["submission_id"] in overlap_partners:
            partners = ", ".join(f"#{p}" for p in overlap_partners[paper["submission_id"]])
            parts.append(
                f'<span class="p-comment overlap-note">▸ posible solapamiento textual '
                f'con {_e(partners)} — ver sección de solapamiento abajo</span>'
            )
        parts.append('</summary>')

        parts.append('<div class=p-body><table>')
        if not refs:
            parts.append('<tr><td colspan=2 style="color:var(--dim)">'
                          'No se extrajeron referencias de este PDF.</td></tr>')
        for r in refs:
            v = r["verdict"]
            parts.append(f'<tr><td class=num>[{r["index"]}]</td>')
            parts.append(f'<td class=st style="color:{STATUS_COLOR.get(v["status"], "inherit")}">'
                         f'{_e(STATUS_LABEL.get(v["status"], v["status"]))}')
            if v.get("source"):
                parts.append(f'<div style="font-weight:400;color:var(--dim);font-size:11px">'
                             f'{_e(v["source"])}</div>')
            parts.append('</td><td>')
            parts.append(f'<div>{_e(r.get("title") or "—")}</div>')
            if r.get("raw"):
                parts.append(f'<div class=raw>{_e(r["raw"][:260])}</div>')
            m = v.get("matched")
            if m:
                au = ", ".join(m.get("authors", [])[:4]) or "—"
                link = match_link(m)
                title_html = (
                    f'<a href="{_e(link)}" target=_blank rel=noopener>{_e(m.get("title"))}</a>'
                    if link else _e(m.get("title"))
                )
                parts.append(
                    f'<div class=found>Registro hallado: <b>{title_html}</b> · '
                    f'{_e(au)} · {_e(m.get("year") or "—")} · {_e(m.get("venue") or "—")}</div>'
                )
            for issue in v.get("issues", []):
                parts.append(f'<div class=issue>▸ {_e(issue)}</div>')
            parts.append('</td></tr>')
        parts.append('</table></div></details>')

    # ------------------------------------------------------------ solapamiento
    if overlaps:
        parts.append('<section class="paper overlap-section" style="margin-top:24px">'
                     '<div class=p-body style="padding-top:16px"><h2 style="font-size:13px;'
                     'text-transform:uppercase;letter-spacing:.06em;color:var(--dim)">'
                     'Solapamiento textual entre manuscritos</h2><table>')
        for p in overlaps:
            parts.append(
                f"<tr><td class='sev-{p.severity}'>{p.severity}</td>"
                f"<td>#{_e(p.a)} ↔ #{_e(p.b)}</td>"
                f"<td style='color:var(--dim)'>containment {p.containment:.1%} · "
                f"jaccard {p.jaccard:.1%} · {p.shared_shingles} bloques</td></tr>"
            )
        parts.append('</table></div></section>')

    parts.append(f"<footer>{DISCLAIMER}</footer>")
    parts.append(f"<script>{FILTER_JS}</script>")
    parts.append("</div></html>")
    out.write_text("".join(parts), encoding="utf-8")


def render_csv(results: list[dict], out: Path) -> None:
    """Detalle por referencia — para triage fino de un paper puntual."""
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["submission_id", "archivo", "ref_n", "estado", "confianza", "fuente",
                    "titulo_citado", "titulo_hallado", "problemas", "raw"])
        for paper in results:
            for r in paper["references"]:
                v = r["verdict"]
                m = v.get("matched") or {}
                w.writerow([
                    paper.get("submission_id", ""), paper["file"], r["index"], v["status"],
                    f"{v.get('confidence', 0):.2f}", v.get("source") or "",
                    r.get("title") or "", m.get("title") or "",
                    " | ".join(v.get("issues", [])), r.get("raw", "")[:400],
                ])


def render_papers_csv(results: list[dict], out: Path) -> None:
    """Un renglón por paper — para la vista de "qué reviso primero"."""
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["submission_id", "archivo", "categoria", "refs_totales",
                    "refs_verificadas", "refs_a_revisar", "revisar_manual",
                    "motivo_revision_manual", "extraccion_incompleta",
                    "error_procesamiento", "rescatadas_llm", "comentario"])
        for paper in results:
            refs = paper["references"]
            counts = status_counts(refs)
            flags = paper.get("doc_flags") or {}
            w.writerow([
                paper.get("submission_id", ""), paper["file"], paper_category(paper),
                len(refs), counts.get("VERIFIED", 0),
                sum(1 for r in refs if r["verdict"]["status"] in NEEDS_REVIEW),
                flags.get("needs_review", False),
                " | ".join(flags.get("reasons", [])),
                paper.get("extraction_note") or "",
                paper.get("processing_error") or "",
                paper.get("llm_rescued", 0), paper_comment(paper),
            ])


def render_json(results: list[dict], out: Path) -> None:
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
