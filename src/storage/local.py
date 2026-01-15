"""
Local filesystem storage implementation.

Stores screenshots in ./storage/screenshots relative to project directory.
Screenshots are converted to WebP format for space efficiency.
"""

import base64
import os
from pathlib import Path
from io import BytesIO
from PIL import Image
from .base import ScreenshotStorage


class LocalStorage(ScreenshotStorage):
    """Local filesystem storage for screenshots"""
    
    def __init__(self, base_path: str = "storage/screenshots"):
        """
        Initialize local storage.
        
        Args:
            base_path: Base directory for screenshots (default: storage/screenshots, relative to project)
        """
        # If path is relative, make it relative to project root
        if not os.path.isabs(base_path):
            # Get project root (assuming this file is in src/storage/)
            project_root = Path(__file__).parent.parent.parent
            self.base_path = project_root / base_path
        else:
            self.base_path = Path(base_path)
        
        self.base_path.mkdir(parents=True, exist_ok=True)
    
    async def save(
        self, 
        evaluation_id: str, 
        task_run_id: str, 
        step: int,
        screenshot_base64: str
    ) -> str:
        """
        Save screenshot to local filesystem as WebP.
        
        Directory structure:
        /storage/screenshots/
            {evaluation_id}/
                {task_run_id}/
                    step_001.webp
                    step_002.webp
                    ...
        """
        
        # Create directory structure
        eval_dir = self.base_path / evaluation_id
        task_dir = eval_dir / task_run_id
        task_dir.mkdir(parents=True, exist_ok=True)
        
        # Decode PNG base64
        image_bytes = base64.b64decode(screenshot_base64)
        image = Image.open(BytesIO(image_bytes))
        
        # Save as WebP (lossless)
        filename = f"step_{step:03d}.webp"
        filepath = task_dir / filename
        
        image.save(
            filepath, 
            format="WEBP", 
            lossless=True, 
            quality=95
        )
        
        # Return relative path from base_path
        relative_path = filepath.relative_to(self.base_path)
        return f"local://{relative_path}"
    
    async def get(self, url_or_path: str) -> str:
        """
        Retrieve screenshot as base64 PNG.
        
        Args:
            url_or_path: Path like "local://evaluation_id/task_run_id/step_001.webp"
            
        Returns:
            Base64-encoded PNG screenshot
        """
        
        # Parse path
        if url_or_path.startswith("local://"):
            relative_path = url_or_path.replace("local://", "")
        else:
            relative_path = url_or_path
        
        filepath = self.base_path / relative_path
        
        if not filepath.exists():
            raise FileNotFoundError(f"Screenshot not found: {filepath}")
        
        # Load WebP
        image = Image.open(filepath)
        
        # Convert to PNG base64
        png_buffer = BytesIO()
        image.save(png_buffer, format="PNG")
        png_buffer.seek(0)
        
        return base64.b64encode(png_buffer.getvalue()).decode('utf-8')
    
    async def delete_evaluation(self, evaluation_id: str) -> None:
        """
        Delete all screenshots for an evaluation.
        
        Removes the entire evaluation directory.
        """
        
        eval_dir = self.base_path / evaluation_id
        
        if eval_dir.exists():
            import shutil
            shutil.rmtree(eval_dir)
    
    async def get_storage_info(self) -> dict:
        """Get storage backend information"""
        
        # Calculate total size
        total_size = sum(
            f.stat().st_size 
            for f in self.base_path.rglob('*.webp')
        )
        
        # Count screenshots
        screenshot_count = len(list(self.base_path.rglob('*.webp')))
        
        return {
            "type": "local",
            "base_path": str(self.base_path),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "screenshot_count": screenshot_count
        }