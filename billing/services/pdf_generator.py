import logging
import os
import tempfile

from django.template.loader import render_to_string

from billing.models import Invoice
from billing.services.cloudflare_storage import upload_file_to_r2

logger = logging.getLogger('billing')


def generate_invoice_pdf(invoice: Invoice) -> str:
    """
    Renders invoice to PDF and uploads to Cloudflare R2.
    Returns the public R2 URL (also saves it to invoice.pdf_url).
    Raises on failure so the caller can log / surface the error.
    """
    try:
        from xhtml2pdf import pisa
    except ImportError:
        logger.error("xhtml2pdf is not installed. Run: pip install xhtml2pdf")
        raise

    context = {
        'invoice':     invoice,
        'line_items':  invoice.line_items.all(),
        'gym':         invoice.gym,
        'gst_profile': getattr(invoice.gym, 'gst_profile', None),
    }

    html_string = render_to_string('billing/invoice_pdf.html', context)

    tmp_path = None
    try:
        # ── Step 1: render to temp file, then CLOSE it fully ──────────────
        # On Windows, file readers can choke if the handle is still open.
        # mkstemp gives us a low-level fd; wrap in fdopen so pisa can write,
        # then let the `with` block close it before we do anything else.
        tmp_fd, tmp_path = tempfile.mkstemp(suffix='.pdf')
        with os.fdopen(tmp_fd, 'wb') as tmp_file:
            pisa_result = pisa.CreatePDF(
                src=html_string.encode('utf-8'),
                dest=tmp_file,
                encoding='utf-8',
            )
        # File is fully closed here — safe to read from disk now.

        if pisa_result.err:
            raise RuntimeError(
                f"xhtml2pdf failed for invoice {invoice.invoice_number} "
                f"(error code {pisa_result.err}). "
                "Check the invoice HTML template for unsupported CSS."
            )

        # Quick sanity check — a valid PDF always starts with %PDF-
        with open(tmp_path, 'rb') as f:
            header = f.read(5)
        if header != b'%PDF-':
            raise RuntimeError(
                f"Generated file is not a valid PDF (header: {header!r}). "
                "xhtml2pdf may have failed silently."
            )

        file_size = os.path.getsize(tmp_path)
        logger.info(
            "PDF rendered: %s bytes for invoice %s",
            file_size, invoice.invoice_number,
        )

        # ── Step 2: upload the fully-written file to Cloudflare R2 ────────
        safe_number = invoice.invoice_number.replace('/', '_')   # INV_2026-27_0001
        key = f"invoices/{invoice.gym.gym_code}/{invoice.financial_year}/{safe_number}.pdf"

        pdf_url = upload_file_to_r2(tmp_path, key, content_type='application/pdf')

    finally:
        # Always clean up — even if an exception was raised above
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    # Persist URL on the invoice row
    Invoice.objects.filter(pk=invoice.pk).update(pdf_url=pdf_url)
    invoice.pdf_url = pdf_url

    logger.info("PDF uploaded for invoice %s -> %s", invoice.invoice_number, pdf_url)
    return pdf_url