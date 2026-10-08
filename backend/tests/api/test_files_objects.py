async def fs_body(env, dev, tenant, name="fs1", **extra):
    return {
        "name": name,
        "size_gb": 20,
        "protocol": "nfs",
        "storage_device_id": dev["id"],
        "tenant_id": tenant["id"],
        **extra,
    }


async def test_nfs_filesystem_with_quota(env):
    dev, tenant = await env.device(), await env.tenant(file=100)
    fs = await env.ok("POST", "/filesystems", json=await fs_body(env, dev, tenant, quota_hard_gb=10))
    assert fs["share_path"] == "/fs1/" and fs["quota_status"] == "within_limit"
    assert fs["quota_hard_gb"] == 10 and fs["quota_soft_gb"] == 8
    assert len(env.dorado_mock.objects["nfshare"]) == 1 and len(env.dorado_mock.objects["fsquota"]) == 1

    updated = await env.ok("POST", f"/filesystems/{fs['id']}/quota", json={"hard_gb": 15, "soft_gb": 12})
    assert updated["quota_hard_gb"] == 15 and updated["quota_soft_gb"] == 12
    await env.err("POST", f"/filesystems/{fs['id']}/quota", 422, json={"hard_gb": 5, "soft_gb": 6})

    snap = await env.ok("POST", f"/filesystems/{fs['id']}/snapshots", json={"name": "snap"})
    assert snap["resource_type"] == "filesystem"
    assert (await env.ok("GET", f"/tenants/{tenant['id']}/usage"))["file"]["used_gb"] == 20

    await env.ok("DELETE", f"/filesystems/{fs['id']}", status=204)
    assert not env.dorado_mock.objects["filesystem"]
    assert [a["action"] for a in await env.audit(resource_type="filesystem") if a["outcome"] == "success"][
        -1
    ] == "filesystem.create"


async def test_cifs_filesystem_and_quota_limits(env):
    dev, tenant = await env.device(), await env.tenant(file=30)
    fs = await env.ok(
        "POST", "/filesystems", json=await fs_body(env, dev, tenant, protocol="cifs", share_name="finance")
    )
    assert fs["protocol"] == "cifs" and fs["quota_status"] == "none" and fs["quota_hard_gb"] is None
    assert list(env.dorado_mock.objects["cifshare"].values())[0]["NAME"] == "finance"
    await env.err("POST", "/filesystems", 409, json=await fs_body(env, dev, tenant, "fs2", size_gb=11))
    await env.err(
        "POST",
        "/filesystems",
        422,
        json=await fs_body(env, dev, tenant, "fs3", size_gb=1, quota_hard_gb=2, quota_soft_gb=3),
    )


async def test_failed_share_rolls_back_the_filesystem(env):
    dev, tenant = await env.device(), await env.tenant()
    await env.ok("POST", "/filesystems", json=await fs_body(env, dev, tenant, "a"))
    # same NFS path on the array -> create_share fails after the filesystem was created
    env.dorado_mock.objects["nfshare"]["1"]["SHAREPATH"] = "/b/"
    await env.err("POST", "/filesystems", 409, json=await fs_body(env, dev, tenant, "b"))
    assert [f["NAME"] for f in env.dorado_mock.objects["filesystem"].values()] == ["a"]
    assert len(await env.ok("GET", "/filesystems")) == 1


async def test_oceanprotect_worm_filesystem(env):
    dev, tenant = await env.device("oceanprotect"), await env.tenant(file=5000)
    policy = await env.ok(
        "POST",
        "/protection-policies",
        json={
            "name": "locked",
            "retention_days": 30,
            "worm_mode": "compliance",
            "snapshot_schedule": "0 2 * * *",
        },
    )
    fs = await env.ok(
        "POST",
        "/filesystems",
        json=await fs_body(env, dev, tenant, "repo", size_gb=1000, protection_policy_id=policy["id"]),
    )
    assert list(env.protect_mock.objects["wormpolicy"].values())[0]["MODENAME"] == "compliance"
    r = await env.err("DELETE", f"/filesystems/{fs['id']}", 409)
    assert r["device_error_code"] == 1077936900
    assert (await env.ok("GET", f"/filesystems/{fs['id']}"))["name"] == "repo"


async def test_worm_policy_needs_oceanprotect(env):
    dev, tenant = await env.device(), await env.tenant()
    policy = await env.ok(
        "POST", "/protection-policies", json={"name": "w", "retention_days": 5, "worm_mode": "enterprise"}
    )
    await env.err(
        "POST", "/filesystems", 422, json=await fs_body(env, dev, tenant, protection_policy_id=policy["id"])
    )
    assert not env.dorado_mock.objects["filesystem"]


async def test_bucket_lifecycle(env):
    dev, tenant = await env.device(), await env.tenant(obj=100)
    b = {
        "bucket_name": "acme-data",
        "quota_gb": 40,
        "storage_device_id": dev["id"],
        "tenant_id": tenant["id"],
    }
    bucket = await env.ok("POST", "/buckets", json=b)
    assert bucket["owner"] == "acme" and bucket["s3_endpoint"].endswith("/s3") and bucket["quota_gb"] == 40
    assert env.dorado_mock.objects["bucket"]["1"]["quota_bytes"] == 40 * 1024**3

    await env.err("POST", "/buckets", 409, json={**b, "bucket_name": "second", "quota_gb": 61})
    grown = await env.ok("PUT", f"/buckets/{bucket['id']}/quota", json={"quota_gb": 100})
    assert grown["quota_gb"] == 100
    await env.err("PUT", f"/buckets/{bucket['id']}/quota", 409, json={"quota_gb": 101})
    shrunk = await env.ok("PUT", f"/buckets/{bucket['id']}/quota", json={"quota_gb": 10})
    assert shrunk["quota_gb"] == 10

    await env.ok("DELETE", f"/buckets/{bucket['id']}", status=204)
    assert not env.dorado_mock.objects["bucket"]
    await env.err("POST", "/buckets", 422, json={**b, "bucket_name": "Bad_Name"})


async def test_s3_credentials_secret_is_not_audited(env):
    dev, tenant = await env.device(), await env.tenant()
    creds = await env.ok(
        "POST", "/buckets/credentials", json={"storage_device_id": dev["id"], "tenant_id": tenant["id"]}
    )
    assert creds["owner"] == "acme" and creds["access_key"].startswith("AK") and creds["secret_key"]
    log = await env.audit(action="s3.credentials")
    assert len(log) == 1 and creds["secret_key"] not in str(log) and creds["access_key"] not in str(log)


async def test_oceanprotect_has_no_object_storage(env):
    dev, tenant = await env.device("oceanprotect"), await env.tenant()
    await env.err(
        "POST",
        "/buckets",
        422,
        json={
            "bucket_name": "nope-bucket",
            "quota_gb": 1,
            "storage_device_id": dev["id"],
            "tenant_id": tenant["id"],
        },
    )
    await env.err(
        "POST", "/buckets/credentials", 422, json={"storage_device_id": dev["id"], "tenant_id": tenant["id"]}
    )
