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
    "Returns server metadata, version, and data freshness for each integrated source (ANAC, BDAP-MOP, SIOPE, Italia Domani PNRR, ISTAT, ISPRA, MIUR, ACI, MEF Federalismo Fiscale, MEF Patrimonio Immobiliare PA, Agenzia delle Entrate/ISTAT ANNCSU, Ministero della Salute Open Data, GSE/MASE Piattaforma Unica Nazionale punti di ricarica, AGCOM Broadband Map, MIMIT Osservatorio Prezzi Carburanti).",
  inputSchema: {
    type: "object",
    properties: {},
    additionalProperties: false,
  },
  handler: async (_args: Record<string, unknown>, env: Env) => {
    let manifest: Record<string, unknown> | null = null;
    try {
      const obj = await env.DATA.get("manifest.json");
      if (obj) manifest = (await obj.json()) as Record<string, unknown>;
    } catch {
      /* manifest may not exist on first deploy */
    }
    return {
      service: "cruscotto-italia-mcp",
      version: "0.9.0",
      protocol: "MCP 2024-11-05",
      datasets: 18,
      institutions: 14,
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
      },
      manifest: manifest ?? { warning: "manifest not yet populated by ETL" },
      generated_at: new Date().toISOString(),
    };
  },
};
