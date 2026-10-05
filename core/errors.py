# core/errors.py
"""
Errors written for the person at the screen, and the one way to send one.

Views used to answer with str(e) of whatever they caught. That is right for
Rasova's own messages ("The opening cash can't be negative.", "A cart can
have at most 30 lines."), and wrong for anything else: a view catching a
plain ValueError or TypeError would hand a guest Python's own words
("'NoneType' object has no attribute 'unit'"), which describe the code, not
the problem. GitHub's code scanning flags every such line (CodeQL,
py/stack-trace-exposure).

So:
  * an error whose message is written for the user is a UserError (or a
    subclass: NumberInputError, OrderError, CartError, ...);
  * a view answers with error_response(e): a UserError or a Django
    ValidationError (whose messages Rasova writes in its models and
    services) is shown as it is; anything else is logged with its
    traceback and the screen gets a plain "Something went wrong".
"""
import logging

from django.core.exceptions import ValidationError
from django.http import JsonResponse

logger = logging.getLogger("pos.core")

GENERIC_MESSAGE = "Something went wrong. Please try again."


class UserError(Exception):
    """An error whose message is written for the person using the screen,
    and is safe to show them as it is."""

    @property
    def message(self):
        return str(self.args[0]) if self.args else ""


def user_message(exc):
    """What the screen may say about `exc`."""
    if isinstance(exc, UserError):
        return exc.message
    if isinstance(exc, ValidationError):
        return " ".join(exc.messages)
    logger.error("Unexpected %s; the screen was given a generic message", type(exc).__name__,
                 exc_info=(type(exc), exc, exc.__traceback__))
    return GENERIC_MESSAGE


def error_response(exc, status=400):
    """The JSON answer for a refused request: {"error": what the screen may say}."""
    return JsonResponse({"error": user_message(exc)}, status=status)
