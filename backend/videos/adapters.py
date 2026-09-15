"""Custom allauth adapter -- the only reason this exists is to log the real
reason behind a social-login failure. Stock allauth calls
on_authentication_error() with the exception/error before rendering its
generic "Third-Party Login Failure" page, but never logs it anywhere, so a
misconfigured OIDC client is otherwise a silent, undebuggable dead end.
"""
import logging

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

logger = logging.getLogger("allauth")


class LoggingSocialAccountAdapter(DefaultSocialAccountAdapter):
    def on_authentication_error(self, request, provider, error=None, exception=None, extra_context=None):
        logger.error(
            "Social login failed for provider=%s error=%s exception=%r extra_context=%s",
            provider, error, exception, extra_context,
        )
        super().on_authentication_error(
            request, provider, error=error, exception=exception, extra_context=extra_context
        )
