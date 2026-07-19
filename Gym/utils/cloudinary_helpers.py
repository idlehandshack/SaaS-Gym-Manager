# ============================================================
# NEW FILE: Gym/utils/cloudinary_helpers.py
# (create the Gym/utils/ package if it doesn't already exist —
# you already have Gym/utils/search.py, so the package exists)
# ============================================================
import logging
from cloudinary.utils import cloudinary_url

logger = logging.getLogger(__name__)


def cloudinary_thumb(field, width=200, height=200, crop="fill", gravity=None,effect=None):
    if not field:
        return ''

    try:
        public_id = field.public_id if hasattr(field, 'public_id') else str(field)
        if not public_id:
            return ''

        kwargs = dict(
            width=width, height=height, crop=crop,
            fetch_format="auto", quality="auto", secure=True,
        )
        if gravity:
            kwargs["gravity"] = gravity

        url, _ = cloudinary_url(public_id, **kwargs)
        return url
    except Exception:
        logger.exception("Cloudinary URL build failed for field=%r", field)
        return ''