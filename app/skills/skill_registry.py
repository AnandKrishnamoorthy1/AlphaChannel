"""Skill Registry - Loads and manages reusable AI agent skills."""

import os
import yaml
import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


class SkillRegistry:
    """
    Central registry for agent skills.
    
    Skills are modular, reusable procedural artifacts that bundle task-specific
    execution instructions, workflows, and tools together.
    
    This registry enables progressive disclosure: the agent sees lightweight
    metadata on initialization, loading full procedures only when needed.
    """
    
    def __init__(self, skills_dir: str = "./skills"):
        self.skills_dir = skills_dir
        self.skills_metadata = []
        self.skills_loaded = False
    
    def load_skill_manifests(self) -> List[Dict[str, Any]]:
        """
        Load lightweight YAML metadata from all skill manifests.
        
        This is called once during system initialization.
        Returns list of skill metadata that gets added to system prompt.
        """
        if self.skills_loaded:
            return self.skills_metadata
        
        try:
            if not os.path.exists(self.skills_dir):
                logger.warning(f"Skills directory not found: {self.skills_dir}")
                return []
            
            for skill_folder in os.listdir(self.skills_dir):
                skill_path = os.path.join(self.skills_dir, skill_folder)
                
                # Skip if not a directory
                if not os.path.isdir(skill_path):
                    continue
                
                manifest_path = os.path.join(skill_path, "SKILL.md")
                
                if os.path.exists(manifest_path):
                    try:
                        with open(manifest_path, "r", encoding="utf-8") as f:
                            content = f.read()
                        
                        # Extract YAML frontmatter
                        if content.startswith("---"):
                            parts = content.split("---", 2)
                            if len(parts) >= 2:
                                meta = yaml.safe_load(parts[1])
                                
                                if meta and isinstance(meta, dict):
                                    skill_info = {
                                        "name": meta.get("name", skill_folder),
                                        "description": meta.get("description", ""),
                                        "domains": meta.get("domains", []),
                                        "path": skill_path,
                                        "manifest_path": manifest_path
                                    }
                                    
                                    self.skills_metadata.append(skill_info)
                                    logger.info(f"Loaded skill: {skill_info['name']}")
                    
                    except Exception as e:
                        logger.error(f"Failed to parse skill manifest at {manifest_path}: {e}")
                        continue
            
            self.skills_loaded = True
            logger.info(f"Loaded {len(self.skills_metadata)} skills")
            return self.skills_metadata
        
        except Exception as e:
            logger.error(f"Error loading skill manifests: {e}")
            return []
    
    def get_skill_by_name(self, skill_name: str) -> Dict[str, Any]:
        """Retrieve a specific skill by name."""
        for skill in self.skills_metadata:
            if skill["name"] == skill_name:
                return skill
        return {}
    
    def get_skills_for_domain(self, domain: str) -> List[Dict[str, Any]]:
        """Retrieve all skills associated with a domain."""
        matching = [
            skill for skill in self.skills_metadata
            if domain in skill.get("domains", [])
        ]
        return matching
    
    def get_system_prompt_context(self) -> str:
        """
        Generate system prompt section describing available skills.
        
        This is embedded in TRIAGE_PROMPT and GENERAL_QA_PROMPT
        to make Qwen aware of available skills.
        """
        if not self.skills_metadata:
            return ""
        
        skills_text = "## Available Skills\n\n"
        skills_text += "The following reusable skills are available:\n\n"
        
        for skill in self.skills_metadata:
            skills_text += f"- **{skill['name']}**: {skill['description']}\n"
            if skill.get("domains"):
                skills_text += f"  Domains: {', '.join(skill['domains'])}\n"
        
        skills_text += "\n"
        return skills_text
    
    def list_all_skills(self) -> List[str]:
        """Return list of available skill names."""
        return [skill["name"] for skill in self.skills_metadata]
    
    def reload_manifests(self):
        """Force reload of skill manifests."""
        self.skills_metadata = []
        self.skills_loaded = False
        self.load_skill_manifests()


# Global skill registry instance
_skill_registry = None


def get_skill_registry(skills_dir: str = "./skills") -> SkillRegistry:
    """Get or create the global skill registry."""
    global _skill_registry
    if _skill_registry is None:
        _skill_registry = SkillRegistry(skills_dir)
        _skill_registry.load_skill_manifests()
    return _skill_registry


def invoke_skill(skill_name: str, **kwargs) -> Dict[str, Any]:
    """
    Invoke a skill by name with given arguments.
    
    Example:
        result = invoke_skill("portfolio_analyzer", portfolio=portfolio_data)
        result = invoke_skill("risk_manager", portfolio=portfolio_data, risk_tolerance="moderate")
        result = invoke_skill("stop_loss_take_profit", portfolio=portfolio_data)
    """
    registry = get_skill_registry()
    
    if skill_name == "portfolio_analyzer":
        from skills.portfolio_analyzer.analysis_tools import PortfolioAnalyzer
        analyzer = PortfolioAnalyzer()
        return analyzer.analyze(kwargs.get("portfolio", {}))
    
    elif skill_name == "risk_manager":
        from skills.risk_manager.risk_tools import RiskManager
        risk_mgr = RiskManager()
        return risk_mgr.assess(
            kwargs.get("portfolio", {}),
            risk_tolerance=kwargs.get("risk_tolerance", "moderate")
        )
    
    elif skill_name == "stop_loss_take_profit":
        from skills.stop_loss_take_profit.trigger_tools import StopLossTakeProfitEngine
        engine = StopLossTakeProfitEngine(
            concentration_threshold=kwargs.get("concentration_threshold", 0.50)
        )
        
        # Check if this is pre-trade validation or regular assessment
        if "proposed_trade" in kwargs:
            return engine.pre_trade_validation(
                kwargs.get("portfolio", {}),
                kwargs.get("proposed_trade", {})
            )
        else:
            return engine.assess_all_triggers(kwargs.get("portfolio", {}))
    
    else:
        logger.warning(f"Unknown skill: {skill_name}")
        return {"status": "error", "detail": f"Unknown skill: {skill_name}"}
