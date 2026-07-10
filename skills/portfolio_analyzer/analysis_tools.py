"""Portfolio Analyzer Skill - Deep portfolio analysis and diversification metrics."""

import logging
from typing import Dict, List, Any
from dataclasses import dataclass
from collections import defaultdict
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Represents a single portfolio position."""
    ticker: str
    shares: float
    current_price: float
    sector: str = "Unknown"
    market_cap: float = 0.0
    
    @property
    def value(self) -> float:
        return self.shares * self.current_price


class AssetAllocationCalculator:
    """Calculates asset allocation percentages and weights."""
    
    @staticmethod
    def calculate_allocation(positions: List[Position], total_value: float) -> List[Dict[str, Any]]:
        """Calculate percentage allocation for each position."""
        allocation = []
        for pos in positions:
            pos_value = pos.value
            pct = (pos_value / total_value * 100) if total_value > 0 else 0
            allocation.append({
                "ticker": pos.ticker,
                "shares": pos.shares,
                "value": round(pos_value, 2),
                "pct": round(pct, 2),
                "sector": pos.sector
            })
        return sorted(allocation, key=lambda x: x["value"], reverse=True)
    
    @staticmethod
    def calculate_sector_weights(positions: List[Position], total_value: float) -> Dict[str, float]:
        """Calculate sector allocation percentages."""
        sector_values = defaultdict(float)
        for pos in positions:
            sector_values[pos.sector] += pos.value
        
        sector_weights = {
            sector: round((value / total_value * 100), 2)
            for sector, value in sector_values.items()
            if total_value > 0
        }
        return dict(sorted(sector_weights.items(), key=lambda x: x[1], reverse=True))


class DiversificationScorer:
    """Computes diversification metrics using Herfindahl-Hirschman Index (HHI) and correlation."""
    
    @staticmethod
    def calculate_herfindahl_index(positions: List[Position], total_value: float) -> float:
        """
        Calculate HHI (Herfindahl-Hirschman Index).
        
        HHI = sum of (market_share_pct)^2
        - 0: Perfect diversification
        - 1: Single position (complete concentration)
        - <0.25: Well diversified
        - 0.25-0.40: Moderate concentration
        - >0.40: High concentration
        """
        if not positions or total_value == 0:
            return 0
        
        hhi = sum(((pos.value / total_value) ** 2) for pos in positions)
        return min(round(hhi, 4), 1.0)  # Normalize to 0-1
    
    @staticmethod
    def calculate_effective_positions(hhi: float) -> float:
        """
        Calculate effective number of positions based on HHI.
        
        Effective N = 1 / HHI
        - Tells you how many "equally-weighted" positions this is equivalent to
        """
        if hhi == 0:
            return 0
        return round(1 / hhi, 1)
    
    @staticmethod
    def assess_concentration_risk(allocation: List[Dict]) -> Dict[str, Any]:
        """Identify concentrated positions and risk level."""
        concentrated = []
        risk_level = "Low"
        
        for pos in allocation:
            pct = pos["pct"]
            if pct > 20:
                concentrated.append({
                    "ticker": pos["ticker"],
                    "pct": pct,
                    "risk": "HIGH" if pct > 40 else "MEDIUM"
                })
                if pct > 40:
                    risk_level = "HIGH"
                elif risk_level != "HIGH":
                    risk_level = "MEDIUM"
        
        return {
            "concentrated_positions": concentrated,
            "risk_level": risk_level,
            "max_position_pct": max((p["pct"] for p in allocation), default=0)
        }
    
    @staticmethod
    def calculate_diversification_score(hhi: float, sector_concentration: float, position_count: int) -> float:
        """
        Composite diversification score (0-100).
        
        Combines HHI (position level), sector concentration, and count.
        """
        # HHI contributes 50% (lower is better)
        hhi_score = (1 - hhi) * 50
        
        # Sector concentration contributes 30% (lower is better)
        sector_score = (1 - sector_concentration) * 30
        
        # Position count contributes 20% (more is better, cap at 10 positions)
        position_score = min(position_count / 10, 1.0) * 20
        
        return round(hhi_score + sector_score + position_score, 1)


class SectorAnalyzer:
    """Analyzes sector-level portfolio composition."""
    
    @staticmethod
    def get_sector_concentration(sector_weights: Dict[str, float]) -> float:
        """
        Calculate sector concentration (similar to HHI but for sectors).
        
        High value = portfolio is concentrated in few sectors
        """
        if not sector_weights:
            return 0
        
        # Convert percentages to decimals
        sector_values = [w / 100 for w in sector_weights.values()]
        concentration = sum(v ** 2 for v in sector_values)
        return round(min(concentration, 1.0), 4)
    
    @staticmethod
    def identify_sector_bias(sector_weights: Dict[str, float], threshold: float = 50.0) -> Dict[str, Any]:
        """Identify if portfolio is heavily biased toward single sector."""
        if not sector_weights:
            return {"bias": "None", "dominant_sector": None, "pct": 0}
        
        top_sector = max(sector_weights.items(), key=lambda x: x[1])
        
        if top_sector[1] >= threshold:
            return {
                "bias": "HIGH",
                "dominant_sector": top_sector[0],
                "pct": top_sector[1],
                "warning": f"Portfolio is {top_sector[1]:.1f}% in {top_sector[0]}"
            }
        elif top_sector[1] >= 35:
            return {
                "bias": "MODERATE",
                "dominant_sector": top_sector[0],
                "pct": top_sector[1],
                "warning": f"Significant concentration in {top_sector[0]} ({top_sector[1]:.1f}%)"
            }
        else:
            return {"bias": "LOW", "dominant_sector": None, "pct": 0}


class PortfolioAnalyzer:
    """Main entry point for portfolio analysis skill."""
    
    def __init__(self):
        self.allocation_calc = AssetAllocationCalculator()
        self.diversification = DiversificationScorer()
        self.sector_analyzer = SectorAnalyzer()
    
    def analyze(self, portfolio_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Comprehensive portfolio analysis.
        
        Args:
            portfolio_data: {
                "positions": [
                    {"ticker": "TSLA", "shares": 10, "price": 393.45, "sector": "Auto"},
                    ...
                ],
                "cash": 1000,
                "total_value": 10000
            }
        
        Returns:
            Comprehensive analysis report
        """
        try:
            # Extract data
            positions_data = portfolio_data.get("positions", [])
            cash = portfolio_data.get("cash", 0)
            total_value = portfolio_data.get("total_value", cash)
            
            if total_value == 0:
                logger.warning("Portfolio total value is 0")
                return self._empty_analysis()
            
            # Convert to Position objects
            positions = [
                Position(
                    ticker=p["ticker"],
                    shares=p.get("shares", 0),
                    current_price=p.get("price", 0),
                    sector=p.get("sector", "Unknown"),
                    market_cap=p.get("market_cap", 0)
                )
                for p in positions_data
                if p.get("shares", 0) > 0 and p.get("price", 0) > 0
            ]
            
            # Calculate allocations
            allocation = self.allocation_calc.calculate_allocation(positions, total_value)
            sector_weights = self.allocation_calc.calculate_sector_weights(positions, total_value)
            
            # Calculate diversification metrics
            hhi = self.diversification.calculate_herfindahl_index(positions, total_value)
            effective_positions = self.diversification.calculate_effective_positions(hhi)
            concentration_risk = self.diversification.assess_concentration_risk(allocation)
            sector_concentration = self.sector_analyzer.get_sector_concentration(sector_weights)
            diversification_score = self.diversification.calculate_diversification_score(
                hhi, sector_concentration, len(positions)
            )
            
            # Sector analysis
            sector_bias = self.sector_analyzer.identify_sector_bias(sector_weights)
            
            # Generate recommendations
            recommendations = self._generate_recommendations(
                allocation, sector_weights, concentration_risk, sector_bias, diversification_score
            )
            
            # Build report
            report = {
                "portfolio_summary": {
                    "total_value": round(total_value, 2),
                    "cash": round(cash, 2),
                    "invested": round(total_value - cash, 2),
                    "num_positions": len(positions)
                },
                "allocation": allocation,
                "sector_weights": sector_weights,
                "diversification_metrics": {
                    "herfindahl_index": hhi,
                    "effective_positions": effective_positions,
                    "diversification_score": diversification_score,
                    "risk_assessment": concentration_risk
                },
                "sector_analysis": {
                    "sector_concentration": sector_concentration,
                    "sector_bias": sector_bias
                },
                "recommendations": recommendations,
                "status": "success"
            }
            
            logger.info(f"Portfolio analysis complete: {len(positions)} positions, HHI={hhi:.4f}, Score={diversification_score}")
            return report
        
        except Exception as e:
            logger.error(f"Portfolio analysis failed: {e}")
            return {
                "status": "error",
                "detail": str(e),
                "recommendations": []
            }
    
    def _empty_analysis(self) -> Dict[str, Any]:
        """Return empty portfolio analysis structure."""
        return {
            "portfolio_summary": {"total_value": 0, "cash": 0, "invested": 0, "num_positions": 0},
            "allocation": [],
            "sector_weights": {},
            "diversification_metrics": {
                "herfindahl_index": 0,
                "effective_positions": 0,
                "diversification_score": 0,
                "risk_assessment": {"concentrated_positions": [], "risk_level": "N/A", "max_position_pct": 0}
            },
            "sector_analysis": {"sector_concentration": 0, "sector_bias": {"bias": "None"}},
            "recommendations": ["Portfolio is empty. Add positions to see analysis."],
            "status": "empty"
        }
    
    def _generate_recommendations(self, allocation: List[Dict], sector_weights: Dict[str, float],
                                  concentration_risk: Dict, sector_bias: Dict, div_score: float) -> List[str]:
        """Generate actionable portfolio recommendations."""
        recommendations = []
        
        # Diversification score recommendations
        if div_score < 40:
            recommendations.append("🔴 Portfolio is highly concentrated. Consider diversifying across more positions and sectors.")
        elif div_score < 60:
            recommendations.append("🟡 Moderate concentration risk. Adding 2-3 uncorrelated positions could improve diversification.")
        else:
            recommendations.append("🟢 Portfolio is well-diversified. Good sector and position balance.")
        
        # Concentration risk recommendations
        if concentration_risk["risk_level"] == "HIGH":
            top_pos = concentration_risk["concentrated_positions"][0]
            recommendations.append(f"⚠️ {top_pos['ticker']} is {top_pos['pct']:.1f}% of portfolio. Consider reducing to <25%.")
        elif concentration_risk["risk_level"] == "MEDIUM":
            positions = concentration_risk["concentrated_positions"]
            if positions:
                rec = f"⚠️ Top 3 positions: {', '.join(f\"{p['ticker']} ({p['pct']:.1f}%)\" for p in positions[:3])}. Consider rebalancing."
                recommendations.append(rec)
        
        # Sector bias recommendations
        if sector_bias["bias"] == "HIGH":
            recommendations.append(
                f"🎯 {sector_bias['dominant_sector']} is {sector_bias['pct']:.1f}% of portfolio. "
                f"Add positions from underrepresented sectors (e.g., Healthcare, Utilities) for hedge."
            )
        elif sector_bias["bias"] == "MODERATE":
            recommendations.append(f"📊 Consider slight rebalance: {sector_bias['dominant_sector']} is {sector_bias['pct']:.1f}%.")
        
        # Generic recommendations
        if len(allocation) < 5:
            recommendations.append(f"📈 With {len(allocation)} positions, adding 2-3 more would improve diversification.")
        
        return recommendations if recommendations else ["Portfolio allocation looks balanced. Monitor for market changes."]
