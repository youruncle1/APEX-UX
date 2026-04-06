"""
Supports AWS S3, MinIO, Cloudflare R2, and any S3-compatible service.
"""

import base64
from io import BytesIO
from typing import Optional
from PIL import Image
from .base import ScreenshotStorage

try:
    import aioboto3
except ImportError:
    aioboto3 = None


class S3Storage(ScreenshotStorage):
    """S3-compatible storage for screenshots"""
    
    def __init__(
        self,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket_name: str,
        region: str = "eu-central-1",
        public_url_base: Optional[str] = None
    ):
        """
        Initialize S3 storage.
        
        Args:
            endpoint_url: S3 endpoint (e.g., "http://localhost:9000" for MinIO)
            access_key: S3 access key
            secret_key: S3 secret key
            bucket_name: S3 bucket name
            region: AWS region (default: eu-central-1 for Europe)
            public_url_base: Public URL base for generating URLs (optional)
        """
        
        if aioboto3 is None:
            raise ImportError(
                "aioboto3 is required for S3 storage. "
                "Install it with: pip install aioboto3"
            )
        
        self.endpoint_url = endpoint_url
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket_name = bucket_name
        self.region = region
        self.public_url_base = public_url_base
        self.session = aioboto3.Session()
    
    async def save(
        self, 
        evaluation_id: str, 
        task_run_id: str, 
        step: int,
        screenshot_base64: str
    ) -> str:
        """
        Save screenshot to S3 as WebP.
        
        Object key structure:
        screenshots/{evaluation_id}/{task_run_id}/step_001.webp
        """
        
        # Decode PNG base64
        image_bytes = base64.b64decode(screenshot_base64)
        image = Image.open(BytesIO(image_bytes))
        
        # Convert to WebP (lossless)
        webp_buffer = BytesIO()
        image.save(webp_buffer, format="WEBP", lossless=True, quality=95)
        webp_buffer.seek(0)
        
        # S3 object key
        key = f"screenshots/{evaluation_id}/{task_run_id}/step_{step:03d}.webp"
        
        # Upload to S3
        async with self.session.client(
            's3',
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region
        ) as s3:
            await s3.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=webp_buffer.getvalue(),
                ContentType='image/webp'
            )
        
        # Return URL for retrieval
        if self.public_url_base:
            return f"{self.public_url_base}/{key}"
        else:
            return f"s3://{self.bucket_name}/{key}"
    
    async def get(self, url_or_path: str) -> str:
        """
        Retrieve screenshot from S3 as base64 PNG.
        
        Args:
            url_or_path: S3 URL like "s3://bucket/screenshots/..." or public URL
            
        Returns:
            Base64-encoded PNG screenshot
        """
        
        # Parse S3 key
        if url_or_path.startswith("s3://"):
            key = url_or_path.replace(f"s3://{self.bucket_name}/", "")
        elif self.public_url_base and url_or_path.startswith(self.public_url_base):
            key = url_or_path.replace(f"{self.public_url_base}/", "")
        else:
            key = url_or_path
        
        # Download from S3
        async with self.session.client(
            's3',
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region
        ) as s3:
            response = await s3.get_object(Bucket=self.bucket_name, Key=key)
            webp_bytes = await response['Body'].read()
        
        # Convert WebP to PNG base64
        image = Image.open(BytesIO(webp_bytes))
        png_buffer = BytesIO()
        image.save(png_buffer, format="PNG")
        png_buffer.seek(0)
        
        return base64.b64encode(png_buffer.getvalue()).decode('utf-8')
    
    async def delete_evaluation(self, evaluation_id: str) -> None:
        """
        Delete all screenshots for an evaluation from S3.
        
        Deletes all objects with prefix: screenshots/{evaluation_id}/
        """
        
        prefix = f"screenshots/{evaluation_id}/"
        
        async with self.session.client(
            's3',
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region
        ) as s3:
            # List all objects with prefix
            paginator = s3.get_paginator('list_objects_v2')
            async for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
                if 'Contents' in page:
                    objects = [{'Key': obj['Key']} for obj in page['Contents']]
                    if objects:
                        await s3.delete_objects(
                            Bucket=self.bucket_name,
                            Delete={'Objects': objects}
                        )
    
