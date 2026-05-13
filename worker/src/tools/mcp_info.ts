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
    "Returns server metadata, version, and data freshness for each integrated source (ANAC, BDAP-MOP, SIOPE, Italia Domani PNRR, ISTAT, ISPRA, MIUR, ACI, MEF Federalismo Fiscale, MEF Patrimonio Immobiliare PA, Agenzia delle Entrate/ISTAT ANNCSU, Ministero della Salute Open Data).",
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
      version: "0.6.0",
      protocol: "MCP 2024-11-05",
      datasets: 15,
      institutions: 11,
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
      },
      manifest: manifest ?? { warning: "manifest not yet populated by ETL" },
      generated_at: new Date().toISOString(),
    };
  },
};
