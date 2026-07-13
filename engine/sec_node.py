"""SEC evidence pipeline for AlphaChannel's 2026 Slack Hackathon agent."""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, ClassVar, Iterable, Literal
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

logger = logging.getLogger("alpha_channel.hackathon_2026.engine.sec")


class SecFilingDocument(BaseModel):
    ticker: str
    company_name: str
    cik: str
    form: str
    filing_date: str
    accession_number: str
    primary_document: str
    filing_url: str
    text: str = Field(repr=False)


class _FilingHTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and data.strip():
            self._chunks.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self._chunks)).strip()


class SecEdgarClient:
    """SEC-compliant latest-filing retriever with process-level caching and throttling."""

    TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
    ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
    _lock: ClassVar[threading.RLock] = threading.RLock()
    _ticker_cache: ClassVar[dict[str, dict[str, Any]] | None] = None
    _filing_cache: ClassVar[dict[tuple[str, tuple[str, ...]], SecFilingDocument]] = {}
    _last_request_at: ClassVar[float] = 0.0

    def __init__(
        self,
        user_agent: str | None = None,
        timeout_seconds: float | None = None,
        backend: Literal["edgartools", "raw"] | None = None,
    ) -> None:
        self.user_agent = (
            user_agent
            or os.getenv("SEC_EDGAR_USER_AGENT")
            or os.getenv("EDGAR_IDENTITY")
            or ""
        ).strip()
        configured_timeout = timeout_seconds or float(os.getenv("SEC_EDGAR_HTTP_TIMEOUT_SECONDS", "8"))
        self.timeout_seconds = max(2.0, configured_timeout)
        self.backend: Literal["edgartools", "raw"] = (
            backend
            or os.getenv("SEC_EDGAR_BACKEND", "raw").strip().lower()
        )  # type: ignore[assignment]
        if self.backend not in {"edgartools", "raw"}:
            raise ValueError("SEC_EDGAR_BACKEND must be 'edgartools' or 'raw'.")

    def fetch_latest_filing(
        self,
        ticker: str,
        forms: tuple[str, ...] = ("10-Q", "10-K", "20-F", "6-K"),
    ) -> SecFilingDocument:
        if not self.user_agent:
            raise RuntimeError(
                "SEC EDGAR identity is required. Set EDGAR_IDENTITY='Organization contact@example.com' "
                "in .env for EdgarTools fair access (SEC_EDGAR_USER_AGENT is also supported)."
            )
        normalized_ticker = ticker.strip().upper()
        cache_key = (normalized_ticker, forms)
        with self._lock:
            cached = self._filing_cache.get(cache_key)
            if cached:
                logger.info(
                    "sec_edgar_cache_hit ticker=%s form=%s filing_date=%s",
                    normalized_ticker,
                    cached.form,
                    cached.filing_date,
                )
                return cached.model_copy(deep=True)

        if self.backend == "edgartools":
            try:
                document = self._fetch_with_edgartools_bounded(normalized_ticker, forms)
            except Exception as exc:
                logger.warning(
                    "edgartools_fetch_failed_falling_back_to_raw",
                    extra={"ticker": normalized_ticker, "error": str(exc)},
                )
                document = self._fetch_with_raw_client(normalized_ticker, forms)
        else:
            document = self._fetch_with_raw_client(normalized_ticker, forms)

        with self._lock:
            self._filing_cache[cache_key] = document
        logger.info(
            "sec_edgar_filing_fetched ticker=%s form=%s filing_date=%s text_chars=%d backend=%s",
            document.ticker,
            document.form,
            document.filing_date,
            len(document.text),
            self.backend,
        )
        return document.model_copy(deep=True)

    def _fetch_with_edgartools_bounded(
        self,
        ticker: str,
        forms: tuple[str, ...],
    ) -> SecFilingDocument:
        """Bound EdgarTools so the official raw fallback can run within the MCP deadline."""
        result_queue: queue.Queue[SecFilingDocument | BaseException] = queue.Queue(maxsize=1)

        def fetch() -> None:
            try:
                result_queue.put_nowait(self._fetch_with_edgartools(ticker, forms))
            except BaseException as exc:
                result_queue.put_nowait(exc)

        worker = threading.Thread(target=fetch, name=f"sec-edgartools-{ticker}", daemon=True)
        worker.start()
        deadline = max(2.0, float(os.getenv("SEC_EDGARTOOLS_TIMEOUT_SECONDS", "8")))
        worker.join(timeout=deadline)
        if worker.is_alive():
            raise TimeoutError(f"EdgarTools did not complete within {deadline:.0f} seconds for {ticker}.")
        try:
            result = result_queue.get_nowait()
        except queue.Empty as exc:
            raise RuntimeError(f"EdgarTools returned no filing result for {ticker}.") from exc
        if isinstance(result, BaseException):
            raise result
        return result

    def _fetch_with_edgartools(
        self, ticker: str, forms: tuple[str, ...]
    ) -> SecFilingDocument:
        """Retrieve and normalize a filing through EdgarTools' maintained API."""
        # Keep EdgarTools' cache out of an unwritable user profile on managed hosts.
        os.environ.setdefault(
            "EDGAR_LOCAL_DATA_DIR",
            str(Path(__file__).resolve().parents[1] / ".edgar-data"),
        )
        # EdgarTools reads this canonical identity variable directly.
        os.environ.setdefault("EDGAR_IDENTITY", self.user_agent)
        from edgar import Company

        company = Company(ticker)
        filing = company.get_filings(form=list(forms), amendments=False).latest()
        if filing is None:
            raise RuntimeError(f"EdgarTools returned no recent filing for {ticker}.")
        filing_text = str(filing.text()).strip()
        if len(filing_text) < 1000:
            raise RuntimeError(f"SEC filing text extraction produced insufficient content for {ticker}.")

        primary_document = ""
        try:
            primary = filing.document
            primary_document = str(
                getattr(primary, "name", None)
                or getattr(primary, "filename", None)
                or getattr(primary, "document", None)
                or ""
            )
        except Exception:
            logger.debug("edgartools_primary_document_unavailable", extra={"ticker": ticker})

        accession_number = str(
            getattr(filing, "accession_number", None)
            or getattr(filing, "accession_no", "")
        )
        cik = str(getattr(filing, "cik", "")).zfill(10)
        return SecFilingDocument(
            ticker=ticker,
            company_name=str(getattr(filing, "company", ticker)),
            cik=cik,
            form=str(getattr(filing, "form", "")),
            filing_date=str(getattr(filing, "filing_date", "")),
            accession_number=accession_number,
            primary_document=primary_document,
            filing_url=str(getattr(filing, "url", getattr(filing, "homepage_url", ""))),
            text=filing_text,
        )

    def _fetch_with_raw_client(
        self, normalized_ticker: str, forms: tuple[str, ...]
    ) -> SecFilingDocument:
        company = self._resolve_company(normalized_ticker)
        cik_padded = str(company["cik_str"]).zfill(10)
        submissions = self._get_json(self.SUBMISSIONS_URL.format(cik=cik_padded))
        recent = submissions.get("filings", {}).get("recent", {})
        columns = ("form", "accessionNumber", "primaryDocument", "filingDate")
        rows = [dict(zip(columns, values, strict=True)) for values in zip(*(recent.get(column, []) for column in columns))]
        filing = next((row for row in rows if row["form"] in forms and row["primaryDocument"]), None)
        if filing is None:
            raise RuntimeError(f"SEC EDGAR returned no recent {'/'.join(forms)} filing for {normalized_ticker}.")

        cik_unpadded = str(int(cik_padded))
        accession_path = str(filing["accessionNumber"]).replace("-", "")
        filing_url = self.ARCHIVES_URL.format(
            cik=cik_unpadded,
            accession=accession_path,
            document=filing["primaryDocument"],
        )
        filing_html = self._get_text(filing_url)
        parser = _FilingHTMLTextExtractor()
        parser.feed(filing_html)
        filing_text = parser.text()
        if len(filing_text) < 1000:
            raise RuntimeError(f"SEC filing text extraction produced insufficient content for {normalized_ticker}.")

        document = SecFilingDocument(
            ticker=normalized_ticker,
            company_name=str(company["title"]),
            cik=cik_padded,
            form=str(filing["form"]),
            filing_date=str(filing["filingDate"]),
            accession_number=str(filing["accessionNumber"]),
            primary_document=str(filing["primaryDocument"]),
            filing_url=filing_url,
            text=filing_text,
        )
        return document

    def _resolve_company(self, ticker: str) -> dict[str, Any]:
        with self._lock:
            if self._ticker_cache is None:
                payload = self._get_json(self.TICKERS_URL)
                self.__class__._ticker_cache = {
                    str(item["ticker"]).upper(): item
                    for item in payload.values()
                    if isinstance(item, dict) and item.get("ticker")
                }
            company = self._ticker_cache.get(ticker) if self._ticker_cache else None
        if company is None:
            raise ValueError(f"Ticker {ticker} was not found in SEC company_tickers.json.")
        return company

    def _get_json(self, url: str) -> dict[str, Any]:
        payload = json.loads(self._request(url).decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError(f"SEC endpoint returned a non-object JSON payload: {url}")
        return payload

    def _get_text(self, url: str) -> str:
        return self._request(url).decode("utf-8", errors="replace")

    def _request(self, url: str) -> bytes:
        endpoint = "archives" if "/Archives/" in url else "submissions" if "submissions/" in url else "tickers"
        started_at = time.monotonic()
        logger.info("sec_edgar_http_request_started", extra={"endpoint": endpoint})
        with self._lock:
            wait_seconds = 0.15 - (time.monotonic() - self._last_request_at)
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            request = Request(
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
                },
            )
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read()
            self.__class__._last_request_at = time.monotonic()
        logger.info(
            "sec_edgar_http_request_completed",
            extra={"endpoint": endpoint, "elapsed_seconds": round(time.monotonic() - started_at, 3)},
        )
        return body


class SecRiskMarker(BaseModel):
    source: str = "SEC"
    label: str
    severity: float = Field(ge=0.0, le=1.0)
    evidence: str

    def as_display_text(self) -> str:
        return f"{self.label} ({self.source}, severity {self.severity:.2f}): {self.evidence}"


class SecRiskExtractor:
    """Extract material risk markers from SEC filing text or PDF disclosures."""

    RISK_PATTERNS: dict[str, tuple[float, str]] = {
        "credit delinquency": (0.82, r"\b(delinquen\w+|nonperforming|charge[- ]offs?)\b"),
        "liquidity pressure": (0.76, r"\b(liquidity|cash constraints?|working capital deficit)\b"),
        "covenant breach risk": (0.84, r"\b(covenant|default|acceleration of debt)\b"),
        "going concern warning": (0.95, r"\b(going concern|substantial doubt)\b"),
        "material weakness": (0.88, r"\b(material weakness|internal control deficiency)\b"),
        "impairment pressure": (0.72, r"\b(impairment|write[- ]down|goodwill charge)\b"),
        "insider distribution signal": (0.70, r"\b(insider sales?|stock disposition|10b5[- ]1)\b"),
        "regulatory investigation": (0.86, r"\b(investigation|subpoena|consent order|enforcement action)\b"),
    }

    def extract_risk_markers(
        self,
        *,
        ticker: str,
        filing_text: str | None = None,
        pdf_paths: Iterable[str | Path] | None = None,
    ) -> list[SecRiskMarker]:
        text = (filing_text or "").strip()
        if pdf_paths:
            text = f"{text}\n{self._extract_pdf_text(pdf_paths)}".strip()

        if not text:
            logger.info("sec_node_no_filing_text", extra={"ticker": ticker})
            return [
                SecRiskMarker(
                    label="SEC parser not connected",
                    severity=0.35,
                    evidence=(
                        "No 10-K/10-Q filing text was available to AlphaChannel's 2026 Slack Hackathon "
                        "SEC evidence pipeline."
                    ),
                )
            ]

        normalized = re.sub(r"\s+", " ", text)
        markers: list[SecRiskMarker] = []
        for label, (severity, pattern) in self.RISK_PATTERNS.items():
            match = re.search(pattern, normalized, flags=re.IGNORECASE)
            if not match:
                continue
            evidence = self._window(normalized, match.start(), match.end())
            markers.append(SecRiskMarker(label=label, severity=severity, evidence=evidence))

        if not markers:
            markers.append(
                SecRiskMarker(
                    label="No high-signal SEC risk phrase detected",
                    severity=0.20,
                    evidence="Keyword scan did not identify severe 10-K/10-Q risk language in the supplied text.",
                )
            )

        logger.info(
            "sec_node_markers_extracted",
            extra={"ticker": ticker, "marker_count": len(markers), "max_severity": max(m.severity for m in markers)},
        )
        return markers

    def _extract_pdf_text(self, pdf_paths: Iterable[str | Path]) -> str:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("pypdf is required to parse SEC PDF filings.") from exc

        chunks: list[str] = []
        for pdf_path in pdf_paths:
            path = Path(pdf_path)
            if not path.exists():
                logger.warning("sec_pdf_missing", extra={"path": str(path)})
                continue
            reader = PdfReader(str(path))
            for page in reader.pages:
                chunks.append(page.extract_text() or "")
        return "\n".join(chunks)

    @staticmethod
    def _window(text: str, start: int, end: int, radius: int = 180) -> str:
        left = max(0, start - radius)
        right = min(len(text), end + radius)
        return text[left:right].strip()
