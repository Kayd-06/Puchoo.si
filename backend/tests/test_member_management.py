"""Existing invite-code collaboration must expire and revoke access safely."""

from datetime import timedelta

from fastapi.testclient import TestClient

from backend import database
from backend.models import InstituteInvite, TenantMembership, User
from backend.security import utcnow
from backend.tests.conftest import csrf_headers, finish_signup, signup_payload


def _create_owner_and_invite(client, otp_codes, *, role="editor"):
    owner_email = "owner@northstar.com"
    response = client.post(
        "/api/v1/auth/signup",
        json=signup_payload(
            full_name="Northstar Owner",
            email=owner_email,
            workspace_type="business",
            workspace_name="Northstar",
        ),
        headers=csrf_headers(client),
    )
    assert response.status_code == 202
    finish_signup(client, owner_email, otp_codes)
    invite = client.post(
        "/api/v1/auth/workspace/invite",
        json={"role": role},
        headers=csrf_headers(client),
    )
    assert invite.status_code == 200
    return owner_email, invite.json()["code"]


def test_expired_workspace_invite_cannot_join_a_tenant(client, otp_codes):
    """Removing the expiry check would allow old leaked role codes to grant access."""

    _owner_email, code = _create_owner_and_invite(client, otp_codes)
    db = database.SessionLocal()
    try:
        invite = db.query(InstituteInvite).one()
        invite.expires_at = utcnow() - timedelta(days=1)
        db.commit()
    finally:
        db.close()

    joined = client.post(
        "/api/v1/auth/signup",
        json=signup_payload(email="expired-invite@northstar.com", invite_code=code),
        headers=csrf_headers(client),
    )

    assert joined.status_code == 400
    assert joined.json()["detail"] == "That workspace invite code is invalid."


def test_owner_can_change_member_role_and_removed_member_loses_session(client, otp_codes):
    """Removing either role authorization or session revocation breaks tenant safety."""

    _owner_email, code = _create_owner_and_invite(client, otp_codes)
    with TestClient(client.app) as member_client:
        joined = member_client.post(
            "/api/v1/auth/signup",
            json=signup_payload(
                full_name="Northstar Member",
                email="member@northstar.com",
                invite_code=code,
            ),
            headers=csrf_headers(member_client),
        )
        assert joined.status_code == 202
        finish_signup(member_client, "member@northstar.com", otp_codes)

        db = database.SessionLocal()
        try:
            member_id = db.query(User.id).filter_by(email="member@northstar.com").scalar()
        finally:
            db.close()
        assert member_id
        db = database.SessionLocal()
        try:
            membership = db.query(TenantMembership).filter_by(user_id=member_id).one()
            assert membership.role == "editor"
        finally:
            db.close()

        changed = client.patch(
            f"/api/v1/auth/workspace/members/{member_id}",
            json={"role": "viewer"},
            headers=csrf_headers(client),
        )
        assert changed.status_code == 200
        assert changed.json()["role"] == "viewer"
        db = database.SessionLocal()
        try:
            assert db.query(TenantMembership).filter_by(user_id=member_id).one().role == "viewer"
        finally:
            db.close()
        assert member_client.get("/api/v1/auth/me").status_code == 200

        removed = client.delete(
            f"/api/v1/auth/workspace/members/{member_id}",
            headers=csrf_headers(client),
        )
        assert removed.status_code == 204
        assert member_client.get("/api/v1/auth/me").status_code == 401


def test_member_cannot_manage_another_member(client, otp_codes):
    """A non-admin must receive 403 even for a member in the same tenant."""

    _owner_email, code = _create_owner_and_invite(client, otp_codes, role="viewer")
    with TestClient(client.app) as member_client:
        joined = member_client.post(
            "/api/v1/auth/signup",
            json=signup_payload(email="viewer@northstar.com", invite_code=code),
            headers=csrf_headers(member_client),
        )
        assert joined.status_code == 202
        finish_signup(member_client, "viewer@northstar.com", otp_codes)

        db = database.SessionLocal()
        try:
            owner_id = db.query(User.id).filter_by(email="owner@northstar.com").scalar()
        finally:
            db.close()

        response = member_client.patch(
            f"/api/v1/auth/workspace/members/{owner_id}",
            json={"role": "editor"},
            headers=csrf_headers(member_client),
        )
        assert response.status_code == 403
        assert member_client.get("/api/v1/auth/workspace/members").status_code == 403

def test_removing_member_revokes_all_workspace_invites(client, otp_codes):
    """When a member is removed, all open invite codes for the workspace are retired."""
    _owner_email, viewer_code = _create_owner_and_invite(client, otp_codes, role="viewer")

    # Create an editor code too
    editor_invite = client.post(
        "/api/v1/auth/workspace/invite",
        json={"role": "editor"},
        headers=csrf_headers(client),
    )
    assert editor_invite.status_code == 200
    editor_code = editor_invite.json()["code"]

    # Member joins using the viewer code
    with TestClient(client.app) as member_client:
        joined = member_client.post(
            "/api/v1/auth/signup",
            json=signup_payload(
                full_name="Northstar Member",
                email="member@northstar.com",
                invite_code=viewer_code,
            ),
            headers=csrf_headers(member_client),
        )
        assert joined.status_code == 202
        finish_signup(member_client, "member@northstar.com", otp_codes)

        db = database.SessionLocal()
        try:
            member_id = db.query(User.id).filter_by(email="member@northstar.com").scalar()
        finally:
            db.close()

        # The owner removes the member
        removed = client.delete(
            f"/api/v1/auth/workspace/members/{member_id}",
            headers=csrf_headers(client),
        )
        assert removed.status_code == 204

    # Now verify both invites are revoked and cannot be used
    with TestClient(client.app) as another_member_client:
        viewer_join = another_member_client.post(
            "/api/v1/auth/signup",
            json=signup_payload(email="another1@northstar.com", invite_code=viewer_code),
            headers=csrf_headers(another_member_client),
        )
        assert viewer_join.status_code == 400

        editor_join = another_member_client.post(
            "/api/v1/auth/signup",
            json=signup_payload(email="another2@northstar.com", invite_code=editor_code),
            headers=csrf_headers(another_member_client),
        )
        assert editor_join.status_code == 400


def test_removing_member_is_safe_when_their_personal_tenant_already_exists(client, otp_codes):
    """A pre-existing personal Tenant row (backfill or earlier removal) must not cause a 500."""

    from backend.models import Tenant

    _owner_email, code = _create_owner_and_invite(client, otp_codes)
    with TestClient(client.app) as member_client:
        assert member_client.post(
            "/api/v1/auth/signup",
            json=signup_payload(full_name="Repeat Member", email="repeat@northstar.com", invite_code=code),
            headers=csrf_headers(member_client),
        ).status_code == 202
        finish_signup(member_client, "repeat@northstar.com", otp_codes)

    db = database.SessionLocal()
    try:
        member_id = db.query(User.id).filter_by(email="repeat@northstar.com").scalar()
        db.add(Tenant(id=member_id, owner_user_id=member_id, name="Personal workspace", workspace_type="personal"))
        db.commit()
    finally:
        db.close()

    removed = client.delete(f"/api/v1/auth/workspace/members/{member_id}", headers=csrf_headers(client))
    assert removed.status_code == 204

    db = database.SessionLocal()
    try:
        assert db.query(Tenant).filter_by(id=member_id).count() == 1
        membership = db.query(TenantMembership).filter_by(user_id=member_id).one()
        assert (membership.tenant_id, membership.role) == (member_id, "owner")
    finally:
        db.close()


def test_role_change_only_touches_membership_in_the_admins_tenant(client, otp_codes):
    """The membership lookup is scoped to the caller's tenant, not just the user id."""

    owner_email, code = _create_owner_and_invite(client, otp_codes)
    with TestClient(client.app) as member_client:
        assert member_client.post(
            "/api/v1/auth/signup",
            json=signup_payload(full_name="Scoped Member", email="scoped@northstar.com", invite_code=code),
            headers=csrf_headers(member_client),
        ).status_code == 202
        finish_signup(member_client, "scoped@northstar.com", otp_codes)

    db = database.SessionLocal()
    try:
        owner_id = db.query(User.id).filter_by(email=owner_email).scalar()
        member_id = db.query(User.id).filter_by(email="scoped@northstar.com").scalar()
        # Simulate a stale membership row that points at a different tenant.
        membership = db.query(TenantMembership).filter_by(user_id=member_id).one()
        from backend.models import Tenant

        db.add(Tenant(id="other-tenant", owner_user_id=member_id, name="Other", workspace_type="business"))
        db.flush()
        membership.tenant_id = "other-tenant"
        membership.role = "editor"
        db.commit()
    finally:
        db.close()

    changed = client.patch(
        f"/api/v1/auth/workspace/members/{member_id}",
        json={"role": "viewer"},
        headers=csrf_headers(client),
    )
    assert changed.status_code == 200
    db = database.SessionLocal()
    try:
        stale = db.query(TenantMembership).filter_by(user_id=member_id).one()
        assert (stale.tenant_id, stale.role) == ("other-tenant", "editor")
        assert owner_id
    finally:
        db.close()


def test_admin_cannot_change_own_or_other_member_roles(client, otp_codes):
    """An admin's management capability must not authorize role reassignment."""
    _owner_email, admin_code = _create_owner_and_invite(client, otp_codes, role="admin")
    editor_code = client.post(
        "/api/v1/auth/workspace/invite", json={"role": "editor"}, headers=csrf_headers(client),
    ).json()["code"]
    with TestClient(client.app) as admin_client:
        assert admin_client.post(
            "/api/v1/auth/signup",
            json=signup_payload(email="admin@northstar.com", invite_code=admin_code),
            headers=csrf_headers(admin_client),
        ).status_code == 202
        finish_signup(admin_client, "admin@northstar.com", otp_codes)
        with TestClient(client.app) as editor_client:
            assert editor_client.post(
                "/api/v1/auth/signup",
                json=signup_payload(email="editor@northstar.com", invite_code=editor_code),
                headers=csrf_headers(editor_client),
            ).status_code == 202
            finish_signup(editor_client, "editor@northstar.com", otp_codes)
        with database.SessionLocal() as db:
            admin_id = db.query(User.id).filter_by(email="admin@northstar.com").scalar()
            editor_id = db.query(User.id).filter_by(email="editor@northstar.com").scalar()
        for member_id, next_role in [(admin_id, "viewer"), (editor_id, "admin")]:
            response = admin_client.patch(
                f"/api/v1/auth/workspace/members/{member_id}",
                json={"role": next_role}, headers=csrf_headers(admin_client),
            )
            assert response.status_code == 403
        with database.SessionLocal() as db:
            assert db.get(User, admin_id).workspace_role == "admin"
            assert db.get(User, editor_id).workspace_role == "editor"
            assert db.query(TenantMembership).filter_by(user_id=admin_id).one().role == "admin"
            assert db.query(TenantMembership).filter_by(user_id=editor_id).one().role == "editor"


def test_only_owner_can_create_admin_invites_or_remove_admins(client, otp_codes):
    """Admins could otherwise grant or revoke admin access without the owner."""
    _owner_email, first_admin_code = _create_owner_and_invite(client, otp_codes, role="admin")
    with TestClient(client.app) as admin_client:
        assert admin_client.post(
            "/api/v1/auth/signup",
            json=signup_payload(email="admin@northstar.com", invite_code=first_admin_code),
            headers=csrf_headers(admin_client),
        ).status_code == 202
        finish_signup(admin_client, "admin@northstar.com", otp_codes)
        second_admin_code = client.post(
            "/api/v1/auth/workspace/invite", json={"role": "admin"}, headers=csrf_headers(client),
        ).json()["code"]
        with TestClient(client.app) as second_client:
            assert second_client.post(
                "/api/v1/auth/signup",
                json=signup_payload(email="second-admin@northstar.com", invite_code=second_admin_code),
                headers=csrf_headers(second_client),
            ).status_code == 202
            finish_signup(second_client, "second-admin@northstar.com", otp_codes)
        with database.SessionLocal() as db:
            second_id = db.query(User.id).filter_by(email="second-admin@northstar.com").scalar()

        denied_invite = admin_client.post(
            "/api/v1/auth/workspace/invite", json={"role": "admin"}, headers=csrf_headers(admin_client),
        )
        assert denied_invite.status_code == 403
        assert admin_client.post(
            "/api/v1/auth/workspace/invite", json={"role": "editor"}, headers=csrf_headers(admin_client),
        ).status_code == 200

        denied_removal = admin_client.delete(
            f"/api/v1/auth/workspace/members/{second_id}", headers=csrf_headers(admin_client),
        )
        assert denied_removal.status_code == 403
        with database.SessionLocal() as db:
            assert db.get(User, second_id).workspace_role == "admin"

        assert client.delete(
            f"/api/v1/auth/workspace/members/{second_id}", headers=csrf_headers(client),
        ).status_code == 204
        with database.SessionLocal() as db:
            assert db.get(User, second_id).workspace_owner_id is None
