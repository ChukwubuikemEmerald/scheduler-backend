import hashlib
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import text

from database import engine
from main import app

client = TestClient(app)


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------


def unique_suffix():
    return uuid4().hex[:8]


def make_user_payload(prefix="testuser"):
    suffix = unique_suffix()

    return {
        "username": f"{prefix}_{suffix}",
        "email": f"{prefix}_{suffix}@example.com",
        "password": "TestPassword123",
    }


def make_course_payload():
    suffix = unique_suffix()

    return {
        "code": f"TST{suffix.upper()}",
        "name": f"Test Course {suffix}",
    }


def cleanup_user(user_id):
    with engine.begin() as connection:
        connection.execute(
            text("""
                DELETE FROM refresh_tokens
                WHERE user_id = :user_id
            """),
            {"user_id": user_id},
        )

        connection.execute(
            text("""
                DELETE FROM users
                WHERE id = :user_id
            """),
            {"user_id": user_id},
        )


# ---------------------------------------------------------
# Fixtures
# ---------------------------------------------------------


@pytest.fixture
def course():
    payload = make_course_payload()

    response = client.post(
        "/api/courses",
        json=payload,
    )

    assert response.status_code == 201

    course_data = response.json()

    yield course_data

    client.delete(f"/api/courses/{course_data['id']}")


@pytest.fixture(scope="session")
def normal_user():
    payload = make_user_payload("normal")

    response = client.post(
        "/api/auth/register",
        json=payload,
    )

    assert response.status_code == 201

    user_data = response.json()

    yield {
        **user_data,
        "password": payload["password"],
    }

    cleanup_user(user_data["id"])


@pytest.fixture(scope="session")
def normal_tokens(normal_user):
    response = client.post(
        "/api/auth/login",
        json={
            "username": normal_user["username"],
            "password": normal_user["password"],
        },
    )

    assert response.status_code == 200

    return response.json()


@pytest.fixture(scope="session")
def admin_user():
    payload = make_user_payload("admin")

    response = client.post(
        "/api/auth/register",
        json=payload,
    )

    assert response.status_code == 201

    user_data = response.json()

    with engine.begin() as connection:
        connection.execute(
            text("""
                UPDATE users
                SET role = 'admin'
                WHERE id = :user_id
            """),
            {"user_id": user_data["id"]},
        )

    yield {
        **user_data,
        "password": payload["password"],
    }

    cleanup_user(user_data["id"])


@pytest.fixture(scope="session")
def admin_tokens(admin_user):
    response = client.post(
        "/api/auth/login",
        json={
            "username": admin_user["username"],
            "password": admin_user["password"],
        },
    )

    assert response.status_code == 200

    return response.json()


@pytest.fixture(scope="session")
def refresh_user():
    payload = make_user_payload("refresh")

    response = client.post(
        "/api/auth/register",
        json=payload,
    )

    assert response.status_code == 201

    user_data = response.json()

    yield {
        **user_data,
        "password": payload["password"],
    }

    cleanup_user(user_data["id"])


@pytest.fixture(scope="session")
def refresh_user_tokens(refresh_user):
    response = client.post(
        "/api/auth/login",
        json={
            "username": refresh_user["username"],
            "password": refresh_user["password"],
        },
    )

    assert response.status_code == 200

    return response.json()


@pytest.fixture(scope="session")
def logout_user():
    payload = make_user_payload("logout")

    response = client.post(
        "/api/auth/register",
        json=payload,
    )

    assert response.status_code == 201

    user_data = response.json()

    yield {
        **user_data,
        "password": payload["password"],
    }

    cleanup_user(user_data["id"])


@pytest.fixture(scope="session")
def logout_user_tokens(logout_user):
    response = client.post(
        "/api/auth/login",
        json={
            "username": logout_user["username"],
            "password": logout_user["password"],
        },
    )

    assert response.status_code == 200

    return response.json()


# =========================================================
# COURSE CRUD
# =========================================================


def test_get_courses(course):
    response = client.get("/api/courses")

    assert response.status_code == 200
    assert isinstance(response.json(), list)

    assert any(item["id"] == course["id"] for item in response.json())


def test_get_course(course):
    response = client.get(f"/api/courses/{course['id']}")

    assert response.status_code == 200

    data = response.json()

    assert data["id"] == course["id"]
    assert data["code"] == course["code"]
    assert data["name"] == course["name"]


def test_get_course_not_found():
    response = client.get("/api/courses/999999999")

    assert response.status_code == 404


def test_create_course():
    payload = make_course_payload()

    response = client.post(
        "/api/courses",
        json=payload,
    )

    assert response.status_code == 201

    data = response.json()

    assert data["code"] == payload["code"]
    assert data["name"] == payload["name"]
    assert "id" in data

    client.delete(f"/api/courses/{data['id']}")


def test_create_duplicate_course(course):
    response = client.post(
        "/api/courses",
        json={
            "code": course["code"],
            "name": "Another Course",
        },
    )

    assert response.status_code == 409


def test_put_course(course):
    new_payload = {
        "code": f"PUT{unique_suffix().upper()}",
        "name": "Updated Course",
    }

    response = client.put(
        f"/api/courses/{course['id']}",
        json=new_payload,
    )

    assert response.status_code == 200

    data = response.json()

    assert data["id"] == course["id"]
    assert data["code"] == new_payload["code"]
    assert data["name"] == new_payload["name"]


def test_put_course_not_found():
    response = client.put(
        "/api/courses/999999999",
        json={
            "code": "DOESNOTEXIST",
            "name": "Missing Course",
        },
    )

    assert response.status_code == 404


def test_put_duplicate_code(course):
    second_payload = make_course_payload()

    second_response = client.post(
        "/api/courses",
        json=second_payload,
    )

    assert second_response.status_code == 201

    second_course = second_response.json()

    try:
        response = client.put(
            f"/api/courses/{course['id']}",
            json={
                "code": second_course["code"],
                "name": "Duplicate Code",
            },
        )

        assert response.status_code == 409

    finally:
        client.delete(f"/api/courses/{second_course['id']}")


def test_patch_course(course):
    response = client.patch(
        f"/api/courses/{course['id']}",
        json={
            "name": "Partially Updated Course",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["name"] == "Partially Updated Course"
    assert data["code"] == course["code"]


def test_patch_course_both_fields(course):
    new_code = f"PAT{unique_suffix().upper()}"

    response = client.patch(
        f"/api/courses/{course['id']}",
        json={
            "code": new_code,
            "name": "Fully Patched Course",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["code"] == new_code
    assert data["name"] == "Fully Patched Course"


def test_patch_empty_body(course):
    response = client.patch(
        f"/api/courses/{course['id']}",
        json={},
    )

    assert response.status_code == 400


def test_patch_course_not_found():
    response = client.patch(
        "/api/courses/999999999",
        json={
            "name": "Does Not Exist",
        },
    )

    assert response.status_code == 404


def test_patch_sql_injection_is_treated_as_data(course):
    malicious_text = "'; DROP TABLE courses; --"

    response = client.patch(
        f"/api/courses/{course['id']}",
        json={
            "name": malicious_text,
        },
    )

    assert response.status_code == 200
    assert response.json()["name"] == malicious_text


def test_delete_course():
    payload = make_course_payload()

    create_response = client.post(
        "/api/courses",
        json=payload,
    )

    assert create_response.status_code == 201

    course_id = create_response.json()["id"]

    response = client.delete(f"/api/courses/{course_id}")

    assert response.status_code == 204

    response = client.get(f"/api/courses/{course_id}")

    assert response.status_code == 404


def test_delete_course_not_found():
    response = client.delete("/api/courses/999999999")

    assert response.status_code == 404


def test_delete_course_with_schedule():
    payload = make_course_payload()

    create_response = client.post(
        "/api/courses",
        json=payload,
    )

    assert create_response.status_code == 201

    course_id = create_response.json()["id"]

    with engine.begin() as connection:
        schedule_result = connection.execute(
            text("""
                INSERT INTO schedules (
                    course_id,
                    title,
                    description,
                    start_time,
                    end_time,
                    location,
                    type
                )
                VALUES (
                    :course_id,
                    'Test Schedule',
                    NULL,
                    '08:00',
                    '10:00',
                    'TEST',
                    'Lecture'
                )
                RETURNING id
            """),
            {"course_id": course_id},
        )

        schedule_id = schedule_result.scalar_one()

    try:
        response = client.delete(f"/api/courses/{course_id}")

        # Your current implementation returns 400.
        assert response.status_code == status.HTTP_409_CONFLICT

    finally:
        with engine.begin() as connection:
            connection.execute(
                text("""
                    DELETE FROM schedules
                    WHERE id = :schedule_id
                """),
                {"schedule_id": schedule_id},
            )

            connection.execute(
                text("""
                    DELETE FROM courses
                    WHERE id = :course_id
                """),
                {"course_id": course_id},
            )


# =========================================================
# REGISTRATION
# =========================================================


def test_register_user():
    payload = make_user_payload("register")

    response = client.post(
        "/api/auth/register",
        json=payload,
    )

    assert response.status_code == 201

    data = response.json()

    assert data["username"] == payload["username"]
    assert data["email"] == payload["email"]

    assert "password" not in data
    assert "hashed_password" not in data

    with engine.begin() as connection:
        result = connection.execute(
            text("""
                SELECT hashed_password
                FROM users
                WHERE id = :user_id
            """),
            {"user_id": data["id"]},
        )

        row = result.mappings().first()

    assert row is not None
    assert row["hashed_password"] != payload["password"]

    cleanup_user(data["id"])


def test_register_duplicate_username():
    payload = make_user_payload("duplicate")

    first_response = client.post(
        "/api/auth/register",
        json=payload,
    )

    assert first_response.status_code == 201

    user_id = first_response.json()["id"]

    try:
        duplicate_response = client.post(
            "/api/auth/register",
            json={
                "username": payload["username"],
                "email": f"other_{unique_suffix()}@example.com",
                "password": payload["password"],
            },
        )

        assert duplicate_response.status_code == 409

    finally:
        cleanup_user(user_id)


def test_register_invalid_password():
    payload = make_user_payload("invalid")

    payload["password"] = "123"

    response = client.post(
        "/api/auth/register",
        json=payload,
    )

    assert response.status_code == 422


# =========================================================
# LOGIN
# =========================================================


def test_login_success(normal_tokens):
    assert "access_token" in normal_tokens
    assert "refresh_token" in normal_tokens
    assert normal_tokens["token_type"] == "bearer"


def test_login_invalid_credentials(normal_user):
    response = client.post(
        "/api/auth/login",
        json={
            # Deliberately SQL-looking input.
            "username": "' OR 1=1 --",
            "password": normal_user["password"],
        },
    )

    assert response.status_code == 401


# =========================================================
# AUTHENTICATION
# =========================================================


def test_me_requires_authentication():
    response = client.get("/api/users/me")

    assert response.status_code == 401


def test_me_with_valid_token(normal_user, normal_tokens):
    response = client.get(
        "/api/users/me",
        headers={"Authorization": (f"Bearer {normal_tokens['access_token']}")},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["id"] == normal_user["id"]
    assert data["username"] == normal_user["username"]
    assert data["email"] == normal_user["email"]


def test_me_with_fake_token():
    response = client.get(
        "/api/users/me",
        headers={"Authorization": "Bearer fake.token.here"},
    )

    assert response.status_code == 401


# =========================================================
# AUTHORIZATION
# =========================================================


def test_admin_endpoint_forbidden_for_normal_user(normal_tokens):
    response = client.get(
        "/api/admin/test",
        headers={"Authorization": (f"Bearer {normal_tokens['access_token']}")},
    )

    assert response.status_code == 403


def test_admin_endpoint_allowed_for_admin(admin_tokens):
    response = client.get(
        "/api/admin/test",
        headers={"Authorization": (f"Bearer {admin_tokens['access_token']}")},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["user"]["role"] == "admin"


# =========================================================
# REFRESH TOKEN
# =========================================================


def test_refresh_token_rotation(refresh_user_tokens):
    old_refresh_token = refresh_user_tokens["refresh_token"]

    response = client.post(
        "/api/auth/refresh",
        json={"refresh_token": old_refresh_token},
    )

    assert response.status_code == 200

    new_tokens = response.json()

    assert "access_token" in new_tokens
    assert "refresh_token" in new_tokens

    new_refresh_token = new_tokens["refresh_token"]

    assert new_refresh_token != old_refresh_token

    # Old refresh token should now be unusable.
    replay_response = client.post(
        "/api/auth/refresh",
        json={"refresh_token": old_refresh_token},
    )

    assert replay_response.status_code == 401

    # New refresh token should work.
    new_refresh_response = client.post(
        "/api/auth/refresh",
        json={"refresh_token": new_refresh_token},
    )

    assert new_refresh_response.status_code == 200


def test_expired_refresh_token(refresh_user_tokens):
    refresh_token = refresh_user_tokens["refresh_token"]

    token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()

    with engine.begin() as connection:
        connection.execute(
            text("""
                UPDATE refresh_tokens
                SET expires_at = NOW() - INTERVAL '1 minute'
                WHERE token_hash = :token_hash
            """),
            {"token_hash": token_hash},
        )

    response = client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_token},
    )

    assert response.status_code == 401


# =========================================================
# LOGOUT
# =========================================================


def test_logout(logout_user_tokens):
    refresh_token = logout_user_tokens["refresh_token"]

    response = client.post(
        "/api/auth/logout",
        json={"refresh_token": refresh_token},
    )

    assert response.status_code == 204

    # Token should no longer be usable.
    refresh_response = client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_token},
    )

    assert refresh_response.status_code == 401


# =========================================================
# CORS
# =========================================================


def test_cors_preflight():
    response = client.options(
        "/api/courses",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


# =========================================================
# RATE LIMITING
# =========================================================


def test_login_rate_limit():
    rate_client = TestClient(
        app,
        client=("10.250.0.1", 50000),
    )

    payload = {
        "username": "rate_limit_test_user",
        "password": "WrongPassword123",
    }

    responses = []

    for _ in range(6):
        response = rate_client.post(
            "/api/auth/login",
            json=payload,
        )

        responses.append(response)

    for response in responses[:5]:
        assert response.status_code == 401

    assert responses[5].status_code == 429
