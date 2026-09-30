"""Re-export of src.integrations for backend/integrations/* path compatibility."""
from src.integrations.world_intel import WorldIntelMCPAdapter, WorldIntelConfig

__all__ = ["WorldIntelMCPAdapter", "WorldIntelConfig"]
