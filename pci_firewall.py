"""
PCI Firewall — scrubs sensitive financial/identity data before sending to any LLM.
Runs entirely locally. Zero external calls. Zero cost.

Detects and masks: credit cards, CVV, SSN, IBAN, Indian PAN, Aadhaar.
"""
import re
import logging

log = logging.getLogger(__name__)

_RULES = [
    # Credit card numbers (Visa/MC/Amex/Discover — 13-19 digits, optional spaces/dashes)
    (r'\b(?:4[0-9]{3}|5[1-5][0-9]{2}|3[47][0-9]{2}|6(?:011|5[0-9]{2}))[- ]?'
     r'[0-9]{4}[- ]?[0-9]{4}[- ]?[0-9]{1,7}\b',
     '[CARD_REDACTED]'),

    # CVV / CVC (3-4 digits near keyword)
    (r'(?i)\b(?:cvv|cvc|csc|security[\s_-]*code)\s*[:\-=]?\s*\d{3,4}\b',
     '[CVV_REDACTED]'),

    # US Social Security Number  xxx-xx-xxxx
    (r'\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b',
     '[SSN_REDACTED]'),

    # IBAN  (GB29 NWBK 6016 1331 9268 19)
    (r'\b[A-Z]{2}\d{2}[A-Z0-9 ]{4,32}\b',
     '[IBAN_REDACTED]'),

    # Indian PAN  (ABCDE1234F)
    (r'\b[A-Z]{5}[0-9]{4}[A-Z]\b',
     '[PAN_REDACTED]'),

    # Indian Aadhaar  (xxxx xxxx xxxx)
    (r'\b[2-9]\d{3}[\s\-]?\d{4}[\s\-]?\d{4}\b',
     '[AADHAAR_REDACTED]'),

    # Generic bank account number hint (account no / acct no followed by digits)
    (r'(?i)\b(?:account[\s_-]*(?:no|number|#))\s*[:\-=]?\s*\d{6,20}\b',
     '[ACCT_REDACTED]'),
]

_COMPILED = [(re.compile(pattern), repl) for pattern, repl in _RULES]


def scrub(text: str) -> tuple:
    """
    Redact PCI/PII data from text before sending to any LLM.
    Returns (cleaned_text, [list_of_redacted_types]).
    """
    redacted_types = []
    for pattern, replacement in _COMPILED:
        new_text, n = pattern.subn(replacement, text)
        if n:
            redacted_types.append(replacement.strip('[]'))
            text = new_text
    if redacted_types:
        log.warning(f"[PCI_FIREWALL] Blocked {len(redacted_types)} sensitive item(s): {redacted_types}")
    return text, redacted_types


def has_sensitive_data(text: str) -> bool:
    return any(p.search(text) for p, _ in _COMPILED)
