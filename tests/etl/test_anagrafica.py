"""Smoke test: validates that the anagrafica ETL imports correctly.

Tests that hit live ISTAT/IPA URLs are skipped on CI (mark them with @network).
"""

import pytest

import etl
from etl.sources import anagrafica


def test_etl_version():
    assert etl.__version__ == "0.1.0"


def test_anagrafica_constants():
    assert "istat.it" in anagrafica.ISTAT_COMUNI_CSV
    assert "indicepa.gov.it" in anagrafica.IPA_CKAN_BASE
    assert "cruscotto-italia-etl" in anagrafica.USER_AGENT


def test_anagrafica_main_signature():
    """main() should be callable with no args (parsed from sys.argv)."""
    assert callable(anagrafica.main)


@pytest.mark.skip(reason="network-dependent — run manually with: pytest -m network --runnetwork")
@pytest.mark.network
def test_anagrafica_e2e_local(tmp_path):
    """End-to-end test against live ISTAT and IPA.
    Run with: pytest -m network -k e2e_local --runnetwork
    """
    import sys
    sys.argv = ["anagrafica", "--target=local", f"--output-dir={tmp_path}"]
    rc = anagrafica.main()
    assert rc == 0
    assert (tmp_path / "anagrafica_unificata.parquet").exists()
    assert (tmp_path / "istat_comuni.parquet").exists()
    assert (tmp_path / "ipa_enti.parquet").exists()
