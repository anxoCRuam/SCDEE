"""
Internationalization (RNF-10).
"""

from ._paths import BASE_DIR

LANGUAGE_CODE = "es"
LANGUAGES = [
    ("es", "Español"),
    ("en", "English"),
]
LOCALE_PATHS = [
    BASE_DIR / "locale",
]
USE_I18N = True
USE_L10N = True
TIME_ZONE = "UTC"
USE_TZ = True
