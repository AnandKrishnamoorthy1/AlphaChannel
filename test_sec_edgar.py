from __future__ import annotations

import pytest

from engine.sec_node import SecEdgarClient, SecRiskExtractor


class FakeSecEdgarClient(SecEdgarClient):
    _ticker_cache = None
    _filing_cache = {}

    def _get_json(self, url: str) -> dict:  # type: ignore[type-arg]
        if "company_tickers" in url:
            return {"0": {"cik_str": 1373715, "ticker": "NOW", "title": "SERVICENOW, INC."}}
        return {
            "filings": {
                "recent": {
                    "form": ["8-K", "10-Q", "10-K"],
                    "accessionNumber": ["0001-26-000001", "0001-26-000002", "0001-25-000003"],
                    "primaryDocument": ["eightk.htm", "now-20260630.htm", "now-20251231.htm"],
                    "filingDate": ["2026-07-01", "2026-06-30", "2026-02-01"],
                }
            }
        }

    def _get_text(self, url: str) -> str:
        return "<html><body><h1>Risk Factors</h1><p>" + (
            "The company faces regulatory investigation and liquidity pressure. " * 40
        ) + "</p></body></html>"


def test_latest_sec_filing_is_resolved_and_parsed() -> None:
    client = FakeSecEdgarClient(user_agent="AlphaChannel test@example.com", backend="raw")
    filing = client.fetch_latest_filing("now")

    assert filing.form == "10-Q"
    assert filing.filing_date == "2026-06-30"
    assert filing.primary_document == "now-20260630.htm"
    assert "/1373715/000126000002/now-20260630.htm" in filing.filing_url
    markers = SecRiskExtractor().extract_risk_markers(ticker="NOW", filing_text=filing.text)
    assert {marker.label for marker in markers} >= {"liquidity pressure", "regulatory investigation"}


def test_sec_client_requires_declared_identity() -> None:
    with pytest.raises(RuntimeError, match="EDGAR_IDENTITY"):
        SecEdgarClient(user_agent=" ").fetch_latest_filing("NOW")
