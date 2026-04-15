from abc import ABC, abstractmethod


class ScreenshotStorage(ABC):
    
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
        """
        pass
    
    @abstractmethod
    async def get(self, url_or_path: str) -> str:
        """
        Retrieve screenshot as base64 PNG.
        """
        pass
    
    @abstractmethod
    async def delete_task_run(self, evaluation_id: str, task_run_id: str) -> None:
        """
        Delete all screenshots for a single task run.
        """
        pass

    @abstractmethod
    async def delete_evaluation(self, evaluation_id: str) -> None:
        """
        Delete all screenshots for an evaluation.
        """
        pass
