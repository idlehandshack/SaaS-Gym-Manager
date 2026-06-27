"""
billing/services/cloudflare_storage.py
-----------------------------------------
Uploads files (PDFs, xlsx reports) to Cloudflare R2.
R2 is S3-compatible, so we use boto3's S3 client pointed at R2's endpoint.

Images (profile pics, signatures) STAY on Cloudinary — only PDFs/reports
move to R2. This file is only for those.

Required env vars (see setup guide):
    CLOUDFLARE_R2_ACCOUNT_ID
    CLOUDFLARE_R2_ACCESS_KEY_ID
    CLOUDFLARE_R2_SECRET_ACCESS_KEY
    CLOUDFLARE_R2_BUCKET_NAME
    CLOUDFLARE_R2_PUBLIC_URL      (e.g. https://pub-xxxx.r2.dev)
"""
import logging
import os

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger('billing')


def _get_r2_client():
    account_id = os.environ.get('CLOUDFLARE_R2_ACCOUNT_ID')
    access_key = os.environ.get('CLOUDFLARE_R2_ACCESS_KEY_ID')
    secret_key = os.environ.get('CLOUDFLARE_R2_SECRET_ACCESS_KEY')

    if not all([account_id, access_key, secret_key]):
        raise RuntimeError(
            "Missing Cloudflare R2 credentials. Check CLOUDFLARE_R2_ACCOUNT_ID, "
            "CLOUDFLARE_R2_ACCESS_KEY_ID, CLOUDFLARE_R2_SECRET_ACCESS_KEY env vars."
        )

    endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"

    return boto3.client(
        's3',
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version='s3v4'),
        region_name='auto',
    )


def upload_file_to_r2(local_path: str, key: str, content_type: str) -> str:
    """
    Uploads a local file to R2 at the given key (path inside bucket).
    Returns the public URL.

    Example:
        upload_file_to_r2(
            '/tmp/invoice.pdf',
            'invoices/golden-gym/2026-27/GG-2026-27-0001.pdf',
            'application/pdf',
        )
        -> 'https://pub-xxxx.r2.dev/invoices/golden-gym/2026-27/GG-2026-27-0001.pdf'
    """
    bucket_name = os.environ.get('CLOUDFLARE_R2_BUCKET_NAME')
    public_url_base = os.environ.get('CLOUDFLARE_R2_PUBLIC_URL', '').rstrip('/')

    if not bucket_name:
        raise RuntimeError("Missing CLOUDFLARE_R2_BUCKET_NAME env var.")
    if not public_url_base:
        raise RuntimeError("Missing CLOUDFLARE_R2_PUBLIC_URL env var.")

    client = _get_r2_client()

    try:
        client.upload_file(
            Filename=local_path,
            Bucket=bucket_name,
            Key=key,
            ExtraArgs={
                'ContentType': content_type,
                # Forces browser to display inline (PDF preview) instead of
                # forcing a download — change to 'attachment' if you'd rather
                # always force-download.
                'ContentDisposition': 'inline',
            },
        )
    except (BotoCoreError, ClientError) as exc:
        logger.exception("R2 upload failed for key=%s", key)
        raise RuntimeError(f"Failed to upload to Cloudflare R2: {exc}") from exc

    url = f"{public_url_base}/{key}"
    logger.info("Uploaded to R2: %s", url)
    return url


def delete_file_from_r2(key: str) -> None:
    """Optional helper — deletes a file from R2 (e.g. when regenerating a PDF)."""
    bucket_name = os.environ.get('CLOUDFLARE_R2_BUCKET_NAME')
    client = _get_r2_client()
    try:
        client.delete_object(Bucket=bucket_name, Key=key)
    except (BotoCoreError, ClientError):
        logger.exception("R2 delete failed for key=%s", key)