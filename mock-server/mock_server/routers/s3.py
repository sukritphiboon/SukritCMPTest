"""Basic S3-compatible bucket API plus an admin API (token auth) for credentials and quotas."""

from __future__ import annotations

import re
import secrets
from typing import Any
from xml.sax.saxutils import escape

from fastapi import APIRouter, Body, Request, Response

from .. import envelope as E
from ..deps import get_state, require_session
from ..state import MockState

router = APIRouter(prefix="/s3")

BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
NS = "http://s3.amazonaws.com/doc/2006-03-01/"


def _xml(body: str, status: int = 200) -> Response:
    return Response(f'<?xml version="1.0" encoding="UTF-8"?>{body}', status, media_type="application/xml")


def _s3_error(status: int, code: str, message: str, resource: str = "") -> Response:
    return _xml(
        f"<Error><Code>{code}</Code><Message>{escape(message)}</Message>"
        f"<Resource>{escape(resource)}</Resource></Error>",
        status,
    )


def _access_key(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    m = re.search(r"Credential=([^/,\s]+)/", auth) or re.fullmatch(r"AWS ([^:\s]+):\S+", auth)
    return m.group(1) if m else None


def _caller(request: Request) -> tuple[MockState, dict[str, str] | None, Response | None]:
    state = get_state(request)
    if not state.profile.supports_object:
        return state, None, _s3_error(501, "NotImplemented", "Object service is not supported.")
    cred = state.s3_credentials.get(_access_key(request) or "")
    if cred is None:
        return state, None, _s3_error(403, "InvalidAccessKeyId", "The access key is unknown.")
    return state, cred, None


def _endpoint(request: Request) -> str:
    return f"{request.base_url}s3".replace("//s3", "/s3")


# ---- S3 data-plane style API -----------------------------------------------------
@router.get("/")
async def list_buckets(request: Request):
    state, cred, err = _caller(request)
    if err:
        return err
    buckets = [b for b in state.objects["bucket"].values() if b["owner"] == cred["owner"]]
    items = "".join(
        f"<Bucket><Name>{b['NAME']}</Name><CreationDate>{b['created']}</CreationDate></Bucket>"
        for b in buckets
    )
    return _xml(
        f'<ListAllMyBucketsResult xmlns="{NS}"><Owner><ID>{cred["owner"]}</ID></Owner>'
        f"<Buckets>{items}</Buckets></ListAllMyBucketsResult>"
    )


@router.put("/{bucket}")
async def put_bucket(request: Request, bucket: str):
    state, cred, err = _caller(request)
    if err:
        return err
    if not BUCKET_RE.match(bucket):
        return _s3_error(400, "InvalidBucketName", "The bucket name is not valid.", bucket)
    existing = next((b for b in state.objects["bucket"].values() if b["NAME"] == bucket), None)
    if existing:
        code = "BucketAlreadyOwnedByYou" if existing["owner"] == cred["owner"] else "BucketAlreadyExists"
        return _s3_error(409, code, "The bucket already exists.", bucket)
    _create_bucket(state, bucket, cred["owner"])
    return Response(status_code=200, headers={"Location": f"/{bucket}"})


@router.head("/{bucket}")
async def head_bucket(request: Request, bucket: str):
    state, cred, err = _caller(request)
    if err:
        return Response(status_code=err.status_code)
    found = any(b["NAME"] == bucket for b in state.objects["bucket"].values())
    return Response(status_code=200 if found else 404)


@router.delete("/{bucket}")
async def delete_bucket(request: Request, bucket: str):
    state, cred, err = _caller(request)
    if err:
        return err
    found = next((b for b in state.objects["bucket"].values() if b["NAME"] == bucket), None)
    if not found:
        return _s3_error(404, "NoSuchBucket", "The specified bucket does not exist.", bucket)
    if found["owner"] != cred["owner"]:
        return _s3_error(403, "AccessDenied", "Access Denied.", bucket)
    state.delete("bucket", found["ID"])
    return Response(status_code=204)


def _create_bucket(state: MockState, name: str, owner: str) -> dict[str, Any]:
    return state.create(
        "bucket",
        NAME=name,
        owner=owner,
        created=_iso(state.now()),
        quota_bytes=None,
        max_objects=None,
        used_bytes=0,
    )


def _iso(ts: float) -> str:
    import datetime as dt

    return dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _bucket_view(request: Request, b: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": b["NAME"],
        "owner": b["owner"],
        "created": b["created"],
        "quota_bytes": b["quota_bytes"],
        "max_objects": b["max_objects"],
        "used_bytes": b["used_bytes"],
        "endpoint": _endpoint(request),
    }


# ---- admin API (iBaseToken auth) ----------------------------------------------------
def _require_object(request: Request) -> MockState:
    state = require_session(request)
    if not state.profile.supports_object:
        raise E.HuaweiError(E.NOT_SUPPORTED, "Object service is not supported on this device.")
    return state


@router.post("/_admin/credentials")
async def create_credentials(request: Request, body: dict[str, Any] = Body(...)):
    state = _require_object(request)
    E.require(body, "owner")
    access_key = "AK" + secrets.token_hex(8).upper()
    secret_key = secrets.token_urlsafe(30)
    state.s3_credentials[access_key] = {
        "owner": body["owner"],
        "access_key": access_key,
        "secret_key": secret_key,
    }
    return E.ok(
        {
            "owner": body["owner"],
            "access_key": access_key,
            "secret_key": secret_key,
            "endpoint": _endpoint(request),
        }
    )


@router.post("/_admin/buckets")
async def admin_create_bucket(request: Request, body: dict[str, Any] = Body(...)):
    state = _require_object(request)
    E.require(body, "name", "owner")
    if not BUCKET_RE.match(body["name"]):
        raise E.HuaweiError(E.PARAM_ERROR, "The bucket name is not valid.")
    if any(b["NAME"] == body["name"] for b in state.objects["bucket"].values()):
        raise E.HuaweiError(E.OBJECT_EXISTS, "The bucket already exists.")
    bucket = _create_bucket(state, body["name"], body["owner"])
    if body.get("quota_bytes"):
        bucket["quota_bytes"] = int(body["quota_bytes"])
    return E.ok(_bucket_view(request, bucket))


@router.get("/_admin/buckets")
async def admin_list_buckets(request: Request):
    state = _require_object(request)
    return E.ok([_bucket_view(request, b) for b in state.objects["bucket"].values()])


@router.delete("/_admin/buckets/{bucket}")
async def admin_delete_bucket(request: Request, bucket: str):
    state = _require_object(request)
    found = next((b for b in state.objects["bucket"].values() if b["NAME"] == bucket), None)
    if not found:
        raise E.HuaweiError(E.OBJECT_NOT_FOUND, "The bucket does not exist.")
    state.delete("bucket", found["ID"])
    return E.ok()


@router.put("/_admin/buckets/{bucket}/quota")
async def admin_set_quota(request: Request, bucket: str, body: dict[str, Any] = Body(...)):
    state = _require_object(request)
    E.require(body, "quota_bytes")
    found = next((b for b in state.objects["bucket"].values() if b["NAME"] == bucket), None)
    if not found:
        raise E.HuaweiError(E.OBJECT_NOT_FOUND, "The bucket does not exist.")
    quota = int(body["quota_bytes"])
    if quota < found["used_bytes"] or quota <= 0:
        raise E.HuaweiError(E.PARAM_ERROR, "The quota must be positive and not below current usage.")
    found["quota_bytes"] = quota
    if body.get("max_objects") is not None:
        found["max_objects"] = int(body["max_objects"])
    return E.ok(_bucket_view(request, found))
