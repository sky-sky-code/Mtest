import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, insert, delete

from db.models import Job, Host, HostCommandBlock
from db.db import Session

router = APIRouter(tags=['host'])


class HostCommandBlockSchema(BaseModel):
    commands: list[Job.CommandType]


@router.put("/hosts/{host_id}/blocks")
def set_host_blocks(host_id: uuid.UUID, body: HostCommandBlockSchema):
    commands = list(dict.fromkeys(body.commands))
    with Session.begin() as session:
        exists = session.execute(
            select(Host.uid).where(Host.uid == host_id)
        ).scalar_one_or_none()

        if not exists:
            raise HTTPException(status_code=404, detail="Host not found")

        session.execute(
            delete(HostCommandBlock).where(HostCommandBlock.host_id == host_id)
        )

        if commands:
            stmt = insert(HostCommandBlock).values([{"host_id": host_id, "command_type": cmd} for cmd in commands])
            session.execute(stmt)

        current = session.execute(
            select(HostCommandBlock.command_type)
            .where(HostCommandBlock.host_id == host_id)
            .order_by(HostCommandBlock.command_type.asc())
        ).scalars().all()

        return {
            "host_id": str(host_id),
            "blocked_commands": [c.value for c in current],
        }


@router.delete("/hosts/{host_id}/blocks/{command_type}")
def delete_host_block(host_id: uuid.UUID, command_type: Job.CommandType):
    with Session.begin() as session:
        exists = session.execute(select(Host.uid).where(Host.uid == host_id)).scalar_one_or_none()
        if not exists:
            raise HTTPException(status_code=404, detail="Host not found")

        res = session.execute(
            delete(HostCommandBlock).where(
                HostCommandBlock.host_id == host_id,
                HostCommandBlock.command_type == command_type,
            )
        )

        return {"deleted": int(res.rowcount or 0)}
