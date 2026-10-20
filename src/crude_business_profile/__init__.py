"""crude-business-profile: your own Google Business Profile over Google's APIs.

Reads the locations a Google account manages and their reviews, and writes the
business's reply to a review, in the ``crude-business-profile <resource> <verb>``
grammar. Google's Account Management API (the accounts), Business Information
API (the locations) and v4 Google My Business API (reviews and replies) serve
it, all on one OAuth user token with the ``business.manage`` scope. Only
locations the signed-in account manages are reachable; another business's
reviews are not.
"""
