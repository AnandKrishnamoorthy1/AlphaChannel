from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, Field

logger = logging.getLogger("alpha_channel.engine.sec")


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
                        "No 10-K/10-Q filing text was supplied by the wrapper. "
                        "Connect Corporate Intelligence Core SEC ingestion for live footnoted risk markers."
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
