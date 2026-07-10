"""Stop-Loss/Take-Profit Triggers Skill - Portfolio position monitoring and alert generation."""

import logging
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from collections import defaultdict

logger = logging.getLogger(__name__)


class AlertSeverity(Enum):
    """Alert severity levels."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class Trigger:
    """Represents an active risk trigger."""
    trigger_type: str  # "concentration_breach", "stop_loss", "take_profit", "correlation_spike"
    ticker: str
    severity: AlertSeverity
    current_value: float
    threshold_value: float
    recommendation: str
    reason: str


class ConcentrationTriggerDetector:
    """Detects when positions exceed concentration thresholds."""
    
    # LOWER THRESHOLD FOR EASY DEMO: 20% for earlier trigger detection
    DEFAULT_CONCENTRATION_THRESHOLD = 0.20
    
    @staticmethod
    def check_concentration(positions: List[Dict[str, Any]], threshold: float = None) -> List[Dict[str, Any]]:
        """
        Check if any position exceeds concentration threshold.
        
        Args:
            positions: List of position dicts with 'ticker', 'value', 'pct_of_portfolio'
            threshold: Override default threshold (0.50)
        
        Returns:
            List of concentration breach triggers
        """
        threshold = threshold or ConcentrationTriggerDetector.DEFAULT_CONCENTRATION_THRESHOLD
        triggers = []
        
        for pos in positions:
            pct = pos.get("pct_of_portfolio", 0) / 100  # Convert from percentage to decimal
            
            if pct > threshold:
                excess_pct = (pct - threshold) * 100
                pos_value = pos.get("value", 0)
                excess_value = pos_value * (pct - threshold) / pct if pct > 0 else 0
                
                severity = AlertSeverity.CRITICAL if pct > threshold * 1.5 else AlertSeverity.HIGH
                
                triggers.append({
                    "type": "concentration_breach",
                    "ticker": pos.get("ticker", "UNKNOWN"),
                    "severity": severity.value,
                    "current_pct": round(pct * 100, 1),
                    "threshold_pct": round(threshold * 100, 1),
                    "excess_pct": round(excess_pct, 1),
                    "excess_value": round(excess_value, 2),
                    "recommendation": (
                        f"🚨 {pos.get('ticker')} is {pct*100:.1f}% of portfolio (threshold: {threshold*100:.0f}%). "
                        f"Reduce by ${excess_value:,.2f} to get below threshold."
                    ),
                    "reason": f"Position concentration exceeds {threshold*100:.0f}% threshold"
                })
        
        return sorted(triggers, key=lambda x: x["current_pct"], reverse=True)


class CorrelationSpikeDetector:
    """Detects when correlated positions create concentration risk."""
    
    CORRELATION_THRESHOLD = 0.70
    COMBINED_CONCENTRATION_ALERT = 0.30
    
    @staticmethod
    def estimate_sector_correlation(sectors: List[str]) -> Dict[Tuple[int, int], float]:
        """Estimate correlation based on sector similarity."""
        sector_correlation_groups = {
            "tech": ["Technology", "Software", "Semiconductors", "Auto", "EV"],
            "finance": ["Finance", "Banking", "Insurance"],
            "healthcare": ["Healthcare", "Biotech", "Pharma"],
            "industrial": ["Industrial", "Manufacturing"],
        }
        
        correlation_matrix = {}
        
        for i in range(len(sectors)):
            for j in range(i + 1, len(sectors)):
                # Check if sectors belong to same group
                same_group = False
                for group_sectors in sector_correlation_groups.values():
                    if sectors[i] in group_sectors and sectors[j] in group_sectors:
                        same_group = True
                        break
                
                # Assign correlation
                if same_group:
                    correlation = 0.72  # High correlation
                else:
                    correlation = 0.25  # Low correlation
                
                correlation_matrix[(i, j)] = correlation
        
        return correlation_matrix
    
    @staticmethod
    def check_correlation_spikes(positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Check for correlated positions creating concentration risk."""
        if len(positions) < 2:
            return []
        
        triggers = []
        sectors = [pos.get("sector", "Unknown") for pos in positions]
        correlation_matrix = CorrelationSpikeDetector.estimate_sector_correlation(sectors)
        
        for (i, j), correlation in correlation_matrix.items():
            if i >= len(positions) or j >= len(positions):
                continue
            
            pos1 = positions[i]
            pos2 = positions[j]
            
            pct1 = pos1.get("pct_of_portfolio", 0) / 100
            pct2 = pos2.get("pct_of_portfolio", 0) / 100
            combined_pct = pct1 + pct2
            
            # Alert if high correlation + significant combined concentration
            if correlation > CorrelationSpikeDetector.CORRELATION_THRESHOLD and \
               combined_pct > CorrelationSpikeDetector.COMBINED_CONCENTRATION_ALERT:
                
                triggers.append({
                    "type": "correlation_spike",
                    "ticker_pair": (pos1.get("ticker"), pos2.get("ticker")),
                    "severity": AlertSeverity.MEDIUM.value,
                    "correlation": round(correlation, 2),
                    "combined_pct": round(combined_pct * 100, 1),
                    "sector_pair": (sectors[i], sectors[j]),
                    "recommendation": (
                        f"⚠️ High correlation alert: {pos1.get('ticker')} + {pos2.get('ticker')} "
                        f"({correlation:.2f} correlation, {combined_pct*100:.1f}% combined). "
                        f"Consider reducing one position for diversification."
                    ),
                    "reason": "Correlated positions create hidden concentration risk"
                })
        
        return triggers


class ProfitLossTriggerDetector:
    """Detects stop-loss and take-profit thresholds."""
    
    STOP_LOSS_PCT = -0.10  # -10%
    TAKE_PROFIT_PCT = 0.30  # +30%
    
    @staticmethod
    def check_stop_loss(portfolio_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Check if any position hit stop-loss threshold (-10%)."""
        triggers = []
        
        for holding in portfolio_history:
            if not holding.get("avg_cost") or holding["avg_cost"] == 0:
                continue
            
            current_price = holding.get("current_price", 0)
            avg_cost = holding.get("avg_cost", 1)
            
            unrealized_return = (current_price - avg_cost) / avg_cost
            
            if unrealized_return < ProfitLossTriggerDetector.STOP_LOSS_PCT:
                loss_pct = abs(unrealized_return) * 100
                loss_amount = holding.get("shares", 0) * (current_price - avg_cost)
                
                triggers.append({
                    "type": "stop_loss",
                    "ticker": holding.get("ticker", "UNKNOWN"),
                    "severity": AlertSeverity.HIGH.value,
                    "unrealized_return_pct": round(unrealized_return * 100, 1),
                    "threshold_pct": ProfitLossTriggerDetector.STOP_LOSS_PCT * 100,
                    "loss_amount": round(loss_amount, 2),
                    "avg_cost": round(avg_cost, 2),
                    "current_price": round(current_price, 2),
                    "recommendation": (
                        f"⛔ Stop-loss triggered: {holding.get('ticker')} down {loss_pct:.1f}% "
                        f"(${abs(loss_amount):,.2f}). "
                        f"Consider exiting position to limit losses."
                    ),
                    "reason": f"Unrealized loss exceeds stop-loss threshold ({ProfitLossTriggerDetector.STOP_LOSS_PCT*100:.0f}%)"
                })
        
        return triggers
    
    @staticmethod
    def check_take_profit(portfolio_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Check if any position hit take-profit threshold (+30%)."""
        triggers = []
        
        for holding in portfolio_history:
            if not holding.get("avg_cost") or holding["avg_cost"] == 0:
                continue
            
            current_price = holding.get("current_price", 0)
            avg_cost = holding.get("avg_cost", 1)
            
            unrealized_return = (current_price - avg_cost) / avg_cost
            
            if unrealized_return > ProfitLossTriggerDetector.TAKE_PROFIT_PCT:
                gain_pct = unrealized_return * 100
                gain_amount = holding.get("shares", 0) * (current_price - avg_cost)
                
                triggers.append({
                    "type": "take_profit",
                    "ticker": holding.get("ticker", "UNKNOWN"),
                    "severity": AlertSeverity.LOW.value,  # Positive news!
                    "unrealized_return_pct": round(unrealized_return * 100, 1),
                    "threshold_pct": ProfitLossTriggerDetector.TAKE_PROFIT_PCT * 100,
                    "gain_amount": round(gain_amount, 2),
                    "avg_cost": round(avg_cost, 2),
                    "current_price": round(current_price, 2),
                    "recommendation": (
                        f"💰 Take-profit triggered: {holding.get('ticker')} up {gain_pct:.1f}% "
                        f"(+${gain_amount:,.2f}). "
                        f"Consider locking in gains."
                    ),
                    "reason": f"Unrealized gain exceeds take-profit target ({ProfitLossTriggerDetector.TAKE_PROFIT_PCT*100:.0f}%)"
                })
        
        return triggers


class StopLossTakeProfitEngine:
    """Main entry point for stop-loss/take-profit monitoring."""
    
    def __init__(self, concentration_threshold: Optional[float] = None):
        self.concentration_threshold = concentration_threshold or ConcentrationTriggerDetector.DEFAULT_CONCENTRATION_THRESHOLD
        self.concentration_detector = ConcentrationTriggerDetector()
        self.correlation_detector = CorrelationSpikeDetector()
        self.profit_loss_detector = ProfitLossTriggerDetector()
    
    def assess_all_triggers(self, portfolio: Dict[str, Any]) -> Dict[str, Any]:
        """
        Comprehensive trigger assessment across all monitoring dimensions.
        
        Args:
            portfolio: {
                "positions": [{ticker, value, pct_of_portfolio, sector, ...}, ...],
                "history": [{ticker, shares, avg_cost, current_price}, ...],
                "total_value": float,
                "cash": float
            }
        
        Returns:
            {
                "active_triggers": int,
                "triggers": [...],
                "summary": str,
                "status": "success" | "error"
            }
        """
        try:
            positions = portfolio.get("positions", [])
            history = portfolio.get("history", [])
            
            if not positions and not history:
                return self._empty_trigger_report()
            
            # Assess all trigger types
            concentration_triggers = self.concentration_detector.check_concentration(
                positions,
                threshold=self.concentration_threshold
            )
            
            correlation_triggers = self.correlation_detector.check_correlation_spikes(positions)
            
            stop_loss_triggers = self.profit_loss_detector.check_stop_loss(history)
            
            take_profit_triggers = self.profit_loss_detector.check_take_profit(history)
            
            # Combine and rank by severity
            all_triggers = (
                concentration_triggers +
                correlation_triggers +
                stop_loss_triggers +
                take_profit_triggers
            )
            
            # Sort by severity (CRITICAL > HIGH > MEDIUM > LOW)
            severity_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
            all_triggers = sorted(
                all_triggers,
                key=lambda x: severity_rank.get(x.get("severity", "LOW"), 999)
            )
            
            # Generate summary
            trigger_summary = self._generate_summary(all_triggers)
            
            report = {
                "active_triggers": len(all_triggers),
                "triggers": all_triggers,
                "summary": trigger_summary,
                "concentration_threshold_pct": self.concentration_threshold * 100,
                "status": "triggers_found" if all_triggers else "no_triggers"
            }
            
            logger.info(f"Trigger assessment complete: {len(all_triggers)} active triggers")
            return report
        
        except Exception as e:
            logger.error(f"Trigger assessment failed: {e}")
            return {
                "active_triggers": 0,
                "triggers": [],
                "summary": f"Error during assessment: {str(e)}",
                "status": "error"
            }
    
    def _generate_summary(self, triggers: List[Dict[str, Any]]) -> str:
        """Generate human-readable trigger summary."""
        if not triggers:
            return "✅ No active triggers. Portfolio is within risk parameters."
        
        # Count by type and severity
        type_counts = defaultdict(int)
        severity_counts = defaultdict(int)
        
        for trigger in triggers:
            type_counts[trigger.get("type", "unknown")] += 1
            severity_counts[trigger.get("severity", "LOW")] += 1
        
        summary_lines = []
        
        # Critical/High alerts first
        if severity_counts.get("CRITICAL", 0) > 0:
            summary_lines.append(f"🚨 {severity_counts['CRITICAL']} CRITICAL trigger(s) detected")
        if severity_counts.get("HIGH", 0) > 0:
            summary_lines.append(f"⚠️ {severity_counts['HIGH']} HIGH severity trigger(s)")
        
        # Breakdown by type
        for trigger_type, count in sorted(type_counts.items()):
            summary_lines.append(f"  • {count} {trigger_type} trigger(s)")
        
        return " | ".join(summary_lines) if summary_lines else "Monitor your portfolio."
    
    def _empty_trigger_report(self) -> Dict[str, Any]:
        """Return empty trigger report structure."""
        return {
            "active_triggers": 0,
            "triggers": [],
            "summary": "No positions to monitor.",
            "status": "empty"
        }
    
    def pre_trade_validation(self, portfolio: Dict[str, Any], proposed_trade: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate if proposed trade would breach risk thresholds.
        
        Args:
            portfolio: Current portfolio state
            proposed_trade: {action: "BUY"|"SELL", ticker: "TSLA", shares: 10}
        
        Returns:
            {
                "approved": bool,
                "warnings": [...],
                "would_breach": [...]
            }
        """
        action = proposed_trade.get("action", "BUY")
        ticker = proposed_trade.get("ticker", "")
        shares = proposed_trade.get("shares", 0)
        price = proposed_trade.get("price", 0)
        
        if action != "BUY" or price == 0 or shares == 0:
            return {"approved": True, "warnings": [], "would_breach": []}
        
        # Simulate position after trade
        positions = portfolio.get("positions", [])
        total_value = portfolio.get("total_value", 0)
        trade_value = shares * price
        new_total = total_value + trade_value
        
        breaches = []
        
        for pos in positions:
            if pos.get("ticker") == ticker:
                # Position already exists
                new_pos_value = pos.get("value", 0) + trade_value
                new_pct = new_pos_value / new_total if new_total > 0 else 0
                
                if new_pct > self.concentration_threshold:
                    breaches.append({
                        "type": "would_breach_concentration",
                        "ticker": ticker,
                        "current_pct": (pos.get("value", 0) / total_value * 100) if total_value > 0 else 0,
                        "post_trade_pct": new_pct * 100,
                        "threshold_pct": self.concentration_threshold * 100,
                        "warning": f"⚠️ Trade would push {ticker} to {new_pct*100:.1f}% (threshold: {self.concentration_threshold*100:.0f}%)"
                    })
        
        return {
            "approved": len(breaches) == 0,
            "warnings": breaches,
            "would_breach": len(breaches) > 0
        }
