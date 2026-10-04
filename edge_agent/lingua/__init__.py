"""Lingua technis: machine language to plain Traditional Chinese, exactly.

The user reads what the Agent translates and does the reasoning themself
(「翻譯絕對精準」). So every translator here is deterministic: a parser
decides what each part means, and the wording comes from fixed tables whose
claims are checked against the real engines in the tests. No model writes or
rewrites a translation; a model may only add a summary on top, which the
caller has to mark as such.
"""
