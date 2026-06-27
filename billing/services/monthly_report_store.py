"""
billing/services/monthly_report_store.py
------------------------------------------
Handles R2 key naming, listing, and generating monthly revenue reports
for the auto-report system.

R2 folder structure:
    reports/{gym_code}/{YYYY}/{YYYY_MM}_revenue.xlsx

Public API:
    generate_and_store_monthly_report(gym, year, month) -> str   (R2 URL)
    list_stored_reports(gym)                             -> list[dict]
    get_report_url(gym, year, month)                     -> str | None
"""
import logging
import os
import tempfile
from datetime import date

from billing.services.cloudflare_storage import _get_r2_client, upload_file_to_r2
from billing.services.monthly_report import generate_monthly_report_excel
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger('billing')


# ── Key helpers ────────────────────────────────────────────────────────────────

def _report_key(gym, year: int, month: int) -> str:
    """
    R2 key for a gym's monthly report.
    e.g.  reports/fitzone/2026/2026_06_revenue.xlsx
    """
    return f"reports/{gym.gym_code}/{year}/{year}_{month:02d}_revenue.xlsx"


def _public_url(key: str) -> str:
    base = os.environ.get('CLOUDFLARE_R2_PUBLIC_URL', '').rstrip('/')
    return f"{base}/{key}"


# ── Generate + upload ──────────────────────────────────────────────────────────

def generate_and_store_monthly_report(gym, year: int, month: int) -> str:
    """
    Generates the monthly Excel report for gym/year/month,
    uploads it to R2, and returns the public URL.
    Overwrites any existing file at that key (idempotent — safe to re-run).
    """
    buf = generate_monthly_report_excel(gym, year, month)

    tmp_path = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(suffix='.xlsx')
        with os.fdopen(tmp_fd, 'wb') as f:
            f.write(buf.read())

        key = _report_key(gym, year, month)
        url = upload_file_to_r2(
            tmp_path,
            key,
            content_type=(
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            ),
        )
        logger.info(
            "Monthly report stored: gym=%s %d-%02d -> %s",
            gym.gym_code, year, month, url,
        )
        return url

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


# ── List stored reports ────────────────────────────────────────────────────────

def list_stored_reports(gym) -> list[dict]:
    """
    Lists all monthly report files stored in R2 for this gym.
    Returns a list of dicts, newest first:
        [
          {
            'year': 2026, 'month': 6,
            'month_label': 'June 2026',
            'key': 'reports/fitzone/2026/2026_06_revenue.xlsx',
            'url': 'https://pub-xxx.r2.dev/reports/...',
            'size_kb': 42,
            'last_modified': datetime(...),
          },
          ...
        ]
    Returns [] if no reports exist or R2 is unreachable.
    """
    bucket = os.environ.get('CLOUDFLARE_R2_BUCKET_NAME', '')
    prefix = f"reports/{gym.gym_code}/"

    try:
        client = _get_r2_client()
        paginator = client.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=bucket, Prefix=prefix)

        results = []
        for page in pages:
            for obj in page.get('Contents', []):
                key = obj['Key']
                # key format: reports/{gym_code}/{year}/{year}_{month:02d}_revenue.xlsx
                filename = key.split('/')[-1]           # "2026_06_revenue.xlsx"
                if not filename.endswith('_revenue.xlsx'):
                    continue
                try:
                    parts = filename.replace('_revenue.xlsx', '').split('_')
                    year  = int(parts[0])
                    month = int(parts[1])
                except (IndexError, ValueError):
                    continue

                month_label = date(year, month, 1).strftime('%B %Y')
                results.append({
                    'year':          year,
                    'month':         month,
                    'month_label':   month_label,
                    'key':           key,
                    'url':           _public_url(key),
                    'size_kb':       round(obj['Size'] / 1024, 1),
                    'last_modified': obj['LastModified'],
                })

        results.sort(key=lambda r: (r['year'], r['month']), reverse=True)
        return results

    except (BotoCoreError, ClientError, RuntimeError):
        logger.exception("Failed to list reports from R2 for gym=%s", gym.gym_code)
        return []


# ── Check if a specific report exists ─────────────────────────────────────────

def get_report_url(gym, year: int, month: int) -> str | None:
    """
    Returns the public URL if a report already exists in R2 for this
    gym/year/month, or None if it hasn't been generated yet.
    Useful for checking before triggering a re-generation.
    """
    bucket = os.environ.get('CLOUDFLARE_R2_BUCKET_NAME', '')
    key    = _report_key(gym, year, month)

    try:
        client = _get_r2_client()
        client.head_object(Bucket=bucket, Key=key)
        return _public_url(key)
    except ClientError as e:
        if e.response['Error']['Code'] in ('404', 'NoSuchKey'):
            return None
        logger.exception("R2 head_object failed for key=%s", key)
        return None
    except (BotoCoreError, RuntimeError):
        logger.exception("R2 connection failed checking key=%s", key)
        return None