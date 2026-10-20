"""SQLAlchemy models. Importing this package registers all tables on `Base.metadata`.

Convention: tables holding per-user data reference `user.id` with ON DELETE CASCADE,
shared or log tables use ON DELETE SET NULL.
"""

from app.models.job import Job, JobRun, JobStatus, RunStatus
from app.models.quota import QuotaLedger
from app.models.setting import GlobalSetting, UserSetting
from app.models.user import Role, User

__all__ = [
    "GlobalSetting",
    "Job",
    "JobRun",
    "JobStatus",
    "QuotaLedger",
    "Role",
    "RunStatus",
    "User",
    "UserSetting",
]
