from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, UniqueConstraint

from config.database import Base


class TgArchiveTask(Base):
    """一次归档提交；消息成员及版本由 TgArchiveMessage 保存。"""

    __tablename__ = 'tg_archive_task'

    task_id = Column(BigInteger, primary_key=True, autoincrement=True)
    dedupe_key = Column(String(64), nullable=False, unique=True)
    message_id = Column(BigInteger, nullable=False, index=True)  # Queue anchor; all members live in tg_archive_message.
    media_revision = Column(Integer, nullable=False)
    storage_id = Column(String(64), nullable=False)
    target_path = Column(String(1000), nullable=False)
    package_name = Column(String(255), nullable=False)
    archive_format = Column(String(20), nullable=False, server_default='files')
    local_path = Column(String(500), nullable=False, server_default='')
    source_cleanup = Column(String(20), nullable=False, server_default='none')
    cleanup_error = Column(Text, nullable=True)
    manifest_json = Column(Text, nullable=False)
    status = Column(String(30), nullable=False, server_default='queued', index=True)
    attempts = Column(Integer, nullable=False, server_default='0')
    next_retry_at = Column(DateTime, nullable=True)
    lease_until = Column(DateTime, nullable=True)
    lease_token = Column(String(40), nullable=True)
    last_error = Column(Text, nullable=True)
    create_by = Column(String(64), nullable=False)
    create_time = Column(DateTime, nullable=False, default=datetime.now)
    completed_at = Column(DateTime, nullable=True)


class TgArchiveMessage(Base):
    __tablename__ = 'tg_archive_message'

    task_id = Column(BigInteger, primary_key=True)
    message_id = Column(BigInteger, primary_key=True, index=True)
    media_revision = Column(Integer, nullable=False)


class TgArchiveItem(Base):
    __tablename__ = 'tg_archive_item'
    __table_args__ = (UniqueConstraint('task_id', 'file_name', name='uq_tg_archive_item_file'),)

    item_id = Column(BigInteger, primary_key=True, autoincrement=True)
    task_id = Column(BigInteger, nullable=False, index=True)
    media_id = Column(BigInteger, nullable=True)
    file_name = Column(String(255), nullable=False)
    file_size = Column(BigInteger, nullable=False)
    sha256 = Column(String(64), nullable=False)
    status = Column(String(30), nullable=False, server_default='queued')
    remote_id = Column(String(1000), nullable=True)
    last_error = Column(Text, nullable=True)
