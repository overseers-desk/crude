"""crude-business-profile: your own Google Business Profile over Google's APIs.

Reads the locations a Google account manages and their reviews, and writes the
business's reply to a review, in the ``crude-business-profile <resource> <verb>``
grammar. Three Google APIs serve it: Account Management (the accounts), Business
Information (the locations) and the v4 Google My Business API (reviews and
replies), all on one OAuth user token with the ``business.manage`` scope. Only
locations the signed-in account manages are reachable; another business's
reviews are not.
"""
