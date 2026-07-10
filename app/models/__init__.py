"""API models and schemas for request/response validation."""

from pydantic import BaseModel, Field
from typing import List, Optional


class AnalysisRequest(BaseModel):
    """Request model for analysis endpoint."""
    
    user_input: str = Field(
        ...,
        description="User query for analysis",
        min_length=1,
        max_length=2000,
    )


class AnalysisResponse(BaseModel):
    """Response model for analysis endpoint."""
    
    status: str
    routing_decision: str
    logs: List[str]
    report_markdown: str
    error_message: str = ""
    execution_time_ms: float


__all__ = ["AnalysisRequest", "AnalysisResponse"]
