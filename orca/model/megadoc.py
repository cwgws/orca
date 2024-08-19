import logging
import os

import regex as re
from slugify import slugify
from sqlalchemy import Column, ForeignKey, String
from sqlalchemy.orm import relationship

from orca import config
from orca._helpers import create_uid, utc_now
from orca.model.base import Base, CommonMixin, StatusMixin

log = logging.getLogger(__name__)


class Megadoc(Base, CommonMixin, StatusMixin):
    """A megadoc is text file containing the results of every document matching
    our search. This is the main thing we're here to produce.
    """

    uid = Column(String(22), primary_key=True)
    filetype = Column(String(16), nullable=False, default=".txt")
    filename = Column(String(255), default="")
    path = Column(String(255), default="")
    url = Column(String(255), default="")
    search_uid = Column(String(22), ForeignKey("searches.uid"), nullable=False)
    search = relationship("Search", back_populates="megadocs")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # We need the ID to generate paths so we'll do it manually here
        self.uid = create_uid()

        # Generate the paths
        timestamp = re.sub(
            r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2}).*",
            r"\1\2\3-\4\5\6",
            f"{utc_now().isoformat()}",
        )
        self.filename = f"{slugify(self.search.search_str)}_{timestamp}Z{self.filetype}"
        self.path = f"{config.megadoc_path / self.filename}"
        if self.full_path.is_file():
            log.warning(f"File already exists, could be error: {self.full_path}")
        self.url = f"{config.cdn.url}/{self.path}"

        # Clear redis progress ticker
        config.db.redis.hset(self.redis_key, "progress", 0)

    @property
    def full_path(self):
        """Returns the full canonical path as a pathlib object."""
        return config.data_path / self.path

    @property
    def filesize(self):
        """Size of megadoc file in bytes. Returns 0 if no file."""
        try:
            self.full_path.touch()
            return os.path.getsize(self.full_path)
        except OSError as e:
            log.warning(f"Error finding size of megadoc: {e}")
            return 0

    @property
    def progress(self):
        """Get current progress from redis if working."""
        if self.status == "PENDING":
            return 0.0
        if self.status in {"SENDING", "SUCCESS"}:
            return 100.0
        ticks = float(int(config.db.redis.hget(self.redis_key, "progress")))
        return ticks / float(len(self.search.documents))

    def tick(self, n=1):
        """Increment redis progress ticker."""
        ticks = int(config.db.redis.hget(self.redis_key, "progress"))
        config.db.redis.hset(self.redis_key, "progress", ticks + n)

    def as_dict(self):
        rows = super().as_dict()
        for key in {"filename", "path", "search_uid"}:
            rows.pop(key)
        rows["filesize"] = self.filesize
        if self.status not in ["SENDING", "SUCCESS"]:
            rows["progress"] = self.progress
        return rows
