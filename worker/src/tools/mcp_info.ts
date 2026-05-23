/**
 * Tool: mcp_info
 *
 * Returns server metadata, version, and data freshness for each integrated source.
 * Reads R2 manifest.json populated by the ETL pipeline.
 */
import type { Env } from "../index.js";
import type { ToolDefinition } from "./index.js";

export const mcpInfo: ToolDefinition = {
  description:
    "Returns server metadata, version, and data freshness for each integrated source (ANAC, BDAP-MOP, SIOPE, Italia Domani PNRR, ISTAT, ISPRA, MIUR, ACI, MEF Federalismo Fiscale, MEF Patrimonio Immobiliare PA, Agenzia delle Entrate/ISTAT ANNCSU, Ministero della Salute Open Data, GSE/MASE Piattaforma Unica Nazionale punti di ricarica, AGCOM Broadband Map, MIMIT Osservatorio Prezzi Carburanti, Ministero del Lavoro Registro Unico Nazionale Terzo Settore RUNTS, ISTAT Archivio Statistico Imprese Attive ASIA UL, ISTAT Matrice Pendolarismo 2021, ISTAT Basi Territoriali + Variabili Censuarie 2021).",
  inputSchema: {
    type: "object",
    properties: {},
    additionalProperties: false,
  },
  handler: async (_args: Record<string, unknown>, env: Env) => {
    let manifest: Record<string, unknown> | null = null;
    try {
      const r = await fetch(`${env.DATA_BASE_URL}/manifest.json`, {
        cf: { cacheTtl: 3600, cacheEverything: true },
      });
      if (r.ok) manifest = (await r.json()) as Record<string, unknown>;
    } catch {
      /* manifest may not exist on first deploy */
    }
    return {
      service: "cruscotto-italia-mcp",
      version: "0.17.0",
      protocol: "MCP 2024-11-05",
      datasets: 25,
      institutions: 16,
      municipalities: 7918,
      sources: {
        anac: {
          canonical: "https://dati.anticorruzione.it/opendata",
          license: "CC-BY 4.0",
          datasets: ["contratti pubblici (OCDS)"],
        },
        bdap: {
          canonical: "https://bdap-opendata.rgs.mef.gov.it",
          license: "IODL 2.0",
          datasets: ["BDAP-MOP opere pubbliche", "SIOPE flussi di cassa"],
        },
        italia_domani: {
          canonical: "https://italiadomani.gov.it/it/strumenti/dati-e-trasparenza.html",
          license: "CC-BY 4.0",
          datasets: ["PNRR progetti (Sistema ReGiS)"],
        },
        istat: {
          canonical: "https://www.istat.it",
          license: "CC-BY 3.0 IT",
          datasets: [
            "POSAS demografia",
            "Censimento permanente (profilo)",
            "Turismo (TUR_1, TUR_7)",
            "Archivio Statistico Imprese Attive - Unità Locali (ASIA UL)",
            "Matrice pendolarismo per lavoro 2021 (Censimento permanente)",
          ],
        },
        ispra: {
          canonical: "https://www.isprambiente.gov.it",
          license: "CC-BY 4.0",
          datasets: [
            "consumo di suolo (SNPA)",
            "rischio idrogeologico (IdroGEO)",
            "rifiuti urbani (Catasto Rifiuti)",
            "qualità dell'aria (rete SNPA - PM10, PM2.5, NO2)",
          ],
        },
        miur: {
          canonical: "https://dati.istruzione.it",
          license: "CC-BY 4.0",
          datasets: ["Anagrafe Scuole Statali (DS0400SCUANAGRAFESTAT)"],
        },
        aci: {
          canonical: "https://lod.aci.it",
          license: "CC-BY 4.0",
          datasets: [
            "Parco veicoli per classe Euro (ISTAT 41_993 DCIS_VEICOLIPRA_COM)",
            "Incidenti stradali con morti e feriti (ISTAT 41_983)",
            "Nuove iscrizioni veicoli per alimentazione (ACI LOD)",
          ],
        },
        mef_redditi: {
          canonical: "https://www.finanze.gov.it/it/statistiche-fiscali/DichiarazioniFiscali-/",
          license: "CC-BY 3.0",
          datasets: [
            "Redditi e principali variabili IRPEF su base comunale (a.i. 2020-2024)",
          ],
        },
        mef_patrimonio: {
          canonical: "https://www.de.mef.gov.it/it/attivita_istituzionali/patrimonio_pubblico/censimento_immobili_pubblici/",
          license: "CC-BY 4.0",
          datasets: [
            "Censimento beni immobili pubblici detenuti dalle Amministrazioni Pubbliche al 31/12/2022 (fabbricati e terreni con geolocalizzazione)",
          ],
        },
        anncsu: {
          canonical: "https://anncsu.open.agenziaentrate.gov.it/",
          license: "Open data ai sensi del Regolamento UE 2023/138 (HVD)",
          datasets: [
            "Archivio Nazionale dei Numeri Civici e delle Strade Urbane (ANNCSU): stradario e indirizzario con georeferenziazione, snapshot continuo (Agenzia delle Entrate + ISTAT)",
          ],
        },
        ministero_salute: {
          canonical: "https://www.dati.salute.gov.it/",
          license: "IODL v2.0",
          datasets: [
            "Farmacie italiane (anagrafica + geolocalizzazione, ~20.800 attive in 7.258 comuni, aggiornamento quotidiano)",
            "Parafarmacie italiane (anagrafica + geolocalizzazione, ~7.200 attive in 2.158 comuni, aggiornamento quotidiano)",
            "Posti letto per stabilimento ospedaliero e disciplina (anno 2023: 1.272 stabilimenti in 736 comuni, ~213.000 posti letto totali con dettaglio per disciplina; aggiornamento annuale)",
          ],
        },
        gse_pun: {
          canonical: "https://www.piattaformaunicanazionale.it/idr",
          license: "CC BY 4.0 ex art. 52 c.2 D.Lgs 82/2005 (CAD) - open by default",
          datasets: [
            "Piattaforma Unica Nazionale dei punti di ricarica per veicoli elettrici (PUN - GSE/MASE): infrastrutture di ricarica EVSE con stato attivo/non attivo, georeferenziazione, indirizzo, CAP, potenza erogabile (Slow/Quick/Fast/HPC/Ultra fast), potenza massima in W, tipologia di corrente (AC/DC), tipologia parcheggio, restrizioni, servizi nelle vicinanze, orario di apertura. 66.619 PdR totali su 5.185 comuni (65,7% copertura). Aggiornamento quotidiano (~03:00 UTC).",
          ],
        },
        agcom: {
          canonical: "https://geo.agcom.it/reportistica/",
          license: "CC BY 4.0 ex art. 52 c.2 D.Lgs 82/2005 (CAD) - open by default",
          datasets: [
            "AGCOM Broadband Map (BBmap) - reportistica delle consistenze dei punti geografici raggiunti dalla rete cablata, ai sensi dell'art. 22 Codice delle Comunicazioni Elettroniche: copertura FTTH DESI %, copertura FTTH entro 20m, famiglie residenti e raggiunte, celle 20x20m raggiunte da FTTH/FTTC, punti dichiarati geograficamente distinti, indirizzi postali raggiunti, indice di confidenza DESI. Copertura nazionale 7.896/7.896 comuni (100%). Aggiornamento trimestrale, dato corrente al 31/12/2025.",
          ],
        },
        mimit_carburanti: {
          canonical: "https://www.mimit.gov.it/it/open-data/elenco-dataset/carburanti-prezzi-praticati-e-anagrafica-degli-impianti",
          license: "IODL 2.0",
          datasets: [
            "Ministero delle Imprese e del Made in Italy (MIMIT) - Osservatorio Prezzi Carburanti, dataset 'Carburanti - Prezzi praticati e anagrafica degli impianti' (art. 51 L. 99/2009): anagrafica completa degli ~23.700 impianti attivi con bandiera, gestore, tipo (Stradale/Autostradale), indirizzo geo-referenziato, e prezzi correnti per Benzina, Gasolio, GPL, Metano, HVO (oltre a premium proprietari V-Power, Hi-Q, Supreme Diesel, Blue Diesel). KPI per comune: n_impianti, mix bandiere top 5, n_pompe_bianche, prezzo medio e minimo per carburante self/serv, freshness (% prezzi aggiornati <=7gg). Aggregati nazionali e regionali pre-calcolati in carburanti/_nazionale.json. Copertura ~5.450 comuni su 7.896 (69%; i ~2.450 senza distributore sono micro-comuni montani). Aggiornamento quotidiano (CSV 'Prezzo alle 8 di mattina' MIMIT, skip automatico via hash).",
          ],
        },
        min_lavoro_runts: {
          canonical: "https://servizi.lavoro.gov.it/runts/it-it/Lista-enti",
          license: "CC BY 4.0 ex art. 52 c.2 D.Lgs 82/2005 (CAD) - open data di default delle PA; dato pubblicato ex D.Lgs 117/2017 art. 53 (pubblicita' legale RUNTS)",
          datasets: [
            "Ministero del Lavoro e delle Politiche Sociali - Registro Unico Nazionale del Terzo Settore (RUNTS, D.Lgs 117/2017): anagrafica completa degli enti del Terzo Settore iscritti su base comunale (sede legale). 145.898 enti totali in 7.547 comuni (95,3% copertura). Mix nazionale per sezione: APS 47% > ODV 27% > IS 15% > ETS 11% > EF 0,4% > SMS 0,1%. 52% degli enti iscritti al beneficio 5x1000. Campi: codice fiscale, repertorio, denominazione, sezione (ODV/APS/EF/IS/SMS/ETS), legale rappresentante, rete associativa, provincia/comune sede legale, 5x1000, data iscrizione. KPI per comune: n_totale, mix per sezione, n_5x1000 + percentuale, n_rete_associativa, iscrizioni per anno. Aggiornamento quotidiano del file XLSX bulk sulla pagina ASP.NET di servizi.lavoro.gov.it (snapshot YYYYMMDD_iscritti_v1.0.xlsx).",
          ],
        },
        istat_censimento: {
          canonical: "https://www.istat.it/notizia/basi-territoriali-e-variabili-censuarie/",
          license: "CC-BY 3.0 IT",
          datasets: [
            "ISTAT - Basi Territoriali 2021 + Variabili censuarie del Censimento permanente 2021. Geometrie delle 756.376 sezioni di censimento nazionali (poligoni WGS84 EPSG:4326 RFC 7946) accorpate per comune, integrate con 119 variabili demografiche/abitative per sezione: popolazione totale + sesso (P1-P3), 16 fasce eta' 5-anni per totale/maschi/femmine (P14-P82), titolo di studio (P86-P100 nessuno/elementare/media/diploma/terziario per sesso), occupati 15-64 (P101-P103), italiani per fascia eta' (IT1-IT12), stranieri UE/extra-UE per sesso/eta'/occupazione (ST1-ST33), famiglie per numero componenti 1-6+ (PF1, PF3-PF8), abitazioni occupate/vuote/totali (A2, A3, A8), edifici residenziali (E3). Copertura 7904/7896 comuni (100% incluso TN/BZ). 252.467 sezioni 'no_vars' (33%) sono aree non residenziali (parchi, aree industriali, infrastrutture) non rilevate dal censimento permanente per assenza di residenti. KPI comune-level pre-calcolati in censimento/<istat>.json (~3-5 KB), geometrie complete in /data/censimento_full/<istat>.geojson lazy-fetch (30 KB - 3 MB). Aggiornamento annuale ISTAT (ultimo rilascio 14/05/2026).",
          ],
        },
        mic_arco: {
          canonical: "https://dati.beniculturali.it/sparql",
          license: "CC-BY 4.0",
          datasets: [
            "Ministero della Cultura (MiC) - ICCD/ArCo: catalogo nazionale dei beni culturali immobili tutelati (Immovable Cultural Property), pubblicato come Linked Open Data tramite endpoint SPARQL ufficiale. Dataset ~113.820 beni nazionali (chiese, palazzi, castelli, ville, aree archeologiche, monumenti, edifici di culto, parchi e giardini storici) catalogati ICCD secondo l'ontologia ArCo (Architecture of Knowledge). Per ogni bene: denominazione (rdfs:label), tipologia granulare (centinaia di slug ArCo CulturalPropertyType normalizzati in 9 macro-categorie: chiesa, palazzo, castello, archeologia, museo, monumento, infrastruttura, parco_giardino, altro), indirizzo civico completo, sigla provincia, coordinate WKT POINT (86% beni con geometria), foto (foaf:depiction, 97% beni), descrizione testuale (28% beni), soprintendenza di tutela (hasHeritageProtectionAgency), eventuale link a CulturalInstituteOrSite DBUnico 2.0 per i beni visitabili con orari/contatti. KPI per comune: n_totale, n_visitabili (subset con cis_link), n_con_coordinate/n_senza_coordinate, mix_categoria, pct_con_foto, pct_con_descrizione, beni_per_1000_ab. Copertura 5657/7895 comuni (71,6%). Lista compatta nello shard base (cap 30), lista completa con tutti i campi in /data/beni_culturali_full/<istat>.json per comuni grandi. Aggiornamento mensile via SPARQL paginato.",
          ],
        },
      },
      manifest: manifest ?? { warning: "manifest not yet populated by ETL" },
      generated_at: new Date().toISOString(),
    };
  },
};
