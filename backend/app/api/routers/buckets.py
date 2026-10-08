import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CtxDep, PageDep
from app.models import ObjectBucket
from app.schemas.api import BucketCreate, BucketOut, BucketQuota, CredentialsCreate, CredentialsOut
from app.services import provisioning

router = APIRouter(prefix="/buckets", tags=["object buckets"])


@router.post("", response_model=BucketOut, status_code=201)
async def create_bucket(body: BucketCreate, ctx: CtxDep):
    bucket = await provisioning.create_bucket(ctx, body)
    await ctx.session.refresh(bucket)
    return bucket


@router.post("/credentials", response_model=CredentialsOut, status_code=201)
async def issue_credentials(body: CredentialsCreate, ctx: CtxDep):
    creds = await provisioning.issue_credentials(ctx, body)
    return CredentialsOut(
        owner=creds.owner, access_key=creds.access_key, secret_key=creds.secret_key, endpoint=creds.endpoint
    )


@router.get("", response_model=list[BucketOut])
async def list_buckets(
    ctx: CtxDep, page: PageDep, tenant_id: uuid.UUID | None = None, storage_device_id: uuid.UUID | None = None
):
    stmt = select(ObjectBucket).order_by(ObjectBucket.created_at, ObjectBucket.bucket_name)
    if tenant_id:
        stmt = stmt.where(ObjectBucket.tenant_id == tenant_id)
    if storage_device_id:
        stmt = stmt.where(ObjectBucket.storage_device_id == storage_device_id)
    return (await ctx.session.scalars(stmt.limit(page.limit).offset(page.offset))).all()


@router.get("/{bucket_id}", response_model=BucketOut)
async def get_bucket(bucket_id: uuid.UUID, ctx: CtxDep):
    return await ctx.get(ObjectBucket, bucket_id, "Bucket")


@router.put("/{bucket_id}/quota", response_model=BucketOut)
async def set_quota(bucket_id: uuid.UUID, body: BucketQuota, ctx: CtxDep):
    bucket = await provisioning.set_bucket_quota(ctx, await ctx.get(ObjectBucket, bucket_id, "Bucket"), body)
    await ctx.session.refresh(bucket)
    return bucket


@router.delete("/{bucket_id}", status_code=204)
async def delete_bucket(bucket_id: uuid.UUID, ctx: CtxDep):
    await provisioning.delete_bucket(ctx, await ctx.get(ObjectBucket, bucket_id, "Bucket"))
