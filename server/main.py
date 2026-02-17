from fastapi import FastAPI
from router import jobs, host

from sqlalchemy import insert, select
from db.models import Host
from db.db import Session

from log.conf import setup_logging
setup_logging()
app = FastAPI()

app.include_router(jobs.router)
app.include_router(host.router)


if __name__ == "__main__":
    import uvicorn

    with Session.begin() as session:
        exists = session.execute(select(Host)).scalars().all()

        if not exists:
            hostnames = [{"hostname": f'host_{i}'} for i in range(1000)]
            session.execute(insert(Host), hostnames)

    uvicorn.run("main:app", host="0.0.0.0", port=8081, reload=True, loop="asyncio")
