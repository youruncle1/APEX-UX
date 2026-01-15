import os
from .base import ScreenshotStorage
from .local import LocalStorage
from .s3 import S3Storage


def create_screenshot_storage() -> ScreenshotStorage:
    """
    Create screenshot storage backend based on environment configuration.
    """
    
    storage_type = os.getenv("SCREENSHOT_STORAGE_TYPE", "local").lower()
    
    if storage_type == "local":
        base_path = os.getenv("LOCAL_STORAGE_PATH", "storage/screenshots")
        print(f"[Storage] Using local filesystem: {base_path}")
        return LocalStorage(base_path=base_path)
    
    elif storage_type == "s3":
        endpoint_url = os.getenv("S3_ENDPOINT_URL")
        access_key = os.getenv("S3_ACCESS_KEY")
        secret_key = os.getenv("S3_SECRET_KEY")
        bucket_name = os.getenv("S3_BUCKET_NAME")
        
        if not all([endpoint_url, access_key, secret_key, bucket_name]):
            raise ValueError(
                "S3 storage requires: S3_ENDPOINT_URL, S3_ACCESS_KEY, "
                "S3_SECRET_KEY, S3_BUCKET_NAME"
            )
        
        region = os.getenv("S3_REGION", "eu-central-1")
        public_url_base = os.getenv("S3_PUBLIC_URL_BASE")
        
        print(f"[Storage] Using S3: {endpoint_url}/{bucket_name} (region: {region})")
        
        return S3Storage(
            endpoint_url=endpoint_url,
            access_key=access_key,
            secret_key=secret_key,
            bucket_name=bucket_name,
            region=region,
            public_url_base=public_url_base
        )
    
    else:
        raise ValueError(
            f"Unknown storage type: {storage_type}. "
            f"Valid options: 'local', 's3'"
        )