"""
Abstract base class for screenshot storage backends.

Defines the interface that all storage implementations must follow.
This allows swapping between local filesystem, S3, MinIO, R2, etc.
"""

from abc import ABC, abstractmethod


class ScreenshotStorage(ABC):
    """Abstract interface for screenshot storage backends"""
    
    @abstractmethod
    async def save(
        self, 
        evaluation_id: str, 
        task_run_id: str, 
        step: int,
        screenshot_base64: str
    ) -> str:
        """
        Save screenshot and return URL/path for retrieval.
        
        The implementation should:
        1. Convert PNG base64 to WebP (lossless compression)
        2. Save to storage backend
        3. Return a URL or path that can be used to retrieve it later
        
        Args:
            evaluation_id: UUID of the evaluation
            task_run_id: UUID of the task run
            step: Step number (for ordering)
            screenshot_base64: Base64-encoded PNG screenshot
            
        Returns:
            str: URL or path to retrieve the screenshot
        """
        pass
    
    @abstractmethod
    async def get(self, url_or_path: str) -> str:
        """
        Retrieve screenshot as base64 PNG.
        
        Used for viewing/replaying evaluations. Converts stored WebP
        back to PNG base64 for display in web browsers.
        
        Args:
            url_or_path: The URL/path returned from save()
            
        Returns:
            str: Base64-encoded PNG screenshot
        """
        pass
    
    @abstractmethod
    async def delete_evaluation(self, evaluation_id: str) -> None:
        """
        Delete all screenshots for an evaluation.
        
        Used for cleanup when evaluations are deleted.
        
        Args:
            evaluation_id: UUID of the evaluation to delete
        """
        pass
    
    @abstractmethod
    async def get_storage_info(self) -> dict:
        """
        Get information about storage backend.
        
        Returns:
            dict: Storage backend info (type, location, etc.)
        """
        pass