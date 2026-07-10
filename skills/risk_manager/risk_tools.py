"""Risk Manager Skill - Comprehensive multi-factor portfolio risk assessment."""

import logging
from typing import Dict, List, Any, Tuple
from dataclasses import dataclass
import numpy as np
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class RiskPosition:
    """Represents a position with risk metrics."""
    ticker: str
    value: float
    pct_of_portfolio: float
    beta: float = 1.0
    sector: str = "Unknown"
    market_cap: float = 0.0


class VolatilityRiskCalculator:
    """Calculates portfolio volatility based on beta-weighting."""
    
    @staticmethod
    def calculate_portfolio_beta(positions: List[RiskPosition]) -> float:
        """
        Calculate weighted average beta (portfolio volatility metric).
        
        Portfolio Beta = sum(weight * beta) for each position
        - 1.0 = moves with market
        - >1.0 = more volatile than market (risky)
        - <1.0 = less volatile than market (stable)
        """
        if not positions:
            return 1.0
        
        total_beta = sum(pos.pct_of_portfolio / 100 * pos.beta for pos in positions)
        return round(total_beta, 2)
    
    @staticmethod
    def classify_volatility_risk(portfolio_beta: float, risk_tolerance: str = "medium") -> Dict[str, Any]:
        """Classify volatility risk relative to user tolerance."""
        tolerance_thresholds = {
            "conservative": 0.8,
            "moderate": 1.2,
            "aggressive": 1.8,
            "very_aggressive": 2.5
        }
        
        threshold = tolerance_thresholds.get(risk_tolerance, 1.2)
        
        if portfolio_beta > threshold * 1.3:
            risk_level = "CRITICAL"
        elif portfolio_beta > threshold:
            risk_level = "HIGH"
        elif portfolio_beta > threshold * 0.7:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"
        
        return {
            "portfolio_beta": portfolio_beta,
            "tolerance_threshold": threshold,
            "risk_level": risk_level,
            "exceeds_tolerance": portfolio_beta > threshold,
            "buffer_remaining": round(threshold - portfolio_beta, 2)
        }


class CorrelationRiskCalculator:
    """Calculates correlation risk between portfolio positions."""
    
    @staticmethod
    def estimate_sector_correlation(sectors: List[str]) -> float:
        """
        Estimate correlation based on sector similarity.
        
        Simplified model: positions in same sector have higher correlation.
        Tech positions (TSLA, NVDA, AMD) are highly correlated (~0.7-0.85)
        Different sectors have lower correlation (~0.2-0.5)
        """
        # Sector correlation matrix (simplified)
        sector_correlation_groups = {
            "tech": ["Technology", "Software", "Semiconductors"],
            "finance": ["Finance", "Banking", "Insurance"],
            "healthcare": ["Healthcare", "Biotech", "Pharma"],
            "industrial": ["Industrial", "Manufacturing", "Auto"],
        }
        
        if not sectors or len(sectors) < 2:
            return 0
        
        # Count same-sector positions
        same_sector_pairs = 0
        total_pairs = len(sectors) * (len(sectors) - 1) / 2
        
        for i in range(len(sectors)):
            for j in range(i + 1, len(sectors)):
                # Check if sectors belong to same correlation group
                for group_sectors in sector_correlation_groups.values():
                    if sectors[i] in group_sectors and sectors[j] in group_sectors:
                        same_sector_pairs += 1
                        break
        
        avg_correlation = (same_sector_pairs / total_pairs * 0.7) + ((total_pairs - same_sector_pairs) / total_pairs * 0.25)
        return round(avg_correlation, 4)
    
    @staticmethod
    def calculate_position_pair_risk(pos1: RiskPosition, pos2: RiskPosition, sector_correlation: float) -> Dict[str, Any]:
        """Calculate correlation risk between two positions."""
        return {
            "pair": (pos1.ticker, pos2.ticker),
            "correlation": sector_correlation,
            "combined_weight": round(pos1.pct_of_portfolio + pos2.pct_of_portfolio, 2),
            "risk_level": "HIGH" if sector_correlation > 0.7 and (pos1.pct_of_portfolio + pos2.pct_of_portfolio > 30) else "MEDIUM"
        }


class LiquidityRiskCalculator:
    """Calculates portfolio liquidity risk."""
    
    @staticmethod
    def estimate_position_liquidity(market_cap: float, position_value: float) -> Dict[str, Any]:
        """Estimate how liquid a position is."""
        if market_cap == 0:
            liquidity_ratio = 1.0  # Unknown, assume low liquidity
        else:
            liquidity_ratio = position_value / market_cap  # Position as % of market cap
        
        if liquidity_ratio > 0.01:  # >1% of market cap
            liquidity_risk = "HIGH"
            time_to_liquidate = "Days"  # Market impact expected
        elif liquidity_ratio > 0.001:  # >0.1% of market cap
            liquidity_risk = "MEDIUM"
            time_to_liquidate = "Hours"
        else:
            liquidity_risk = "LOW"
            time_to_liquidate = "Minutes"
        
        return {
            "liquidity_ratio": round(liquidity_ratio, 6),
            "liquidity_risk": liquidity_risk,
            "estimated_liquidation_time": time_to_liquidate
        }
    
    @staticmethod
    def assess_portfolio_liquidity(positions: List[RiskPosition]) -> Dict[str, Any]:
        """Assess overall portfolio liquidity."""
        liquidity_risks = []
        for pos in positions:
            liq = CorrelationRiskCalculator.estimate_position_liquidity(pos.market_cap, pos.value)
            if liq["liquidity_risk"] in ["HIGH", "CRITICAL"]:
                liquidity_risks.append((pos.ticker, liq["liquidity_risk"]))
        
        if liquidity_risks:
            overall_risk = "HIGH" if len(liquidity_risks) > 1 else "MEDIUM"
        else:
            overall_risk = "LOW"
        
        return {
            "overall_liquidity_risk": overall_risk,
            "high_risk_positions": liquidity_risks
        }


class ConcentrationRiskCalculator:
    """Calculates concentration risk (same as Portfolio Analyzer HHI)."""
    
    @staticmethod
    def calculate_herfindahl_index(positions: List[RiskPosition]) -> float:
        """HHI for concentration risk."""
        if not positions:
            return 0
        hhi = sum((pos.pct_of_portfolio / 100) ** 2 for pos in positions)
        return round(hhi, 4)
    
    @staticmethod
    def classify_concentration(hhi: float) -> str:
        """Classify concentration level."""
        if hhi < 0.25:
            return "LOW"
        elif hhi < 0.40:
            return "MODERATE"
        else:
            return "HIGH"


class RiskScorer:
    """Composite risk scoring."""
    
    @staticmethod
    def calculate_composite_risk_score(
        volatility_risk: float,
        correlation_risk: float,
        concentration_risk: float,
        liquidity_risk: str
    ) -> Tuple[float, str]:
        """
        Composite risk score (0-100).
        
        Components:
        - Volatility: 35% weight
        - Correlation: 25% weight
        - Concentration: 25% weight
        - Liquidity: 15% weight
        """
        # Normalize components to 0-1 scale
        vol_score = min(volatility_risk / 2.5, 1.0)  # Normalize to 0-1
        
        # Liquidity risk to numeric
        liquidity_numeric = {"LOW": 0.1, "MEDIUM": 0.5, "HIGH": 0.9, "CRITICAL": 1.0}.get(liquidity_risk, 0.5)
        
        # Weighted composite
        composite = (
            vol_score * 0.35 +
            correlation_risk * 0.25 +
            concentration_risk * 0.25 +
            liquidity_numeric * 0.15
        )
        
        # Convert to 0-100 scale (where 100 = highest risk)
        risk_score = round(composite * 100, 1)
        
        # Classify risk level
        if risk_score < 25:
            risk_rating = "LOW"
        elif risk_score < 50:
            risk_rating = "MODERATE"
        elif risk_score < 75:
            risk_rating = "HIGH"
        else:
            risk_rating = "CRITICAL"
        
        return risk_score, risk_rating


class RiskManager:
    """Main entry point for risk management skill."""
    
    def __init__(self):
        self.volatility_calc = VolatilityRiskCalculator()
        self.correlation_calc = CorrelationRiskCalculator()
        self.liquidity_calc = LiquidityRiskCalculator()
        self.concentration_calc = ConcentrationRiskCalculator()
        self.risk_scorer = RiskScorer()
    
    def assess(self, portfolio_data: Dict[str, Any], risk_tolerance: str = "moderate") -> Dict[str, Any]:
        """
        Comprehensive portfolio risk assessment.
        
        Args:
            portfolio_data: Portfolio state with positions
            risk_tolerance: "conservative", "moderate", "aggressive", "very_aggressive"
        
        Returns:
            Comprehensive risk report
        """
        try:
            positions_data = portfolio_data.get("positions", [])
            total_value = portfolio_data.get("total_value", 1)
            
            # Convert to RiskPosition objects
            positions = [
                RiskPosition(
                    ticker=p["ticker"],
                    value=p.get("price", 0) * p.get("shares", 0),
                    pct_of_portfolio=(p.get("price", 0) * p.get("shares", 0)) / total_value * 100,
                    beta=p.get("beta", 1.0),
                    sector=p.get("sector", "Unknown"),
                    market_cap=p.get("market_cap", 0)
                )
                for p in positions_data
                if p.get("shares", 0) > 0
            ]
            
            if not positions:
                return self._empty_risk_report()
            
            # Calculate risk metrics
            portfolio_beta = self.volatility_calc.calculate_portfolio_beta(positions)
            volatility_risk = self.volatility_calc.classify_volatility_risk(portfolio_beta, risk_tolerance)
            
            sectors = [p.sector for p in positions]
            correlation_risk = self.correlation_calc.estimate_sector_correlation(sectors)
            
            hhi = self.concentration_calc.calculate_herfindahl_index(positions)
            concentration_risk_level = self.concentration_calc.classify_concentration(hhi)
            
            liquidity_risk = self.liquidity_calc.assess_portfolio_liquidity(positions)
            
            # Composite risk score
            risk_score, risk_rating = self.risk_scorer.calculate_composite_risk_score(
                portfolio_beta, correlation_risk, hhi, liquidity_risk["overall_liquidity_risk"]
            )
            
            # Generate alerts
            alerts = self._generate_alerts(
                volatility_risk, positions, sectors, hhi, risk_tolerance
            )
            
            # Generate recommendations
            recommendations = self._generate_recommendations(
                risk_score, risk_rating, alerts, volatility_risk, concentration_risk_level
            )
            
            report = {
                "overall_risk_score": risk_score,
                "risk_rating": risk_rating,
                "volatility_risk": {
                    "portfolio_beta": portfolio_beta,
                    "tolerance": risk_tolerance,
                    "risk_level": volatility_risk["risk_level"],
                    "exceeds_tolerance": volatility_risk["exceeds_tolerance"]
                },
                "correlation_risk": round(correlation_risk, 4),
                "concentration_risk": {
                    "herfindahl_index": hhi,
                    "risk_level": concentration_risk_level
                },
                "liquidity_risk": liquidity_risk["overall_liquidity_risk"],
                "alerts": alerts,
                "recommendations": recommendations,
                "status": "success"
            }
            
            logger.info(f"Risk assessment complete: Score={risk_score}, Rating={risk_rating}")
            return report
        
        except Exception as e:
            logger.error(f"Risk assessment failed: {e}")
            return {
                "overall_risk_score": 0,
                "risk_rating": "ERROR",
                "status": "error",
                "detail": str(e),
                "alerts": [],
                "recommendations": []
            }
    
    def _generate_alerts(self, volatility_risk: Dict, positions: List[RiskPosition],
                        sectors: List[str], hhi: float, risk_tolerance: str) -> List[str]:
        """Generate risk alerts."""
        alerts = []
        
        if volatility_risk["exceeds_tolerance"]:
            buffer = abs(volatility_risk["buffer_remaining"])
            alerts.append(
                f"🚨 Portfolio volatility (β={volatility_risk['portfolio_beta']}) "
                f"exceeds {risk_tolerance} risk tolerance (threshold: {volatility_risk['tolerance_threshold']}). "
                f"Buffer: {buffer}"
            )
        
        # Concentration alert
        if hhi > 0.40:
            alerts.append(f"⚠️ Portfolio is highly concentrated (HHI={hhi:.4f}). Consider diversifying.")
        
        # Sector concentration
        sector_counts = defaultdict(int)
        for sector in sectors:
            sector_counts[sector] += 1
        top_sector_count = max(sector_counts.values())
        if top_sector_count / len(sectors) > 0.5:
            top_sector = max(sector_counts, key=sector_counts.get)
            alerts.append(f"📊 {top_sector} dominates portfolio ({top_sector_count}/{len(sectors)} positions).")
        
        if not alerts:
            alerts.append("✅ Portfolio risk is within acceptable parameters.")
        
        return alerts
    
    def _generate_recommendations(self, risk_score: float, risk_rating: str, alerts: List[str],
                                 volatility_risk: Dict, concentration_risk_level: str) -> List[str]:
        """Generate mitigation recommendations."""
        recommendations = []
        
        if risk_score > 75:
            recommendations.append("🔴 Portfolio is in HIGH RISK regime. Consider immediate rebalancing or position reduction.")
        elif risk_score > 50:
            recommendations.append("🟡 Consider reducing portfolio risk. Add stable positions or reduce volatile holdings.")
        else:
            recommendations.append("🟢 Current risk profile is manageable. Continue monitoring.")
        
        if volatility_risk["exceeds_tolerance"]:
            recommendations.append(f"Reduce volatility by replacing high-beta holdings with lower-beta alternatives.")
        
        if concentration_risk_level == "HIGH":
            recommendations.append("Add uncorrelated positions from different sectors to reduce concentration.")
        
        return recommendations
    
    def _empty_risk_report(self) -> Dict[str, Any]:
        """Return empty risk report."""
        return {
            "overall_risk_score": 0,
            "risk_rating": "N/A",
            "status": "empty",
            "alerts": ["Portfolio is empty."],
            "recommendations": ["Add positions to assess risk."]
        }
