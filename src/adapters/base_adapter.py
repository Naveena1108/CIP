from abc import ABC, abstractmethod
from typing import Any, List
from src.contracts.base import CanonicalSignalBase

class BaseSignalAdapter(ABC):
    """
    Abstract Base Adapter.
    Translates raw heterogeneous data (CSV, JSON, Excel, DB records)
    into validated Canonical Signal Contracts.
    """
    @abstractmethod
    def parse(self, raw_data: Any) -> List[CanonicalSignalBase]:
        """Convert raw input into strongly-typed canonical contract list."""
        pass
