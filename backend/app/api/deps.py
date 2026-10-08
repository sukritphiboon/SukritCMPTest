from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import get_session, get_sessionmaker
from app.core.security import get_actor
from app.services.context import Ctx, DriverFactory, default_driver_factory


def get_driver_factory() -> DriverFactory:
    """Overridden in tests to point drivers at an in-process mock server."""
    return default_driver_factory


def get_maker() -> async_sessionmaker[AsyncSession]:
    """Session factory for work that needs several independent sessions. Overridden in tests."""
    return get_sessionmaker()


def get_now() -> datetime:
    """Overridden in tests to replay a fixed clock."""
    return datetime.now(UTC)


async def get_ctx(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[str, Depends(get_actor)],
    factory: Annotated[DriverFactory, Depends(get_driver_factory)],
) -> Ctx:
    return Ctx(session, actor, factory, request.client.host if request.client else None)


CtxDep = Annotated[Ctx, Depends(get_ctx)]
NowDep = Annotated[datetime, Depends(get_now)]
MakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_maker)]


class Page:
    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        self.limit, self.offset = limit, offset


PageDep = Annotated[Page, Depends(Page)]
