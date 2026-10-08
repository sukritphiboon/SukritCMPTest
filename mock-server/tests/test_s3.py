
async def admin(c, method, path, json=None):
    r = await c.http.request(method, f"/s3{path}", json=json, headers={"iBaseToken": c.token})
    return r.json()


def auth(ak):
    return {"Authorization": f"AWS {ak}:signature"}


async def new_creds(c, owner="tenant-a"):
    body = await admin(c, "POST", "/_admin/credentials", {"owner": owner})
    assert body["error"]["code"] == 0
    return body["data"]


async def test_credentials_are_generated(dorado):
    creds = await new_creds(dorado)
    assert creds["access_key"].startswith("AK") and len(creds["secret_key"]) >= 20
    other = await new_creds(dorado, "tenant-b")
    assert other["access_key"] != creds["access_key"]


async def test_s3_bucket_lifecycle(dorado):
    ak = (await new_creds(dorado))["access_key"]
    r = await dorado.http.put("/s3/my-bucket", headers=auth(ak))
    assert r.status_code == 200

    r = await dorado.http.get("/s3/", headers=auth(ak))
    assert r.status_code == 200 and "<Name>my-bucket</Name>" in r.text
    assert r.headers["content-type"].startswith("application/xml")

    r = await dorado.http.put("/s3/my-bucket", headers=auth(ak))
    assert r.status_code == 409 and "BucketAlreadyOwnedByYou" in r.text

    assert (await dorado.http.head("/s3/my-bucket", headers=auth(ak))).status_code == 200
    assert (await dorado.http.delete("/s3/my-bucket", headers=auth(ak))).status_code == 204
    r = await dorado.http.delete("/s3/my-bucket", headers=auth(ak))
    assert r.status_code == 404 and "NoSuchBucket" in r.text


async def test_s3_rejects_unknown_key_and_bad_name(dorado):
    r = await dorado.http.get("/s3/", headers=auth("AKUNKNOWN"))
    assert r.status_code == 403 and "InvalidAccessKeyId" in r.text
    ak = (await new_creds(dorado))["access_key"]
    r = await dorado.http.put("/s3/Bad_Name", headers=auth(ak))
    assert r.status_code == 400 and "InvalidBucketName" in r.text


async def test_buckets_are_isolated_per_owner(dorado):
    a = (await new_creds(dorado, "a"))["access_key"]
    b = (await new_creds(dorado, "b"))["access_key"]
    await dorado.http.put("/s3/bucket-a", headers=auth(a))
    assert "bucket-a" not in (await dorado.http.get("/s3/", headers=auth(b))).text
    assert (await dorado.http.delete("/s3/bucket-a", headers=auth(b))).status_code == 403


async def test_admin_bucket_and_quota(dorado):
    body = await admin(
        dorado, "POST", "/_admin/buckets", {"name": "quota-bucket", "owner": "t1", "quota_bytes": 10**9}
    )
    assert body["data"]["quota_bytes"] == 10**9 and body["data"]["endpoint"].endswith("/s3")
    body = await admin(dorado, "PUT", "/_admin/buckets/quota-bucket/quota", {"quota_bytes": 5 * 10**9})
    assert body["data"]["quota_bytes"] == 5 * 10**9
    body = await admin(dorado, "PUT", "/_admin/buckets/missing/quota", {"quota_bytes": 1})
    assert body["error"]["code"] == 1077948996


async def test_admin_requires_token(dorado):
    r = await dorado.http.post("/s3/_admin/credentials", json={"owner": "x"})
    assert r.json()["error"]["code"] == -401


async def test_oceanprotect_has_no_object_service(protect):
    r = await protect.http.get("/s3/", headers=auth("AKX"))
    assert r.status_code == 501
    body = await admin(protect, "POST", "/_admin/credentials", {"owner": "x"})
    assert body["error"]["code"] == 1077949004
