#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
genera_indice.py — Indice dei contenuti di Cruscotto Italia.

Estrae le etichette VISIBILI da frontend/comune.html (livello A), le unisce ai
sinonimi curati in frontend/indice_sinonimi.json (livello B) e rigenera la
sezione "Cosa cercare e dove" dentro frontend/about.html, fra due marker.

Uso:
    python3 -u scripts/genera_indice.py --check     # non scrive: verifica soltanto
    python3 -u scripts/genera_indice.py --scrivi    # rigenera about.html

--check esce con codice 1 se comune.html espone etichette che l'indice non
copre: serve a impedire che l'indice resti indietro dopo una modifica al FE.
"""

import argparse
import html
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = os.path.join(ROOT, "frontend")
COMUNE = os.path.join(FE, "comune.html")
ABOUT = os.path.join(FE, "about.html")
SINONIMI = os.path.join(FE, "indice_sinonimi.json")

MARK_START = "<!-- INDICE-CONTENUTI:START (generato da scripts/genera_indice.py) -->"
MARK_END = "<!-- INDICE-CONTENUTI:END -->"
CSS_START = "/* INDICE-CONTENUTI-CSS:START (generato da scripts/genera_indice.py) */"
CSS_END = "/* INDICE-CONTENUTI-CSS:END */"

CSS = CSS_START + """
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;
  overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0;}
.indice-acc{border:1px solid var(--border);border-radius:8px;background:var(--bg-soft);
  margin:1.2rem 0;}
.indice-acc>summary{cursor:pointer;padding:.85rem 1rem;font-weight:600;
  color:var(--blu-italia);list-style:none;}
.indice-acc>summary::-webkit-details-marker{display:none;}
.indice-acc>summary::before{content:"\\25B8";display:inline-block;margin-right:.5rem;
  transition:transform .15s ease;}
.indice-acc[open]>summary::before{transform:rotate(90deg);}
.indice-acc>summary:focus-visible{outline:2px solid var(--blu-italia);outline-offset:2px;}
.indice-body{padding:0 1rem 1rem;}
.indice-search-lab{display:block;font-size:.85rem;font-weight:600;margin-bottom:.35rem;}
.indice-search{width:100%;max-width:26rem;padding:.5rem .7rem;font:inherit;
  border:1px solid var(--border);border-radius:6px;background:var(--bg);}
.indice-search:focus-visible{outline:2px solid var(--blu-italia);outline-offset:1px;}
.indice-count{font-size:.85rem;color:var(--ink-soft,#555);margin:.5rem 0 .75rem;}
.indice-tab-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;}
.indice-tab{width:100%;border-collapse:collapse;font-size:.92rem;}
.indice-tab th,.indice-tab td{text-align:left;padding:.4rem .6rem;
  border-bottom:1px solid var(--border-soft);vertical-align:top;}
.indice-tab thead th{position:sticky;top:0;background:var(--bg);
  border-bottom:2px solid var(--border);}
.indice-tab tbody th{font-weight:600;width:52%;}
.indice-tab tbody td{color:var(--ink-soft,#444);}
.indice-empty{padding:.75rem;font-style:italic;}
@media (max-width:640px){
  .indice-tab{font-size:.86rem;}
  .indice-tab tbody th{width:50%;}
}
""" + CSS_END

# ---------------------------------------------------------------- tab e ancore

TAB_LABEL = {
    "panoramica": "Panoramica", "anncsu": "ANNCSU e Catasto", "aria": "Meteo e Aria",
    "banda-larga": "Banda larga", "beni-culturali": "Beni culturali",
    "censimento": "Censimento 2023", "contratti": "Contratti ANAC",
    "demografia": "Demografia", "carburanti": "Distributori e prezzi",
    "asia": "Imprese e addetti", "opere": "Opere", "patrimonio-pa": "Patrimonio PA",
    "pendolarismo": "Pendolarismo", "pnrr": "PNRR", "profilo": "Profilo",
    "pun": "Punti di ricarica", "redditi": "Redditi e fisco", "sanita": "Sanità",
    "scuole": "Scuole", "spese": "SIOPE", "territorio": "Territorio",
    "runts": "Terzo Settore", "turismo": "Turismo", "veicoli": "Veicoli e incidenti",
}

# funzione contenitore top-level -> tab. Le funzioni successive ereditano
# l'ultima ancora vista, finche' non ne compare un'altra.
ANCORE = {
    "renderComune": "panoramica", "renderOpereDettaglioContent": "opere",
    "renderDemografiaTabSkeleton": "demografia", "renderProfiloTabSkeleton": "profilo",
    "renderTurismoTabSkeleton": "turismo", "renderPnrrTabSkeleton": "pnrr",
    "renderTerritorioTabSkeleton": "territorio", "renderSpeseTabSkeleton": "spese",
    "renderContrattiTab": "contratti", "renderScuoleTab": "scuole",
    "renderRedditiTab": "redditi", "renderImmobiliPaTab": "patrimonio-pa",
    "renderBeniCulturaliTab": "beni-culturali", "renderPunTab": "pun",
    "renderBandaLargaTab": "banda-larga", "renderCarburantiTab": "carburanti",
    "renderPunFilters": "pun", "renderSanitaTab": "sanita",
    "renderAnncsuTab": "anncsu", "renderCensimentoTab": "censimento",
    "renderVeicoliTab": "veicoli", "renderOpereTab": "opere",
    "renderAriaTabSkeleton": "aria", "renderPendolarismoTab": "pendolarismo",
    "renderRuntsTab": "runts", "renderAsiaTab": "asia",
    "initSanitaMap": "sanita", "renderSanitaMapBlock": "sanita", "miniRender": "sanita",
    "initPendolarismoMap": "pendolarismo",
    # blocchi non riferibili a una tab: esclusi
    "adaptDashboardToOverview": "_infra", "populateCachesFromDashboard": "_infra",
    "updateTabBadgesFromDashboard": "_infra", "setupDownloadButton": "_infra",
    "init": "_infra", "setupTabsScrollHint": "_infra", "enhanceTabsA11y": "_infra",
    "bindTabSwitching": "_infra", "addChartDataTables": "_infra",
    "mcpCall": "_infra", "ensureComuniIndex": "_infra",
}

# etichette troncate dal markup -> forma leggibile
FIX = {
    "Meteo e qualita dell'": "Meteo e qualita dell'aria",
    "Meteo e qualità dell'": "Meteo e qualità dell'aria",
    "Precipitazioni (24h": "Precipitazioni 24h",
    "Vento (10m": "Vento a 10 metri",
    "Scuola dell": "Scuola dell'infanzia",
    "Strade, civici e": "Strade, civici e catasto",
    "Capacita ricettiva e": "Capacita ricettiva",
    "Capacità ricettiva e": "Capacità ricettiva",
    "Farmacie, parafarmacie e": "Farmacie, parafarmacie e ospedali",
    "Frane e": "Frane e alluvioni",
    "Produzione e": "Produzione e raccolta rifiuti",
    "Voci di": "Voci di cassa",
    "Distribuzione classi Euro (autovetture": "Distribuzione classi Euro (autovetture)",
    "Aree a pericolosita idraulica (alluvioni": "Aree a pericolosita idraulica (alluvioni)",
    "Aree a pericolosità idraulica (alluvioni": "Aree a pericolosità idraulica (alluvioni)",
    "Esposti a frane (P3+P4": "Esposti a frane (P3+P4)",
    "Esposti ad alluvioni (P2 media": "Esposti ad alluvioni (P2 media)",
    "Top categorie merceologiche (CPV": "Top categorie merceologiche (CPV)",
    "Altro (CPIA, ecc.": "Altro (CPIA, ecc.)",
    "Mappa ( punti": "Mappa dei punti",
    "Mappa ( impianti": "Mappa degli impianti",
    "Reddito medio (€": "Reddito medio",
    "Piramide eta (maschi vs femmine": "Piramide eta",
    "Piramide età (maschi vs femmine": "Piramide età",
    "Popolazione femmine per eta (5 anni": "Popolazione femmine per eta",
    "Popolazione femmine per età (5 anni": "Popolazione femmine per età",
    "Popolazione maschi per eta (5 anni": "Popolazione maschi per eta",
    "Popolazione maschi per età (5 anni": "Popolazione maschi per età",
    "Popolazione per eta (totale, 5 anni": "Popolazione per eta",
    "Popolazione per età (totale, 5 anni": "Popolazione per età",
    "Stranieri (UE / Extra-UE, sesso, eta": "Stranieri UE ed Extra-UE",
    "Stranieri (UE / Extra-UE, sesso, età": "Stranieri UE ed Extra-UE",
    "Tabella completa — 119 variabili ISTAT (raw": "Tabella completa variabili ISTAT",
    "Titolo di studio (pop. 9+": "Titolo di studio",
    "Titolo di studio (popolazione 9+": "Titolo di studio",
    "Presenze (notti": "Presenze (notti)",
    "Fine (prevista / effettiva": "Fine prevista / effettiva",
    "Inizio (previsto / effettivo": "Inizio previsto / effettivo",
    "Top 10 destinazioni (dove vanno a lavorare": "Top 10 destinazioni pendolari",
    "Top 10 origini (da dove vengono": "Top 10 origini pendolari",
    "Benzina (self": "Benzina self service",
    "Gasolio (self": "Gasolio self service",
    "Alberghi · strutture": "Alberghi",
    "Extra-alberghiero · strutture": "Extra-alberghiero",
    "Part.": "Particella catastale",
    "Foglio": "Foglio catastale",
    "Progetti del": "Progetti PNRR",
    "5 stelle (lusso": "5 stelle (lusso)",
    "Archivio Nazionale dei Numeri Civici": None,  # citato in un disclaimer, non e' una voce
}

# frammenti e messaggi di stato: non sono dati cercabili
DROP = {
    "attive nel", "dal", "attivi, prezzi al", "aggiudicazioni", "progetti", "famiglia",
    "Tipo", "variabili del Censimento permanente 2021", "pendolari netti: il comune e",
    "pendolari netti: il comune è", "punti di ricarica EVSE, di cui",
    "del Terzo Settore con sede legale al", "Rete", "% sul comune", "Per abitante",
    "Dati non disponibili", "Errore di rete", "plessi scolastici", "Quota media",
    "Movimenti", "per mese", "Pagam.", "Day H.", "Day S.", "Ordinaria",
    "Altre", "Altri", "Attivi", "Non attivi", "Avanzamento per", "Ambito dei dati",
    "Anno di riferimento", "Con foto", "Con descrizione", "Dati AGCOM", "Vista",
    "% famiglie", "Elettr. + ibride", "Termiche / altro", "Tot. Addetti", "Tot. UL",
    "Pop. 9+", "Dim. media", "Dim. famiglia",
}

RX_LABEL = [
    r'class="[^"]*(?:label|lab|lbl)[^"]*"[^>]*>([^<]{2,90})',
    r'<h[234][^>]*>([^<]{2,90})',
    r'<th[^>]*>([^<]{2,70})',
    r'<summary[^>]*>([^<]{2,90})',
    r'<caption[^>]*>([^<]{2,90})',
    r'<button[^>]*>\s*([A-ZÀ-Ù][^<]{2,60})',
    r'\b(?:label|lab|nome|titolo|title)\s*:\s*[\'"]([^\'"]{3,60})[\'"]',
    r'bindTooltip\(\s*`([^`$]{3,40})',
    r'<strong>([A-ZÀ-Ù][^<$]{3,40})',
]


def strip_interp(s):
    """Rimuove ${...} bilanciando le graffe: gestisce annidamenti e backtick."""
    out, i, n = [], 0, len(s)
    while i < n:
        if s[i] == "$" and i + 1 < n and s[i + 1] == "{":
            d, i = 1, i + 2
            while i < n and d > 0:
                if s[i] == "{":
                    d += 1
                elif s[i] == "}":
                    d -= 1
                i += 1
            out.append(" \x01 ")
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def estrai_livello_a(path):
    """Etichette visibili in comune.html, raggruppate per tab."""
    lines = open(path, encoding="utf-8").read().split("\n")
    top = []
    for i, l in enumerate(lines, 1):
        m = re.match(r'^(\s{0,6})(?:async\s+)?function\s+(\w+)\s*\(', l)
        if m:
            top.append((i, m.group(2)))
    top.sort()

    segmenti, corrente = [], None
    for i, (riga, nome) in enumerate(top):
        fine = top[i + 1][0] if i + 1 < len(top) else len(lines) + 1
        if nome in ANCORE:
            corrente = ANCORE[nome]
        segmenti.append((riga, fine, corrente))

    res = {}
    for a, b, tab in segmenti:
        if tab in (None, "_infra"):
            continue
        blocco = strip_interp("\n".join(lines[a - 1:b - 1]))
        for rx in RX_LABEL:
            for m in re.findall(rx, blocco):
                t = html.unescape(m).replace("\x01", " ")
                t = re.sub(r'\s+', ' ', t).strip(" ·—-|,:;()")
                if len(t) < 3:
                    continue
                if re.fullmatch(r'[\d\s.,%€°/+–—-]+', t):
                    continue
                if t.isupper() and len(t) < 6:
                    continue
                if not re.search(r'[a-zà-ù]', t):
                    continue
                res.setdefault(tab, set()).add(t)
    return res


def normalizza(grezze):
    """Applica FIX e DROP; ritorna {termine: {tab_label, ...}}."""
    out = {}
    for tab, voci in grezze.items():
        for v in voci:
            if v.startswith("Nessun") or v in DROP:
                continue
            t = FIX.get(v, v)
            if t is None:
                continue
            t = t.lstrip("§ ").strip()
            t = re.sub(r'\s+', ' ', t).strip(" ·—-|,:;")
            if len(t) < 4:
                continue
            out.setdefault(t, set()).add(TAB_LABEL[tab])
    return out


def costruisci_html(indice):
    """Sezione about.html: descrizione + accordion + ricerca + tabella."""
    righe = []
    for termine in sorted(indice, key=lambda x: x.lower()):
        tabs = ", ".join(sorted(indice[termine]))
        righe.append(
            '            <tr><th scope="row">{}</th><td>{}</td></tr>'.format(
                html.escape(termine), html.escape(tabs)))
    n_termini = len(indice)
    n_tab = len({t for v in indice.values() for t in v})

    return """{start}
      <div class="section-title">&sect; indice dei contenuti</div>
      <h2 class="section-h">Cosa cercare e <em>dove</em>.</h2>
      <p>
        Ogni scheda comunale e' divisa in schede tematiche. Questo indice elenca
        <strong>{n_termini} voci</strong> distribuite su <strong>{n_tab} schede</strong>:
        per ogni termine trovi la scheda in cui il dato viene mostrato. Alcuni argomenti
        compaiono in piu' schede &mdash; le automobili, per esempio, stanno sia nel
        Censimento (auto possedute dalle famiglie) sia in Veicoli e incidenti
        (parco circolante ACI).
      </p>
      <details class="indice-acc">
        <summary>Apri l'indice dei contenuti ({n_termini} voci)</summary>
        <div class="indice-body">
          <label class="indice-search-lab" for="indice-q">Filtra le voci</label>
          <input type="search" id="indice-q" class="indice-search"
                 placeholder="Scrivi un termine: catasto, tasse, farmacie&hellip;"
                 autocomplete="off" aria-describedby="indice-count">
          <p id="indice-count" class="indice-count" role="status" aria-live="polite">
            {n_termini} voci
          </p>
          <div class="indice-tab-wrap">
          <table class="indice-tab">
            <caption class="sr-only">Elenco alfabetico dei dati e scheda in cui trovarli</caption>
            <thead>
              <tr><th scope="col">Voce</th><th scope="col">Dove si trova</th></tr>
            </thead>
            <tbody id="indice-tbody">
{righe}
            </tbody>
          </table>
          </div>
          <p class="indice-empty" id="indice-empty" hidden>Nessuna voce corrisponde alla ricerca.</p>
        </div>
      </details>
      <script>
      (function () {{
        var q = document.getElementById('indice-q');
        if (!q) return;
        var tbody = document.getElementById('indice-tbody');
        var cnt = document.getElementById('indice-count');
        var vuoto = document.getElementById('indice-empty');
        var righe = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
        var tot = righe.length;
        function norm(s) {{
          return s.toLowerCase()
                  .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
        }}
        var idx = righe.map(function (tr) {{ return norm(tr.textContent); }});
        var timer = null;
        function filtra() {{
          var v = norm(q.value.trim());
          var n = 0;
          for (var i = 0; i < righe.length; i++) {{
            var ok = !v || idx[i].indexOf(v) !== -1;
            righe[i].hidden = !ok;
            if (ok) n++;
          }}
          cnt.textContent = v ? (n + ' voci su ' + tot) : (tot + ' voci');
          vuoto.hidden = n !== 0;
        }}
        q.addEventListener('input', function () {{
          clearTimeout(timer);
          timer = setTimeout(filtra, 120);
        }});
      }})();
      </script>
{end}""".format(start=MARK_START, end=MARK_END, righe="\n".join(righe),
                n_termini=n_termini, n_tab=n_tab)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verifica senza scrivere")
    ap.add_argument("--scrivi", action="store_true", help="rigenera about.html")
    args = ap.parse_args()
    if not (args.check or args.scrivi):
        ap.error("indicare --check oppure --scrivi")

    print("[1/5] lettura " + COMUNE)
    grezze = estrai_livello_a(COMUNE)
    n_grezze = sum(len(v) for v in grezze.values())
    print("      etichette grezze: {} su {} tab".format(n_grezze, len(grezze)))

    print("[2/5] normalizzazione")
    indice = normalizza(grezze)
    print("      livello A: {} voci".format(len(indice)))

    print("[3/5] sinonimi da " + SINONIMI)
    sin = json.load(open(SINONIMI, encoding="utf-8"))
    noti = set(TAB_LABEL.values())
    errori = []
    for termine, tabs in sin.items():
        for t in tabs:
            if t not in noti:
                errori.append("{} -> tab sconosciuta '{}'".format(termine, t))
        indice.setdefault(termine, set()).update(tabs)
    if errori:
        print("      ERRORE nei sinonimi:")
        for e in errori:
            print("        " + e)
        sys.exit(1)
    print("      livello B: {} sinonimi -> totale {} voci".format(len(sin), len(indice)))

    print("[4/5] verifica copertura")
    coperte = {v.lower() for v in indice}
    scoperte = []
    for tab, voci in grezze.items():
        for v in voci:
            if v.startswith("Nessun") or v in DROP:
                continue
            t = FIX.get(v, v)
            if t is None:
                continue
            t = t.lstrip("§ ").strip()
            if len(t) >= 4 and t.lower() not in coperte:
                scoperte.append((tab, v))
    if scoperte:
        print("      ETICHETTE NON COPERTE: {}".format(len(scoperte)))
        for tab, v in scoperte:
            print("        [{}] {}".format(tab, v))
        print("      -> aggiungerle a FIX/DROP o ai sinonimi, poi rilanciare")
        sys.exit(1)
    print("      OK: nessuna etichetta scoperta")

    if args.check:
        print("[5/5] --check: nessuna scrittura")
        return

    print("[5/5] scrittura " + ABOUT)
    testo = open(ABOUT, encoding="utf-8").read()

    # --- CSS: dentro il blocco <style> in head ---
    if CSS_START in testo:
        assert testo.count(CSS_START) == 1 and testo.count(CSS_END) == 1, \
            "marker CSS duplicati in about.html"
        testo = testo.split(CSS_START)[0] + CSS + testo.split(CSS_END)[1]
        print("      CSS sostituito")
    else:
        assert testo.count("</style>") == 1, \
            "</style> trovato {} volte".format(testo.count("</style>"))
        testo = testo.replace("</style>", CSS + "\n</style>")
        print("      CSS inserito")

    nuovo = costruisci_html(indice)
    if MARK_START in testo:
        assert testo.count(MARK_START) == 1 and testo.count(MARK_END) == 1, \
            "marker duplicati in about.html"
        pre = testo.split(MARK_START)[0]
        post = testo.split(MARK_END)[1]
        testo = pre + nuovo + post
        print("      sezione sostituita")
    else:
        ancora = '<section class="about-section" id="metodologia">'
        assert testo.count(ancora) == 1, \
            "ancora '{}' trovata {} volte".format(ancora, testo.count(ancora))
        blocco = ('<section class="about-section" id="cosa-cercare">\n'
                  '  <div class="wrap">\n'
                  + nuovo +
                  '\n  </div>\n</section>\n\n')
        testo = testo.replace(ancora, blocco + ancora)
        print("      sezione inserita prima di #metodologia")
    tmp = ABOUT + ".tmp"
    open(tmp, "w", encoding="utf-8").write(testo)
    os.replace(tmp, ABOUT)
    print("      fatto: {} voci scritte".format(len(indice)))


if __name__ == "__main__":
    main()
