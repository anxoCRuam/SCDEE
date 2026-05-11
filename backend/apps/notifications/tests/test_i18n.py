"""
Internationalisation smoke test for the notification email pipeline.

Verifies that the wiring between Django's i18n machinery and the
notification templates actually works: a string surrounded by
``{% trans %}`` is replaced by its translated form when the active
language is switched.

How it works:

1. The test creates a temporary ``locale/es/LC_MESSAGES/django.po``
   file with a single (English-source → Spanish-target) translation.
2. It compiles that ``.po`` to ``.mo`` with ``msgfmt`` (Django needs
   the binary catalogue, not the source).
3. It points ``LOCALE_PATHS`` at the temporary directory.
4. It renders the ``grades_published`` template under both ``en``
   and ``es`` and asserts that the Spanish render carries the
   translated string while the English render keeps the source text.

This is **not** a test that production translations exist (they
don't, in v1.0 — see the requirements audit). It is a test that the
pipeline through which production translations would flow is wired
correctly: missing the test would mean a future ``msgmerge`` could
drift silently and nobody would notice until a user hit the bug.

References: RF-13.7, RNF-10.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from django.test import override_settings
from django.utils import translation

pytestmark = pytest.mark.django_db


# ── Fixture ────────────────────────────────────────────────────────


@pytest.fixture
def temp_locale_dir(tmp_path: Path) -> Path:
    """Create a self-contained ``locale/es/LC_MESSAGES/django.po``+``.mo``
    inside ``tmp_path`` and return the locale root.

    The translation entry covers the literal string used in the
    ``grades_published.txt`` template:
    ``"You can consult your grade by signing in to SCDEE."``
    """
    locale_root = tmp_path / "locale"
    es_lc_messages = locale_root / "es" / "LC_MESSAGES"
    es_lc_messages.mkdir(parents=True)

    po_contents = (
        'msgid ""\n'
        'msgstr ""\n'
        '"Content-Type: text/plain; charset=UTF-8\\n"\n'
        '"Language: es\\n"\n'
        "\n"
        "# Translation of the SCDEE-specific string used in "
        "grades_published.txt.\n"
        'msgid "You can consult your grade by signing in to SCDEE."\n'
        'msgstr "Puede consultar su nota iniciando sesión en SCDEE."\n'
        "\n"
        'msgid "Best regards"\n'
        'msgstr "Un saludo"\n'
    )
    po_path = es_lc_messages / "django.po"
    po_path.write_text(po_contents, encoding="utf-8")

    # Compile to .mo. ``msgfmt`` ships with gettext on Linux/macOS.
    # On hosts without it, the test is skipped — the pipeline being
    # tested is Django+gettext, so a missing gettext means the host
    # cannot exercise i18n at all.
    mo_path = es_lc_messages / "django.mo"
    subprocess.run(  # noqa: S603
        ["msgfmt", "-o", str(mo_path), str(po_path)],  # noqa: S607
        check=True,
        capture_output=True,
    )

    return locale_root


# ── Helpers ────────────────────────────────────────────────────────


def _flush_translation_caches() -> None:
    """Discard Django's in-process translation catalogue cache.

    Django caches loaded translation catalogues in
    ``django.utils.translation.trans_real``. Switching ``LOCALE_PATHS``
    or compiling new ``.mo`` files at runtime is invisible until the
    caches are cleared — a normal occurrence in tests but not in
    production.
    """
    from django.utils.translation import trans_real

    trans_real._translations = {}
    trans_real._default = None
    trans_real._active = trans_real.Local()


# ── Tests ──────────────────────────────────────────────────────────


def test_template_renders_in_spanish_when_language_overridden(temp_locale_dir):
    """Active language ``es`` → template uses the Spanish translation."""
    with override_settings(LOCALE_PATHS=[temp_locale_dir]):
        _flush_translation_caches()

        # Sanity: Django actually finds the translation.
        with translation.override("es"):
            from django.utils.translation import gettext

            translated = gettext("You can consult your grade by signing in to SCDEE.")
            assert translated == "Puede consultar su nota iniciando sesión en SCDEE.", (
                f"gettext returned {translated!r}; the .mo catalogue is not "
                f"being picked up. Check LOCALE_PATHS and the cache flush."
            )

        # Now render the actual template in es and en, and check that
        # the strings differ on the translated phrase.
        from apps.notifications.email import render_email

        # Build a minimal user-like context object.
        class _User:
            first_name = "Ana"

        context = {"user": _User(), "exam_name": "Cálculo I"}

        _, body_es, _ = render_email("grades_published", language="es", context=context)
        _, body_en, _ = render_email("grades_published", language="en", context=context)

    assert (
        "Puede consultar su nota" in body_es
    ), f"The Spanish render should contain the translated phrase, got body_es={body_es!r}."
    assert (
        "You can consult your grade" in body_en
    ), f"The English render should contain the source phrase, got body_en={body_en!r}."
    # The two renders MUST differ — if not, the template was not
    # actually wired to gettext.
    assert body_es != body_en, (
        "Spanish and English renders are identical. Either the "
        "template doesn't use {% trans %}/{% blocktrans %} on the "
        "asserted strings, or the language switch did not take effect."
    )


def test_email_language_for_returns_default_for_anonymous_user(settings):
    """``email_language_for(None)`` falls back to ``LANGUAGE_CODE``.

    Sanity check that does not require a translation catalogue. This
    is the entry point used by the password-reset flow when no user
    is authenticated yet.
    """
    from apps.notifications.email import email_language_for

    assert email_language_for(None) == settings.LANGUAGE_CODE
