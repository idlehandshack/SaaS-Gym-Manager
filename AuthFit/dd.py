# AuthFit/views.py

import secrets
import os
import json
import functools
from datetime import date, timedelta

from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_log, logout
from django.contrib.auth.models import User
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.core.cache import cache
from django.conf import settings
from django.utils.http import url_has_allowed_host_and_scheme
from django.core.paginator import Paginator
from django.db import transaction
import cloudinary.uploader
from cloudinary.utils import cloudinary_url
from PIL import Image
import io
import logging
from urllib.parse import urlencode
from Gym.models import Gym                          
from AuthFit.models import (
    Contact, Enrollment, MembershipPlan, Trainer,
    Attendence as Attendence_model, GymNotification
)
from AuthFit.rate_limit import check_login_attempt, reset_attempt, record_failed_attempt ,get_client_ip
from .attendance import mark_attendance
from .forms import UserLogin
from urllib.parse import quote
from Shop.notifications import notify_staff_new_enrollment
from django.contrib.auth.hashers import check_password
logger = logging.getLogger(__name__)

ALLOWED_IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/webp'}
ALLOWED_EXTENSIONS  = {'.jpg', '.jpeg', '.png', '.webp'}
INTERNAL_API_KEY    = os.environ.get("INTERNAL_API_KEY", "")