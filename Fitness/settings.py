import os
from pathlib import Path
import dj_database_url
import cloudinary
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "")
SECRET_KEY = os.environ['SECRET_KEY']
DEBUG = os.environ.get('DEBUG', 'False') == 'True'
API_KEY = os.environ.get("INTERNAL_API_KEY", "")

# ── ALLOWED_HOSTS ─────────────────────────────────────────────────────────
# Multi-tenant: each gym gets a subdomain like gym1.saas-gym-manager.onrender.com
# Never use ["*"] — even in DEBUG it masks misconfiguration
if DEBUG:
    ALLOWED_HOSTS = [
        '.localhost', '127.0.0.1', '0.0.0.0', 'localhost', '*',"bilabiate-overdevoted-juan.ngrok-free.dev",
    ]
else:
    ALLOWED_HOSTS = [
        'entergym.in',
        'www.entergym.in',
        '.entergym.in',   # wildcard covers all gym subdomains
    ]

# ── CSRF ──────────────────────────────────────────────────────────────────
# Must cover every gym subdomain or members will get 403 on form POSTs
CSRF_TRUSTED_ORIGINS = [
    "https://entergym.in",
    "https://*.entergym.in",

    # ngrok
    "https://bilabiate-overdevoted-juan.ngrok-free.dev",

    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

INSTALLED_APPS = [
    'daphne',            # must be first if you use it for runserver
    'channels',
    'jazzmin',

    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party
    'rest_framework',
    'cloudinary',
    'cloudinary_storage',
    'rest_framework.authtoken',

    # Your apps
    'AuthFit',
    'Shop',
    'notifications',
    'Gym',
    'billing',
    'reviews',
    'demoRequest',
    'announcements',
    'member_messages',
    'expenses',
    "communications",
    'notification_center'
]

JAZZMIN_UI_TWEAKS = {
    "theme": "solar",
}

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'Gym.middleware.GymMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'AuthFit.middleware.SecurityHeadersMiddleware',
]

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.BCryptSHA256PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
]
ASGI_APPLICATION = 'Fitness.asgi.application'
# Reuses the same REDIS_URL you already use for CACHES — one Redis instance,
# two logical uses (cache DB vs channel layer), no new infra.

ROOT_URLCONF = 'Fitness.urls'
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
ENCRYPTION_KEY = os.environ["ENCRYPTION_KEY"]
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v23.0")
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'AuthFit.context_processors.gym_config',
                'AuthFit.context_processors.saas_config',
                'notifications.context_processors.vapid_key',
                'AuthFit.context_processors.gym_context',
                'AuthFit.context_processors.gym_branding',
                'AuthFit.context_processors.gym_theme',
                'AuthFit.context_processors.dashboard_theme',
                'Gym.context_processors.notification_bell',
            ],
        },
    }
]

WSGI_APPLICATION = 'Fitness.wsgi.application'
cloudinary.config(
    cloud_name=os.environ['CLOUDINARY_CLOUD_NAME'],
    api_key=os.environ['CLOUDINARY_API_KEY'],
    api_secret=os.environ['CLOUDINARY_API_SECRET'],
    secure=True,
)

DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'
STATICFILES_STORAGE  = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

if DEBUG:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": dj_database_url.parse(os.environ["DATABASE_URL"])
    }

# DATABASES = {
#     'default': dj_database_url.parse(os.environ['DATABASE_URL'])
# }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE     = 'Asia/Kolkata'
USE_I18N      = True
USE_TZ        = True

STATIC_URL       = '/static/'
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'static')]
STATIC_ROOT      = os.path.join(BASE_DIR, "staticfiles_build")
WHITENOISE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year — safe because WhiteNoise uses content-hashed filenames

REDIS_URL = os.environ['REDIS_URL']

if DEBUG:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                **(
                    {"CONNECTION_POOL_KWARGS": {"ssl_cert_reqs": None}}
                    if REDIS_URL.startswith("rediss://")
                    else {}
                ),
            },
        }
    }

VAPID_PUBLIC_KEY  = os.environ.get("VAPID_PUBLIC_KEY")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY")
VAPID_CLAIMS      = {"sub": f"mailto:{os.environ.get('VAPID_EMAIL')}"}

LOGIN_URL           = '/login/'
LOGIN_REDIRECT_URL  = '/'
LOGOUT_REDIRECT_URL = '/'

# ── Session & Cookie Security ─────────────────────────────────────────────
# cached_db: reads from Redis (fast), falls back to DB if Redis is down.
# Pure cache backend loses all sessions on Redis restart — bad for a SaaS.
if DEBUG:
    SESSION_ENGINE = 'django.contrib.sessions.backends.db'
else:
    SESSION_ENGINE = 'django.contrib.sessions.backends.cached_db'
SESSION_CACHE_ALIAS = 'default'
SESSION_COOKIE_AGE = 60 * 60 * 24 * 90
SESSION_SAVE_EVERY_REQUEST = True
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = False  
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE   = not DEBUG

CSRF_COOKIE_HTTPONLY = False   # JS needs to read it for AJAX
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SECURE   = not DEBUG
WHATSAPP_DEFAULT_COUNTRY_CODE = os.getenv("WHATSAPP_DEFAULT_COUNTRY_CODE", "+91")
# ── File upload limits ────────────────────────────────────────────────────
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # 5 MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── Gym geo-defaults ──────────────────────────────────────────────────────
# NOTE: These are fallback defaults only.
# In a multi-tenant setup each Gym object should store its own
# latitude, longitude, and radius in the database.
# GymMiddleware should read from request.gym, not from these settings.
GYM_LATITUDE_DEFAULT      = float(os.environ.get('GYM_LATITUDE',      21.2179))
GYM_LONGITUDE_DEFAULT     = float(os.environ.get('GYM_LONGITUDE',     81.3311))
GYM_RADIUS_METERS_DEFAULT = float(os.environ.get('GYM_RADIUS_METERS', 100))

FIREBASE_CREDENTIALS_PATH = os.getenv(
    "FIREBASE_CREDENTIALS_PATH",
    "firebase-credentials.json"
)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {'class': 'logging.StreamHandler'},
    },
    'loggers': {
        'shop': {
            'handlers': ['console'],
            'level': 'INFO',
        },
        'django.security': {
            'handlers': ['console'],
            'level': 'WARNING',
        },
        'demoRequest': {
            'handlers': ['console'],
            'level': 'INFO',
        },
    },
}
REST_FRAMEWORK = {
       "DEFAULT_AUTHENTICATION_CLASSES": [
           "rest_framework.authentication.TokenAuthentication",
           "rest_framework.authentication.SessionAuthentication",
       ],
       "DEFAULT_PERMISSION_CLASSES": [
           "rest_framework.permissions.IsAuthenticated",
       ],
   }

if not DEBUG:
    SECURE_HSTS_SECONDS            = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD            = True
    SECURE_SSL_REDIRECT            = True
    SECURE_PROXY_SSL_HEADER        = ('HTTP_X_FORWARDED_PROTO', 'https')

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [
                {
                    "address": REDIS_URL,
                    **(
                        {"ssl_cert_reqs": None}
                        if REDIS_URL.startswith("rediss://")
                        else {}
                    ),
                }
            ],
        },
    },
}

JAZZMIN_SETTINGS = {
    "site_title":   "EnterGYM Admin",
    "site_header":  "EnterGYM Dashboard",
    "site_brand":   "EnterGYM",
    "welcome_sign": "Welcome to EnterGYM Control Panel",
    "site_logo":    "images/Logo.png",
    "site_icon":    "images/Logo.png",
    "copyright":    "EnterGYM",

    "topmenu_links": [
        {"name": "Visit Website", "url": "https://entergym.in/"},
        {"name": "Dashboard", "url": "https://entergym.in/superadmin/dashboard/"},
    ],
    "usermenu_links": [
        {"name": "Visit Website", "url": "https://entergym.in/", "new_window": True},
        {"name": "Support", "url": "https://wa.me/917000032565", "new_window": True},
    ],

    "show_sidebar":        True,
    "navigation_expanded": False,

    # ── Sidebar order: EnterGYM Platform → Authentication → Demo Requests →
    #    Notifications → Store → Members → Billing ─────────────────────────
    "order_with_respect_to": [
        # 1. EnterGYM Platform — Gym model first
        "Gym",
        "Gym.gym",
        "Gym.subscriptionplan",
        "Gym.platformsubscriptionpayment",
        "Gym.platformsettings",
        "Gym.gymgstprofile",
        "Gym.equipmentbrand",
        "Gym.service",
        "Gym.staffprofile",
        "Gym.staffpermission",
        "Gym.orphanuserdeletionlog",
        "Gym.gymwhatsappsettings",
        "Gym.whatsappmessagelog",

        # 2. Authentication
        "auth",
        "auth.user",
        "auth.group",

        # 3. Demo Requests
        "demoRequest",
        "demoRequest.demorequest",

        # 4. Notifications
        "notifications",
        "notifications.webpushsubscription",

        # 5. Store (Shop)
        "Shop",
        "Shop.globalproduct",
        "Shop.gymproduct",
        "Shop.gymproductflavor",
        "Shop.order",
        "Shop.gyminventorymovement",
        "Shop.staffdevice",
        "Shop.globalproductflavor",

        # 6. Members (AuthFit)
        "AuthFit",
        "AuthFit.enrollment",
        "AuthFit.membershipplan",
        "AuthFit.trainer",
        "AuthFit.attendence",
        "AuthFit.gymnotification",
        "AuthFit.userdevice",
        "AuthFit.contact",
        "AuthFit.loginsupportquery",

        # 7. Billing (last)
        "billing",
        "billing.invoice",
        "billing.invoicelineitem",
        "billing.payment",
        "billing.invoicecounter",
    ],

    # ── App-level icons (top of each sidebar group) ────────────────────────
    "icons": {
        "Gym":            "fas fa-building",
        "auth":            "fas fa-users-cog",
        "auth.user":       "fas fa-user",
        "auth.group":      "fas fa-users",
        "demoRequest":     "fas fa-clipboard-list",
        "notifications":   "fas fa-bell",
        "Shop":            "fas fa-store",
        "AuthFit":         "fas fa-dumbbell",
        "billing":         "fas fa-file-invoice-dollar",
        
        # EnterGYM Platform models
        "Gym.gym":                        "fas fa-building",
        "Gym.subscriptionplan":            "fas fa-tags",
        "Gym.platformsubscriptionpayment": "fas fa-hand-holding-usd",
        "Gym.platformsettings":            "fas fa-sliders-h",
        "Gym.gymgstprofile":               "fas fa-file-contract",
        "Gym.equipmentbrand":              "fas fa-industry",
        "Gym.service":                     "fas fa-concierge-bell",
        "Gym.staffprofile":                "fas fa-user-shield",
        "Gym.staffpermission":             "fas fa-key",
        "Gym.orphanuserdeletionlog":       "fas fa-user-slash",
        "Gym.gymwhatsappsettings":  "fab fa-whatsapp",
        "Gym.whatsappmessagelog":   "fas fa-comment-dots",
        

        # Demo Requests
        "demoRequest.demorequest": "fas fa-comment-dots",

        # Notifications
        "notifications.webpushsubscription": "fas fa-broadcast-tower",

        # Store models
        "Shop.globalproduct":        "fas fa-box-open",
        "Shop.gymproduct":           "fas fa-boxes",
        "Shop.gymproductflavor":     "fas fa-vial",
        "Shop.order":                "fas fa-shopping-cart",
        "Shop.gyminventorymovement": "fas fa-exchange-alt",
        "Shop.staffdevice":          "fas fa-mobile-alt",
        "Shop.globalproductflavor":  "fas fa-flask",

        # Members models
        "AuthFit.enrollment":       "fas fa-id-card",
        "AuthFit.membershipplan":   "fas fa-layer-group",
        "AuthFit.trainer":          "fas fa-user-tie",
        "AuthFit.attendence":       "fas fa-clipboard-user",
        "AuthFit.gymnotification": "fas fa-bell",
        "AuthFit.userdevice":       "fas fa-tablet-alt",
        "AuthFit.contact":          "fas fa-address-book",
        "AuthFit.loginsupportquery":"fas fa-headset",

        # Billing models
        "billing.invoice":         "fas fa-file-invoice",
        "billing.invoicelineitem": "fas fa-list-ul",
        "billing.payment":         "fas fa-money-bill-wave",
        "billing.invoicecounter":  "fas fa-sort-numeric-up",
    },

    "default_icon_parents": "fas fa-chevron-circle-right",
    "default_icon_children": "fas fa-circle",

    "changeform_format":    "horizontal_tabs",
    "related_modal_active": False,
    "custom_css":           "css/admin_custom.css",
}

EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.environ['EMAIL_HOST']
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ['EMAIL_HOST_USER']
EMAIL_HOST_PASSWORD = os.environ['EMAIL_HOST_PASSWORD']
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', EMAIL_HOST_USER)
WHATSAPP_VERIFY_TOKEN = os.environ["WHATSAPP_VERIFY_TOKEN"]
WHATSAPP_APP_SECRET = os.environ["WHATSAPP_APP_SECRET"]