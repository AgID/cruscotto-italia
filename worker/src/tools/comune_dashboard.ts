/**
 * Tool: comune_dashboard
 *
 * Single-fetch endpoint che restituisce TUTTI i dati di un comune in una
 * sola chiamata MCP. Sostituisce le 6+ chiamate separate (overview,
 * demografia, profilo, turismo, pnrr, territorio, opere, contratti, spese)
 * usate dal frontend al primo load di /comune.html.
 *
 * Sorgente: dashboard/<istat>.json su R2, generato da etl/sources/dashboard.py
 * che accorpa:
 *   - lookup/comuni-bundle.json[<istat>] (anagrafica)
 *   - demografia/<istat>.json
 *   - profilo/<istat>.json
 *   - turismo/<istat>.json
 *   - pnrr/<istat>.json
 *   - territorio/<istat>.json
 *   - bdap/dettaglio/<istat>.json    (opere)
 *   - siope/<istat>.json             (spese SIOPE pre-calcolate)
 *   - immobili_pa/<istat>.json     (MEF DE - Beni Immobili Pubblici 2022)
 *   - anncsu/<istat>.json          (Agenzia Entrate + ISTAT - ANNCSU strade
 *                                   e numeri civici certificati, sample 1000
 *                                   punti geo-ref. Full su anncsu_full/ via
 *                                   endpoint dedicato /data/anncsu_full/)
 *   - sanita_mds/<istat>.json      (Ministero Salute - farmacie e parafarmacie
 *                                   geo-localizzate, posti letto ospedalieri
 *                                   per stabilimento e disciplina. Licenza
 *                                   IODL v2.0. Aggiornamento farmacie/parafarm.
 *                                   quotidiano, ospedali annuale.)
 *   - pun/<istat>.json             (GSE/MASE - Piattaforma Unica Nazionale
 *                                   punti di ricarica per veicoli elettrici.
 *                                   Licenza CC BY 4.0 ex art. 52 c.2 CAD
 *                                   (open by default). 66.619 PdR su 5.185
 *                                   comuni (65,7%). Aggiornamento quotidiano.)
 *   - agcom_bbmap/<istat>.json     (AGCOM - Broadband Map ex art. 22 Codice
 *                                   Comunicazioni Elettroniche. Licenza
 *                                   CC BY 4.0 ex art. 52 c.2 CAD (open by
 *                                   default). 7896/7896 comuni (100%).
 *                                   Aggiornamento trimestrale.)
 *   - carburanti/<istat>.json      (MIMIT - Osservatorio Prezzi Carburanti
 *                                   (art. 51 L. 99/2009). Licenza IODL 2.0.
 *                                   ~23.700 impianti su ~5.450 comuni (69%).
 *                                   Anagrafica + prezzi correnti per Benzina,
 *                                   Gasolio, GPL, Metano, HVO; KPI per comune;
 *                                   aggregati nazionali in
 *                                   carburanti/_nazionale.json (separato).
 *                                   Aggiornamento quotidiano.)
 *   - runts/<istat>.json           (Min. Lavoro e Politiche Sociali - Registro
 *                                   Unico Nazionale Terzo Settore (D.Lgs
 *                                   117/2017 art. 53). Dato pubblico per legge
 *                                   ex D.Lgs 36/2006 (Direttiva PSI). 145.898
 *                                   enti del Terzo Settore in 7.547 comuni
 *                                   (95,3%). Anagrafica + sezione (ODV/APS/EF/
 *                                   IS/SMS/ETS), 5x1000, data iscrizione, rete
 *                                   associativa. Cap 5000 enti per comune,
 *                                   ordinati per data iscrizione DESC.
 *                                   Aggiornamento quotidiano.)
 *   - lookup/anac-aggregato.json[<cf>] (contratti)
 *
 * Schema output (passa-attraverso del file R2):
 *   {
 *     "_etl_version": "0.1.0",
 *     "_generated_at": "ISO-8601",
 *     "_missing": ["lista shard non disponibili per questo comune"],
 *     "anagrafica":  { ... },
 *     "demografia":  { ... } | null,
 *     "profilo":     { ... } | null,
 *     "turismo":     { ... } | null,
 *     "pnrr":        { ... } | null,
 *     "territorio":  { ... } | null,
 *     "opere":       { ... } | null,
 *     "siope":       { ... } | null,    // schema v0.2.0 multi-anno:
 *                                         //   { _etl_version: "0.2.0",
 *                                         //     anni_disponibili: [2025, 2026],
 *                                         //     anno_default: 2025,
 *                                         //     per_anno: {
 *                                         //       "2025": { totale_anno, n_voci, voci, mesi_disponibili,
 *                                         //                 ultimo_mese, parziale: false, ... },
 *                                         //       "2026": { ..., parziale: true }
 *                                         //     }
 *                                         //   }
 *     "anac":        { ... } | null,
 *     "immobili_pa": { ... } | null    // MEF DE - Beni Immobili Pubblici 2022:
 *                                       //   { anno_rilevazione: 2022,
 *                                       //     kpi: { n_totale, n_fabbricati, n_terreni,
 *                                       //            pct_geo_referenziati,
 *                                       //            pct_vincolo_qualsiasi, pct_vincolo_culturale,
 *                                       //            pct_uso_terzi, superficie_totale_mq,
 *                                       //            mix_categoria, mix_natura },
 *                                       //     punti: [{ lat, lon, cat, tipo, sup,
 *                                       //               vincolo, uso_terzi }, ...]
 *                                       //               // capped a 500 punti per comune
 *                                       //               // (sampling stratificato per categoria)
 *                                       //   }
 *     "anncsu":      { ... } | null    // Agenzia Entrate + ISTAT - ANNCSU
 *                                       //   (Archivio Nazionale Numeri Civici
 *                                       //   e Strade Urbane), snapshot mensile:
 *                                       //   { _snapshot_date: "YYYY-MM-DD",
 *                                       //     kpi: { n_strade, n_civici,
 *                                       //            n_civici_geo_ref,
 *                                       //            pct_geo_ref,
 *                                       //            n_strade_bilingui,
 *                                       //            n_civici_rosso, n_civici_nero,
 *                                       //            n_civici_metrici, n_civici_bis,
 *                                       //            quota_mean_m,
 *                                       //            top_10_strade: [{odo, acc}, ...],
 *                                       //            metodi_georef:
 *                                       //              {gps, catasto, ortofoto,
 *                                       //               cartografia, altro},
 *                                       //            bbox: [latmin, latmax,
 *                                       //                   lonmin, lonmax] },
 *                                       //     punti: [{ lat, lon, odo, civ, esp,
 *                                       //               quota, met }, ...]
 *                                       //             // sample 1000 punti per
 *                                       //             // comune; per il dataset
 *                                       //             // completo fetch HTTP GET
 *                                       //             // /data/anncsu_full/<istat>.json
 *                                       //   }
 *     "sanita_mds":  { ... } | null    // Ministero Salute - bundle sanita'
 *                                       //   territoriale (3 dataset MdS):
 *                                       //   { _license: "IODL v2.0",
 *                                       //     _fonti: { farmacie, parafarmacie,
 *                                       //               ospedali: {anno_dati: 2023} },
 *                                       //     farmacie:     { kpi, punti } | null,
 *                                       //     parafarmacie: { kpi, punti } | null,
 *                                       //     ospedali:     { kpi, stabilimenti } | null
 *                                       //   }
 *                                       //   farmacie.kpi: { n_totale, n_geo_referenziate,
 *                                       //                    pct_geo_referenziate,
 *                                       //                    mix_tipologia,
 *                                       //                    n_outlier_coordinate }
 *                                       //   farmacie.punti: [{nome,tipo,indirizzo,cap,
 *                                       //                     lat,lon}, ...]
 *                                       //   parafarmacie.kpi: { n_totale, ... }
 *                                       //   ospedali.kpi: { n_stabilimenti, n_reparti_totali,
 *                                       //                   posti_letto_totali,
 *                                       //                   posti_letto_ordinaria,
 *                                       //                   posti_letto_pagamento,
 *                                       //                   posti_letto_day_hospital,
 *                                       //                   posti_letto_day_surgery,
 *                                       //                   mix_discipline }
 *                                       //   ospedali.stabilimenti: [{ codice_struttura,
 *                                       //                              subcodice, denominazione,
 *                                       //                              tipo_struttura, indirizzo,
 *                                       //                              totale_posti_letto,
 *                                       //                              discipline:[...] }, ...]
 *                                       //   Capping: nessuno (Roma worst-case ~270KB).
 *                                       //   Coverage comuni: farmacie 91.9%,
 *                                       //   parafarmacie 27.3%, ospedali 9.3%.
 *     "pun":         { ... } | null    // GSE/MASE - Piattaforma Unica Nazionale
 *                                       //   punti di ricarica per veicoli elettrici:
 *                                       //   { _data_last_modified: "ISO-8601",
 *                                       //     kpi: { n_totale, n_attivi,
 *                                       //            n_non_attivi, pct_attivi,
 *                                       //            n_ac, n_dc,
 *                                       //            potenza_tot_kw,
 *                                       //            mix_potenza:
 *                                       //              {Slow, Quick, Fast,
 *                                       //               HPC, "Ultra fast"} },
 *                                       //     punti: [{ id_evse, lat, lon,
 *                                       //               indirizzo, cap, stato,
 *                                       //               tipo_parcheggio,
 *                                       //               potenza_categoria,
 *                                       //               potenza_w, corrente,
 *                                       //               restrizioni,
 *                                       //               servizi_vicini,
 *                                       //               orario }, ...]
 *                                       //     // no capping per ora
 *                                       //   }
 *                                       //   Coverage: 5185/7896 comuni (65,7%),
 *                                       //   66619 PdR totali. Aggiornamento
 *                                       //   quotidiano via GSE S3 (Cognito guest).
 *     "agcom_bbmap": { ... } | null    // AGCOM Broadband Map (BBmap) - reportistica
 *                                       //   consistenze rete cablata, art. 22 CCE:
 *                                       //   { _data_period: "31/12/2025",
 *                                       //     kpi: { famiglie_residenti,
 *                                       //            famiglie_ftth, famiglie_ftth_20m,
 *                                       //            copertura_ftth_desi_pct,
 *                                       //            copertura_ftth_20m_pct,
 *                                       //            confidenza_desi_pct,
 *                                       //            celle_20m_raggiunte,
 *                                       //            celle_20m_ftth, celle_20m_fttc,
 *                                       //            punti_dichiarati,
 *                                       //            punti_dichiarati_ftth,
 *                                       //            punti_geo_distinti,
 *                                       //            punti_geo_distinti_ftth,
 *                                       //            indirizzi_postali_distinti,
 *                                       //            indirizzi_postali_distinti_ftth },
 *                                       //     anagrafica_locale: { regione,
 *                                       //                          provincia, comune },
 *                                       //     mappa_ufficiale: { url, level }
 *                                       //   }
 *                                       //   Coverage 7896/7896 (100%), aggiornamento
 *                                       //   trimestrale. Le geometrie (polilinee
 *                                       //   strade FTTH/rame) NON sono nello shard:
 *                                       //   la mappa di dettaglio e' linkata via
 *                                       //   deep-link al Web AppBuilder ufficiale
 *                                       //   AGCOM costruito client-side dal frontend.
 *     "carburanti":  { ... } | null    // MIMIT - Osservatorio Prezzi Carburanti
 *                                       //   (art. 51 L. 99/2009), licenza IODL 2.0:
 *                                       //   { _data_last_modified: "YYYY-MM-DD",
 *                                       //     kpi: { n_impianti, n_stradali,
 *                                       //            n_autostradali, n_pompe_bianche,
 *                                       //            pct_pompe_bianche,
 *                                       //            n_bandiere_distinte,
 *                                       //            mix_bandiere:
 *                                       //              {<top5 brand>:int, "Altre":int},
 *                                       //            prezzo_medio:
 *                                       //              { benzina_self, benzina_serv,
 *                                       //                gasolio_self, gasolio_serv,
 *                                       //                gpl, metano, hvo }
 *                                       //              (null se carburante non offerto),
 *                                       //            prezzo_min:
 *                                       //              { benzina_self, gasolio_self }
 *                                       //              (impianto piu' economico),
 *                                       //            freshness_pct: % prezzi <=7gg },
 *                                       //     punti: [{ id, name, brand, tipo,
 *                                       //               lat, lon, indirizzo,
 *                                       //               prezzi:
 *                                       //                 { benzina_self?, benzina_serv?,
 *                                       //                   gasolio_self?, gasolio_serv?,
 *                                       //                   gpl?, metano?, hvo? },
 *                                       //               prezzi_extra: {<premium>}?,
 *                                       //               ultimo_aggiornamento: "YYYY-MM-DD"
 *                                       //             }, ...]
 *                                       //   }
 *                                       //   Coverage: ~5.450/7.896 comuni (69%),
 *                                       //   ~23.700 impianti totali. Aggregati
 *                                       //   nazionali e regionali in shard separato
 *                                       //   carburanti/_nazionale.json (fetch
 *                                       //   lazy lato frontend). Aggiornamento
 *                                       //   quotidiano (CSV bulk MIMIT "Prezzo
 *                                       //   alle 8 di mattina").
 *     "runts":       { ... }            // Min. Lavoro - Registro Unico Nazionale
 *                                       //   Terzo Settore (RUNTS, D.Lgs 117/2017):
 *                                       //   { _snapshot_date: "YYYY-MM-DD",
 *                                       //     _source_url: <pagina min. lavoro>,
 *                                       //     kpi: { n_totale,
 *                                       //            mix_sezione:
 *                                       //              {APS,ODV,IS,ETS,EF,SMS}:int
 *                                       //              (sorted by count desc),
 *                                       //            n_5x1000,
 *                                       //            pct_5x1000,
 *                                       //            n_rete_associativa,
 *                                       //            iscrizioni_per_anno:
 *                                       //              {"YYYY":int,...} },
 *                                       //     enti: [{ cf, rep, denom, sez,
 *                                       //              rapp, rete, x1000,
 *                                       //              data_iscr: "YYYY-MM-DD" },
 *                                       //            ...]
 *                                       //   }
 *                                       //   Sezione MAI null (anche per comuni
 *                                       //   con 0 enti: n_totale=0, enti=[]).
 *                                       //   Cap enti[] a 5000 ordinati per
 *                                       //   data_iscr DESC; se cappato, presenti
 *                                       //   _enti_truncated:true, _enti_total:N,
 *                                       //   _enti_cap:5000 (es. Roma 6616 enti).
 *                                       //   Coverage: 7.547 / 7.918 comuni
 *                                       //   con n_totale>0 (95,3%); 371 comuni
 *                                       //   con n_totale=0 (piccoli, montani).
 *                                       //   Aggiornamento quotidiano (XLSX bulk
 *                                       //   ASP.NET PostBack servizi.lavoro.gov.it).
 *   }
 *
 * NB sulla cache:
 *   - KV cache disabilitata: shard grandi (Roma ~984 KB raw) ridurrebbero il
 *     vantaggio per via di JSON.parse lato worker; R2 con CF cache layer
 *     copre comunque i casi caldi.
 *   - Aggiornamento dati: l'ETL dashboard rigenera i file su R2 quando uno
 *     degli shard sorgente cambia. Per ora ricostruito on-demand via GitHub
 *     Actions (TODO multi-cadence).
 *
 * Input alternativi:
 *   - istat_code (preferito, 6 cifre)
 *   - denominazione (case-insensitive, fallback)
 */

import type { Env } from "../index.js";
import type { ToolDefinition } from "./index.js";
import { fetchR2Json } from "../lib/r2cache.js";

interface ComuneAnagrafica {
  istat_code: string;
  denominazione: string;
  provincia: string;
  regione: string;
  codice_ipa: string | null;
  codice_fiscale: string | null;
  nome_categoria: string | null;
  codice_catastale: string | null;
  kpi: Record<string, unknown>;
}

interface ComuniBundle {
  _etl_version: string;
  comuni: Record<string, ComuneAnagrafica>;
}

interface DashboardShard {
  _etl_version: string;
  _generated_at: string;
  _missing: string[];
  anagrafica: ComuneAnagrafica;
  demografia: unknown | null;
  profilo: unknown | null;
  turismo: unknown | null;
  pnrr: unknown | null;
  territorio: unknown | null;
  opere: unknown | null;
  siope: unknown | null;
  anac: unknown | null;
  pun: unknown | null;
  agcom_bbmap: unknown | null;
  carburanti: unknown | null;
  runts: unknown | null;
}

export const comuneDashboard: ToolDefinition = {
  description:
    "Vista completa di un comune italiano in una sola chiamata. Sezioni: anagrafica, demografia, censimento, turismo, PNRR (Italia Domani), territorio (ISPRA Suolo/IdroGEO/Rifiuti), qualita aria (ISPRA SNPA: PM10/PM2.5/NO2), opere pubbliche (BDAP-MOP), spese (SIOPE multi-anno), contratti (ANAC), scuole (MIUR), veicoli e incidenti (ISTAT + ACI), redditi IRPEF 2020-2024 (MEF DF), patrimonio immobili PA (MEF DE 2022), ANNCSU civici e strade (Agenzia Entrate + ISTAT), sanita territoriale farmacie/parafarmacie/ospedali (Min. Salute), punti ricarica EV (GSE/MASE PUN), banda larga FTTH (AGCOM BBmap), distributori e prezzi carburanti (MIMIT), enti del Terzo Settore RUNTS (Min. Lavoro - ODV/APS/EF/IS/SMS/ETS, 5x1000, rete associativa). Tool da preferire per qualsiasi domanda generale su un comune (es. 'mostrami Bergamo', 'dati Milano'). Richiede istat_code (6 cifre, es. '075035' per Lecce). Se hai solo il nome, chiama prima search_comune per ottenerlo (gestisce omonimie/fusioni). Per il dettaglio completo dello schema di output per ogni sezione consulta lo skill cruscotto-italia-workflow.",
  inputSchema: {
    type: "object",
    properties: {
      istat_code: {
        type: "string",
        pattern: "^\\d{6}$",
        description: "Codice ISTAT 6 cifre (es. '075035' per Lecce)",
      },
      denominazione: {
        type: "string",
        minLength: 2,
        description: "Nome del comune (case-insensitive). Risolto via comuni-bundle.",
      },
    },
    additionalProperties: false,
  },
  handler: async (args: Record<string, unknown>, env: Env) => {
    const istatCode = args.istat_code as string | undefined;
    const denominazione = args.denominazione as string | undefined;

    if (!istatCode && !denominazione) {
      throw new Error("Either 'istat_code' or 'denominazione' is required");
    }

    // 1. Risoluzione istat_code se non fornito
    let resolvedIstat: string | undefined = istatCode;
    if (!resolvedIstat && denominazione) {
      const bundle = await fetchR2Json<ComuniBundle>(
        env, "lookup/comuni-bundle.json"
      );
      if (!bundle?.comuni) {
        return { error: "comuni_bundle_not_found" };
      }
      const target = denominazione.toLowerCase().trim();
      const match = Object.values(bundle.comuni).find(
        (c) => c.denominazione.toLowerCase() === target,
      );
      if (!match) {
        return {
          error: "comune_not_found",
          searched: { denominazione },
        };
      }
      resolvedIstat = match.istat_code;
    }

    // 2. Fetch del dashboard shard (no KV cache: file grandi)
    const shardKey = `dashboard/${resolvedIstat}.json`;
    const shard = await fetchR2Json<DashboardShard>(
      env, shardKey, { useKvCache: false }
    );

    if (!shard) {
      return {
        error: "dashboard_shard_not_found",
        istat_code: resolvedIstat,
        hint:
          "Lo shard non e' disponibile per questo comune. Potrebbe essere un comune molto recente non ancora processato dall'ETL, oppure un errore di codice ISTAT.",
      };
    }

    return shard;
  },
};
