"""Errors raised by the SIP layer. Kept free of pjsua2 so callers (and
tests) can catch them without loading PJSIP."""


class SipError(Exception):
    """A PJSIP call failed. Wraps pjsua2.Error so callers don't import pjsua2."""
