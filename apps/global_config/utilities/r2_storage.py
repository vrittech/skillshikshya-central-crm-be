import uuid

from apps.global_config.integrations import make_r2_client, r2_config


def get_r2_client(cfg=None):
    """R2 client for the account in use (Settings page, else .env)."""
    return make_r2_client(cfg or r2_config())


def r2_bucket():
    return r2_config().bucket_name


def upload_file_to_r2(file_obj, folder='uploads'):
    ext = file_obj.name.rsplit('.', 1)[-1].lower() if '.' in file_obj.name else ''
    filename = f'{uuid.uuid4().hex}.{ext}' if ext else uuid.uuid4().hex
    key = f'{folder}/{filename}'

    cfg = r2_config()
    client = get_r2_client(cfg)
    client.put_object(
        Bucket=cfg.bucket_name,
        Key=key,
        Body=file_obj.read(),
        ContentType=file_obj.content_type or 'application/octet-stream',
    )

    if cfg.public_url:
        url = f'{cfg.public_url.rstrip("/")}/{key}'
    else:
        url = f'/api/files/{key}'

    return {'key': key, 'url': url, 'filename': file_obj.name, 'size': file_obj.size}
