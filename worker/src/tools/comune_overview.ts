/**
 * Tool: comune_overview
 *
 * Vista 360° di un comune: anagrafica + KPI aggregati real-time.
 * Sorgenti R2 (cachate in KV per 1h):
 *  - lookup/comuni-bundle.json     (anagrafica + popolazione ISTAT POSAS 2026)
 *  - lookup/anac-aggregato.json    (KPI contratti per CF stazione appaltante)
 *  - lookup/bdap-aggregato.json    (KPI opere pubbliche per CF titolare)
 *
 * Input: istat_code (preferito) | denominazione (case-insensitive)
 * Output: anagrafica + kpi joined via codice_fiscale
 */

import type { Env } from "../index.js";
import type { ToolDefinition } from "./index.js";
import { fetchR2Json } from "../lib/r2cache.js";

interface ComuneDetail {
  istat_code: string;
  denominazione: string;
  provincia: string;
  regione: string;
  codice_ipa: string | null;
  codice_fiscale: string | null;
  nome_categoria: string | null;
  codice_catastale: string | null;
  kpi: {
    contratti: unknown;
    opere: unknown;
    spese_siope: unknown;
    coesione: unknown;
    popolazione: unknown;
  };
}
interface ComuniBundle {
  _etl_version: string;
  comuni: Record<string, ComuneDetail>;
}

interface AnacBuyerKpi {
  buyer_name: string;
  count: number;
  importo_totale: number;
  first_award_date: string;
  last_award_date: string;
  distinct_cpv: number;
  top_cpv: Array<{ code: string; desc: string | null; count: number; importo: number }>;
}
interface AnacAggregato {
  _etl_version: string;
  _period_files: string[];
  data: Record<string, AnacBuyerKpi>;
}

interface BdapStato {
  count: number;
  costo_lavori_eff: number;
  costo_lavori_prev: number;
  finanz_statali: number;
  finanz_europei: number;
  finanz_enti_terr: number;
  finanz_privati: number;
  finanz_altri: number;
}
interface BdapBuyerKpi {
  nome_titolare: string;
  totale: BdapStato;
  per_stato: Record<string, BdapStato>;
  top_settori: Array<{ settore: string; count: number; costo: number }>;
}
interface BdapAggregato {
  _etl_version: string;
  _source: string;
  data: Record<string, BdapBuyerKpi>;
}

export const comuneOverview: ToolDefinition = {
  description:
    "Vista d'insieme di un comune italiano. Anagrafica unificata (ISTAT+IPA) + KPI aggregati su contratti pubblici (ANAC), opere pubbliche (BDAP MOP), spese (SIOPE), progetti coesione (OpenCoesione), demografia (ISTAT POSAS).",
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
        description: "Nome esatto del comune (es. 'Lecce')",
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

    const bundle = await fetchR2Json<ComuniBundle>(env, "lookup/comuni-bundle.json");
    if (!bundle?.comuni) {
      return { error: "comuni_bundle_not_found", hint: "Run anagrafica ETL first" };
    }

    let detail: ComuneDetail | undefined;
    if (istatCode) {
      detail = bundle.comuni[istatCode];
    } else if (denominazione) {
      const target = denominazione.toLowerCase().trim();
      detail = Object.values(bundle.comuni).find(
        (c) => c.denominazione.toLowerCase() === target
      );
    }
    if (!detail) {
      return {
        error: "comune_not_found",
        searched: { istat_code: istatCode, denominazione },
        hint: "Use search_comune to find the correct istat_code",
      };
    }

    // Join ANAC contratti
    let contrattiKpi: unknown = detail.kpi.contratti;
    let anacPeriod: string[] | null = null;
    if (detail.codice_fiscale) {
      try {
        const anac = await fetchR2Json<AnacAggregato>(env, "lookup/anac-aggregato.json");
        if (anac?.data) {
          anacPeriod = anac._period_files || null;
          const k = anac.data[detail.codice_fiscale];
          if (k) {
            contrattiKpi = {
              count: k.count,
              importo_totale: k.importo_totale,
              importo_medio: k.count > 0 ? Math.round(k.importo_totale / k.count) : 0,
              first_award_date: k.first_award_date,
              last_award_date: k.last_award_date,
              distinct_cpv: k.distinct_cpv,
              top_cpv: k.top_cpv,
              _source: "ANAC OCDS",
              _period: anacPeriod,
            };
          }
        }
      } catch {}
    }

    // Join BDAP opere
    let opereKpi: unknown = detail.kpi.opere;
    let bdapSource: string | null = null;
    if (detail.codice_fiscale) {
      try {
        const bdap = await fetchR2Json<BdapAggregato>(env, "lookup/bdap-aggregato.json");
        if (bdap?.data) {
          bdapSource = bdap._source || null;
          const k = bdap.data[detail.codice_fiscale];
          if (k) {
            const t = k.totale;
            opereKpi = {
              count: t.count,
              costo_lavori_eff: t.costo_lavori_eff,
              costo_lavori_prev: t.costo_lavori_prev,
              finanz: {
                statali: t.finanz_statali,
                europei: t.finanz_europei,
                enti_terr: t.finanz_enti_terr,
                privati: t.finanz_privati,
                altri: t.finanz_altri,
              },
              per_stato: k.per_stato,
              top_settori: k.top_settori,
              _source: "BDAP MOP",
            };
          }
        }
      } catch {}
    }

    return {
      anagrafica: {
        istat_code: detail.istat_code,
        denominazione: detail.denominazione,
        provincia: detail.provincia,
        regione: detail.regione,
        codice_ipa: detail.codice_ipa,
        codice_fiscale: detail.codice_fiscale,
        nome_categoria: detail.nome_categoria,
        codice_catastale: detail.codice_catastale,
      },
      kpi: {
        contratti: contrattiKpi,
        opere: opereKpi,
        spese_siope: detail.kpi.spese_siope,
        coesione: detail.kpi.coesione,
        popolazione: detail.kpi.popolazione,
      },
      _etl_version: bundle._etl_version,
      _anac_period: anacPeriod,
      _bdap_source: bdapSource,
    };
  },
};
