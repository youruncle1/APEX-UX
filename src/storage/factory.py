from src.config import get_settings, require_s3_config
from .base import ScreenshotStorage
from .local import LocalStorage
from .s3 import S3Storage


def create_screenshot_storage() -> ScreenshotStorage:
    """Create screenshot storage backend based on application settings."""
    settings = get_settings()
    storage_type = settings.screenshot_storage_type.lower()

    if storage_type == "local":
        print(f"[Storage] Using local filesystem: {settings.local_storage_path}")
        return LocalStorage(base_path=settings.local_storage_path)

    if storage_type == "s3":
        cfg = require_s3_config(settings)
        print(
            f"[Storage] Using S3: {cfg['endpoint_url']}/{cfg['bucket_name']} "
            f"(region: {cfg['region']})"
        )
        return S3Storage(**cfg)

    raise ValueError(
        f"Unknown storage type: {storage_type!r}. Valid options: 'local', 's3'."
    )