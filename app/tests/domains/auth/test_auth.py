BASE = "/api/v1/auth"
CREDENTIALS = {
    "email": "b@example.com",
    "password": "passw0rd",
}


async def _register_and_login(client) -> dict:
    await client.post(f"{BASE}/register", json=CREDENTIALS)
    resp = await client.post(f"{BASE}/login", json=CREDENTIALS)
    return resp.json()

# ---------------- 注册 ----------------
async def test_register_success(client):
    resp = await client.post(f"{BASE}/register", json=CREDENTIALS)
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == CREDENTIALS["email"]
    assert "password" not in body and "hashed_password" not in body


async def test_register_duplicate_email_rejected(client):
    await client.post(f"{BASE}/register", json=CREDENTIALS)
    resp = await client.post(f"{BASE}/register", json=CREDENTIALS)
    assert resp.status_code == 409
    assert resp.json()["code"] == "conflict"


async def test_register_weak_password_rejected(client):
    resp = await client.post(
        f"{BASE}/register", json={"email": "b@example.com", "password": "allletters"}
    )
    assert resp.status_code == 422


# ---------------- 登录 ----------------
async def test_login_success(client):
    await client.post(f"{BASE}/register", json=CREDENTIALS)
    resp = await client.post(f"{BASE}/login", json=CREDENTIALS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] and body["refresh_token"] and body["token_type"] == "bearer"


async def test_login_wrong_password(client):
    await client.post(f"{BASE}/register", json=CREDENTIALS)
    resp = await client.post(f"{BASE}/login", json={**CREDENTIALS, "password": "wrongpass1"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_credentials"


async def test_login_nonexistent_user_same_error_as_wrong_password(client):
    resp = await client.post(f"{BASE}/login", json={"email": "nobody@example.com", "password": "x1234567"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_credentials"


# ---------------- 受保护接口 ----------------
async def test_me_requires_token(client):
    resp = await client.get(f"{BASE}/me")
    print(resp.json())
    assert resp.status_code == 401


async def test_me_with_valid_token(client):
    tokens = await _register_and_login(client)
    resp = await client.get(f"{BASE}/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == CREDENTIALS["email"]


async def test_me_with_garbage_token(client):
    resp = await client.get(f"{BASE}/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_token"


async def test_refresh_token_cannot_access_protected_endpoint(client):
    tokens = await _register_and_login(client)
    resp = await client.get(f"{BASE}/me", headers={"Authorization": f"Bearer {tokens['refresh_token']}"})
    assert resp.status_code == 401


# ---------------- 刷新与轮换 ----------------
async def test_refresh_issues_new_pair(client):
    tokens = await _register_and_login(client)
    resp = await client.post(f"{BASE}/refresh", json={"refresh_token": tokens["refresh_token"]})
    print(resp.json())
    assert resp.status_code == 200
    new_tokens = resp.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]
    assert new_tokens["access_token"] != tokens["access_token"]


async def test_refresh_reuse_is_detected_and_family_revoked(client):
    tokens = await _register_and_login(client)
    old_refresh = tokens["refresh_token"]

    first = await client.post(f"{BASE}/refresh", json={"refresh_token": old_refresh})
    new_refresh = first.json()["refresh_token"]

    second = await client.post(f"{BASE}/refresh", json={"refresh_token": old_refresh})
    assert second.status_code == 401
    assert second.json()["code"] == "token_reuse_detected"

    third = await client.post(f"{BASE}/refresh", json={"refresh_token": new_refresh})
    assert third.status_code == 401
    assert third.json()["code"] == "invalid_token"


async def test_refresh_with_unknown_token(client):
    resp = await client.post(f"{BASE}/refresh", json={"refresh_token": "does-not-exist"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_token"


# ---------------- 登出 ----------------
async def test_logout_revokes_refresh_token(client):
    tokens = await _register_and_login(client)
    assert (await client.post(f"{BASE}/logout", json={"refresh_token": tokens["refresh_token"]})).status_code == 204
    resp = await client.post(f"{BASE}/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401


async def test_logout_is_idempotent(client):
    tokens = await _register_and_login(client)
    await client.post(f"{BASE}/logout", json={"refresh_token": tokens["refresh_token"]})
    assert (await client.post(f"{BASE}/logout", json={"refresh_token": tokens["refresh_token"]})).status_code == 204


async def test_logout_all_devices(client):
    tokens_1 = await _register_and_login(client)
    tokens_2 = (await client.post(f"{BASE}/login", json=CREDENTIALS)).json()

    await client.post(f"{BASE}/logout-all", headers={"Authorization": f"Bearer {tokens_1['access_token']}"})

    assert (await client.post(f"{BASE}/refresh", json={"refresh_token": tokens_1["refresh_token"]})).status_code == 401
    assert (await client.post(f"{BASE}/refresh", json={"refresh_token": tokens_2["refresh_token"]})).status_code == 401
