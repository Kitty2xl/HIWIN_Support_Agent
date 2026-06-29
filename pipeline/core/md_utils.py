"""
md_utils.py — small Markdown post-processing helpers shared across passes.
"""

import re

# Pass 1 stamps image regions onto the page as bare bracket placeholders, e.g.
#   [Tables/page_5_table_1.jpg]   or   [Figures/page_5_figure_2.jpg]
# Pass 2's prompt asks the VLM to rewrite these as real Markdown image tags
# (![Table 1](Tables/...)), but the model does not always comply — it often
# copies the placeholder verbatim.  Downstream passes (2b captioning, 3 table
# transcription) only match the proper ![...](...) form, so an un-converted
# placeholder is silently skipped and never transcribed.
#
# This regex catches the bare form so we can normalise it deterministically:
#   - (?<!\!)  : not already preceded by '!', so real image tags are left alone
#   - captures a Tables/ or Figures/ path with no spaces or closing bracket
_BARE_PLACEHOLDER_RE = re.compile(r'(?<!\!)\[((?:Tables|Figures)/[^\]\s]+)\]')


def normalize_image_placeholders(text: str) -> str:
    """
    Convert bare image placeholders left by Pass 2 into proper Markdown image
    tags so figure captioning (Pass 2b) and table transcription (Pass 3) can
    find them.

        [Tables/x.jpg]   ->  ![](Tables/x.jpg)
        [Figures/y.jpg]  ->  ![](Figures/y.jpg)

    Tags already in Markdown image form (``![alt](path)``) are left untouched.
    """
    if not text:
        return text
    return _BARE_PLACEHOLDER_RE.sub(r'![](\1)', text)
