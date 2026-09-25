"""
Jamf-SnipeIT Suite - Matching Utilities
User matching algorithms and identity resolution.
"""
from .user_matcher import (
    longest_common_subsequence,
    char_overlap,
    bigram_dice_coefficient,
    normalize_name,
    UserMatcher,
    pick_primary_local_identity,
)

__all__ = [
    "longest_common_subsequence",
    "char_overlap",
    "bigram_dice_coefficient",
    "normalize_name",
    "UserMatcher",
    "pick_primary_local_identity",
]
