"""User-facing messages for answer results. Never built from gateway text, keys or entities."""

from __future__ import annotations

B1 = (
    "I couldn't find an answer to this in Demo Mortgage Co.'s policy documents. "
    "Try asking again in different words, or I can pass your question to a mortgage specialist."
)
B1H = "Closest sections (low relevance; they may not answer your question):"
B2 = (
    "I found policy sections that look relevant, but I couldn't produce an answer with verified "
    "citations. Please review the sections below, or I can pass your question to a mortgage "
    "specialist."
)
B3 = (
    "The AI answer is turned off right now because the relevance check is unavailable. These "
    "sections matched your question by keyword and meaning. Please review them, or I can pass "
    "your question to a mortgage specialist."
)
B4 = (
    "Your question was blocked by the AI gateway's data policy. Please remove any personal "
    "information, such as a SIN, phone number or account number, and ask in English."
)
B5 = "The AI assistant has reached its usage budget and can't answer right now. Please contact the administrator."  # noqa: E501
B6 = "The AI assistant is receiving too many requests. Please try again in a minute."
B7 = "The AI service took too long to respond. Please try again."
B8 = "The service is unavailable right now. Please try again later."
B9 = "The AI assistant is not configured correctly. Please contact the administrator."
B10 = "The AI service could not process this request. Please rephrase your question and try again."
B11a = "Please enter a question."
B11b = "Please shorten your question to 1,000 characters or fewer."
SFX = "Related policy sections are listed below."

ERROR_MESSAGES = {
    "budget_exceeded": B5,
    "rate_limited": B6,
    "timeout": B7,
    "gateway_unavailable": B8,
    "retrieval_unavailable": B8,
    "auth_config": B9,
    "bad_request": B10,
}
