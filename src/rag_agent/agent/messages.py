"""User-facing messages of the agent loop. Fixed strings; never built from model or gateway text."""

from __future__ import annotations

C1 = (
    "This exception request is only a draft. The underwriter must review and raise it, and then "
    "someone with the right approval authority must decide it. Nothing has been raised or "
    "approved yet."
)
C2 = (
    "I stopped because several of the requested actions are not permitted for your role or this "
    "loan. Please check the request, or contact a senior underwriter."
)
C3 = "I stopped because this request needed too many steps. Please split it into smaller questions."
C4 = "Your user ID is not recognized."
