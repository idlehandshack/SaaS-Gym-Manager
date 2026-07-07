import logging
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from django.contrib import messages

from .forms import DemoRequestForm
from .services.demo_request import create_demo_request, DuplicateDemoRequestError

logger = logging.getLogger("demoRequest")


@require_http_methods(["GET", "POST"])
def request_demo_view(request):
    if request.method == "POST":
        form = DemoRequestForm(request.POST)
        if form.is_valid():
            try:
                create_demo_request(form, request)
                return redirect("demoRequest:success")
            except DuplicateDemoRequestError as exc:
                messages.warning(request, str(exc))
            except Exception:
                logger.exception("Unexpected error while creating demo request")
                messages.error(
                    request,
                    "Something went wrong on our end. Please try again in a moment.",
                )
        else:
            logger.info("Demo request form validation failed: %s", form.errors.as_json())
    else:
        form = DemoRequestForm(initial={"source": "website"})

    return render(request, "demoRequest/request_demo.html", {"form": form})


def demo_request_success_view(request):
    return render(request, "demoRequest/success.html")