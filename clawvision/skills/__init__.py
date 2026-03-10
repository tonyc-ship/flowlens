"""Site-specific visual understanding skills.

Skills use lightweight CV (not ML models) to detect page structure
and extract semantic regions from screenshots.
"""

from .base import SiteSkill
from .xiaohongshu_skill import XiaohongshuSkill

__all__ = ["SiteSkill", "XiaohongshuSkill"]
