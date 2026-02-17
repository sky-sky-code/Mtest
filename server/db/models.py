import enum
import uuid

from typing import Optional, Any
from datetime import datetime, timezone

from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, JSON, ForeignKey, DateTime, func, Text, Enum, Integer, UniqueConstraint

from .db import Base


class Host(Base):
    __tablename__ = 'hosts'

    hostname: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    metadata_: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, name='metadata', nullable=True)

    blocks: Mapped[list['HostCommandBlock']] = relationship(back_populates='host', cascade='all, delete-orphan')
    executions: Mapped[list["Execution"]] = relationship(back_populates="host", cascade="all, delete-orphan")


class Job(Base):
    __tablename__ = 'jobs'
    __table_args__ = (UniqueConstraint('external_id', 'signature', name='external_id_signature'),)

    class CommandType(str, enum.Enum):
        PING = "PING"
        RESTART_SERVICE = "RESTART_SERVICE"
        DEPLOY = "DEPLOY"
        RUN_SCRIPT = "RUN_SCRIPT"

    class ApprovalState(str, enum.Enum):
        WAIT_APPROVAL = "WAIT_APPROVAL"
        APPROVED = "APPROVED"
        REJECTED = "REJECTED"

    class Status(str, enum.Enum):
        NEW = "NEW"
        QUEUED = "QUEUED"
        RUNNING = "RUNNING"
        SUCCESS = "SUCCESS"
        FAILED = "FAILED"
        PARTIAL = "PARTIAL"

    external_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=True)

    selector: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default={})
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default={})
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=lambda: datetime.now(timezone.utc))

    #TODO добавить кто сделать approve и когда
    status: Mapped[Status] = mapped_column(Enum(Status, name='job_status'), nullable=False, default=Status.NEW)
    command_type: Mapped[CommandType] = mapped_column(Enum(CommandType, name='job_command_type'), nullable=False)
    approval_state: Mapped[ApprovalState] = mapped_column(Enum(ApprovalState, name='job_approval_state'), nullable=True)
    executions: Mapped[list["Execution"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class HostCommandBlock(Base):
    __tablename__ = 'host_command_blocks'

    host_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('hosts.uid', ondelete='CASCADE'), nullable=False)
    command_type: Mapped[Job.CommandType] = mapped_column(Enum(Job.CommandType, name='host_block_command_type'), nullable=False)

    host: Mapped[Host] = relationship(back_populates='blocks')


class Execution(Base):
    __tablename__ = 'executions'

    class Status(str, enum.Enum):
        NEW = "NEW"
        QUEUED = "QUEUED"
        RUNNING = "RUNNING"
        SUCCESS = "SUCCESS"
        FAILED = "FAILED"
        CANCELLED = "CANCELLED"
        TIMEOUT = "TIMEOUT"
        BLOCKED = "BLOCKED"

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('jobs.uid', ondelete='CASCADE'), nullable=False)
    host_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('hosts.uid', ondelete='CASCADE'), nullable=False)
    status: Mapped[Status] = mapped_column(Enum(Status, name='executions_status'), default=Status.NEW, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=lambda: datetime.now(timezone.utc))

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    job: Mapped["Job"] = relationship(back_populates="executions")
    host: Mapped["Host"] = relationship(back_populates="executions")
    logs: Mapped[list["ExecutionLogs"]] = relationship(
        back_populates="execution", cascade="all, delete-orphan", order_by="ExecutionLogs.ts.asc()"
    )


class ExecutionLogs(Base):
    __tablename__ = 'execution_logs'

    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('executions.uid'), nullable=False)

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    line: Mapped[str] = mapped_column(Text, nullable=False)
    execution: Mapped["Execution"] = relationship(back_populates="logs")


class Outbox(Base):
    __tablename__ = 'outbox_event'

    class Status(str, enum.Enum):
        NEW = "NEW"
        SENT = "SENT"
        FAILED = "FAILED"

    class Event(str, enum.Enum):
        PLAN_JOB = "PLAN_JOB"

    event_type: Mapped[Event] = mapped_column(Enum(Event, name='outbox_event_type'), default=Event.PLAN_JOB)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=True)
    status: Mapped[Status] = mapped_column(Enum(Status, name='outbox_status'), nullable=False, default=Status.NEW)
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=lambda: datetime.now(timezone.utc))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

