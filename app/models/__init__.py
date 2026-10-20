"""SQLAlchemy models. Importing this package registers all tables on `Base.metadata`.

Convention: tables holding per-user data reference `user.id` with ON DELETE CASCADE,
shared or log tables use ON DELETE SET NULL.
"""

from app.models.channel import Channel
from app.models.download import (
    Download,
    DownloadKind,
    DownloadRequest,
    DownloadStatus,
    RequestSource,
)
from app.models.filter_rule import FilterRule, FilterRuleType
from app.models.job import Job, JobRun, JobStatus, RunStatus
from app.models.quota import QuotaLedger
from app.models.setting import GlobalSetting, UserSetting
from app.models.subscription import Subscription, SubscriptionSource, SubscriptionStatus
from app.models.user import Role, User
from app.models.user_video_state import UserVideoState
from app.models.video import LiveStatus, Video
from app.models.watch_event import WatchEvent, WatchEventType

__all__ = [
    "Channel",
    "Download",
    "DownloadKind",
    "DownloadRequest",
    "DownloadStatus",
    "FilterRule",
    "FilterRuleType",
    "GlobalSetting",
    "Job",
    "JobRun",
    "JobStatus",
    "LiveStatus",
    "QuotaLedger",
    "RequestSource",
    "Role",
    "RunStatus",
    "Subscription",
    "SubscriptionSource",
    "SubscriptionStatus",
    "User",
    "UserSetting",
    "UserVideoState",
    "Video",
    "WatchEvent",
    "WatchEventType",
]
