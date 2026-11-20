"""Organization deletion is owner-only and isolated to the caller's tenant."""
import pytest
from fastapi.testclient import TestClient
from backend import database
from backend.models import User, Tenant, TenantMembership, AuthSession, InstituteInvite
from backend.tests.conftest import csrf_headers, finish_signup, signup_payload
from backend.tests.test_member_management import _create_owner_and_invite


def _join(client, code, otp_codes):
    assert client.post('/api/v1/auth/signup', json=signup_payload(
        email='member@northstar.com', invite_code=code,
    ), headers=csrf_headers(client)).status_code == 202
    finish_signup(client, 'member@northstar.com', otp_codes)


@pytest.mark.parametrize('role', ['admin', 'editor', 'viewer'])
def test_nonowner_cannot_delete_organization(client, otp_codes, role):
    _, code = _create_owner_and_invite(client, otp_codes, role=role)
    with TestClient(client.app) as member:
        _join(member, code, otp_codes)
        response = member.request('DELETE', '/api/v1/auth/workspace',
            json={'confirmation': 'Northstar', 'current_password': 'language10'}, headers=csrf_headers(member))
        assert response.status_code == 403
        assert member.get('/api/v1/auth/me').status_code == 200
    with database.SessionLocal() as db:
        assert db.query(User).count() == 2


def test_wrong_confirmation_preserves_organization(client, otp_codes):
    _create_owner_and_invite(client, otp_codes)
    response = client.request('DELETE', '/api/v1/auth/workspace',
        json={'confirmation': 'Wrong name', 'current_password': 'language10'}, headers=csrf_headers(client))
    assert response.status_code == 400
    assert client.get('/api/v1/auth/me').status_code == 200
    with database.SessionLocal() as db:
        assert db.query(Tenant).count() == 1


def test_owner_deletes_accounts_and_sessions_but_preserves_other_tenant(client, otp_codes):
    _, code = _create_owner_and_invite(client, otp_codes)
    with TestClient(client.app) as member, TestClient(client.app) as other:
        _join(member, code, otp_codes)
        assert other.post('/api/v1/auth/signup', json=signup_payload(
            email='other@elsewhere.com', workspace_type='business', workspace_name='Elsewhere',
        ), headers=csrf_headers(other)).status_code == 202
        finish_signup(other, 'other@elsewhere.com', otp_codes)
        response = client.request('DELETE', '/api/v1/auth/workspace',
            json={'confirmation': 'Northstar', 'current_password': 'language10'}, headers=csrf_headers(client))
        assert response.status_code == 204
        assert client.get('/api/v1/auth/me').status_code == 401
        assert member.get('/api/v1/auth/me').status_code == 401
        assert other.get('/api/v1/auth/me').status_code == 200
    with database.SessionLocal() as db:
        assert [u.email for u in db.query(User).all()] == ['other@elsewhere.com']
        assert db.query(Tenant).one().name == 'Elsewhere'
        assert db.query(TenantMembership).count() == 1
        assert db.query(AuthSession).count() == 1
        assert db.query(InstituteInvite).count() == 0


def test_deletion_removes_persisted_sources_history_and_local_cache(client, otp_codes, tmp_path, monkeypatch):
    from apps.api.session import session_manager
    from apps.core import workspaces
    from backend.models import WorkspaceRecord, WorkspaceGuardrail, WorkspaceHistoryRecord, MemoryDeletion
    _create_owner_and_invite(client, otp_codes)
    with database.SessionLocal() as db:
        owner_id = db.query(User).one().id
        source = tmp_path / 'source.db'
        source.write_bytes(b'local data')
        db.add(WorkspaceRecord(id='delete-source', tenant_id=owner_id, name='Sales',
            source_type='file', source_ref=str(source)))
        db.flush()
        db.add(WorkspaceGuardrail(workspace_id='delete-source', settings_json='{}'))
        db.add(WorkspaceHistoryRecord(workspace_id='delete-source', entry_id='query', payload_json='{}'))
        db.commit()
    monkeypatch.setattr(workspaces, 'LOCAL_WORKSPACE_DIRECTORY', tmp_path)
    monkeypatch.setattr(session_manager, 'save', lambda *args, **kwargs: None)
    monkeypatch.setattr(session_manager, 'workspaces', {'delete-source': {
        'id': 'delete-source', 'owner_user_id': owner_id, 'source_type': 'file', 'database_uri': f'sqlite:///{source}',
    }})
    for name, value in [('query_history', {'delete-source': [{'id': 'query'}]}),
                        ('workspace_guardrails', {'delete-source': {}}),
                        ('schema_snapshots', {'schema_delete-source': 'schema'}),
                        ('active_proposals', {'delete-source': {}})]:
        monkeypatch.setattr(session_manager, name, value)
    response = client.request('DELETE', '/api/v1/auth/workspace',
        json={'confirmation': 'Northstar', 'current_password': 'language10'}, headers=csrf_headers(client))
    assert response.status_code == 204
    assert not source.exists()
    assert not session_manager.workspaces
    assert not session_manager.query_history
    assert not session_manager.workspace_guardrails
    assert not session_manager.schema_snapshots
    assert not session_manager.active_proposals
    with database.SessionLocal() as db:
        for model in [WorkspaceRecord, WorkspaceGuardrail, WorkspaceHistoryRecord]:
            assert db.query(model).count() == 0
        assert db.query(MemoryDeletion).filter_by(tenant_id=owner_id, workspace_id='delete-source').count() == 1


def test_personal_owner_cannot_delete_an_organization(client, otp_codes):
    assert client.post('/api/v1/auth/signup', json=signup_payload(email='personal@example.com'),
        headers=csrf_headers(client)).status_code == 202
    finish_signup(client, 'personal@example.com', otp_codes)
    response = client.request('DELETE', '/api/v1/auth/workspace',
        json={'confirmation': 'Personal workspace', 'current_password': 'language10'}, headers=csrf_headers(client))
    assert response.status_code == 403


def test_school_owner_can_delete_school(client, otp_codes):
    assert client.post('/api/v1/auth/signup', json=signup_payload(
        email='school@example.com', workspace_type='institution', workspace_name='Northstar School',
    ), headers=csrf_headers(client)).status_code == 202
    finish_signup(client, 'school@example.com', otp_codes)
    response = client.request('DELETE', '/api/v1/auth/workspace',
        json={'confirmation': 'Northstar School', 'current_password': 'language10'}, headers=csrf_headers(client))
    assert response.status_code == 204
    with database.SessionLocal() as db:
        assert db.query(User).count() == 0
        assert db.query(Tenant).count() == 0


def test_organization_delete_requires_csrf(client, otp_codes):
    _create_owner_and_invite(client, otp_codes)
    response = client.request('DELETE', '/api/v1/auth/workspace', json={'confirmation': 'Northstar', 'current_password': 'language10'})
    assert response.status_code == 403
    with database.SessionLocal() as db:
        assert db.query(User).count() == 1


@pytest.mark.parametrize('role', ['admin', 'editor', 'viewer'])
def test_only_owner_can_delete_a_data_source(client, otp_codes, monkeypatch, role):
    from backend.main import create_app
    from apps.api.session import session_manager
    _, code = _create_owner_and_invite(client, otp_codes, role=role)
    with database.SessionLocal() as db:
        owner_id = db.query(User).one().id
    with TestClient(create_app(include_product=True)) as member:
        _join(member, code, otp_codes)
        monkeypatch.setattr(session_manager, 'workspaces', {'school-source': {
            'id': 'school-source', 'name': 'School1', 'owner_user_id': owner_id,
        }})
        response = member.delete('/api/workspaces/school-source', headers=csrf_headers(member))
        assert response.status_code == 403
        assert session_manager.get_workspace('school-source') is not None


def _delete(client, **body):
    return client.request('DELETE', '/api/v1/auth/workspace', json=body, headers=csrf_headers(client))


def test_wrong_password_preserves_organization_with_generic_error(client, otp_codes):
    _create_owner_and_invite(client, otp_codes)
    wrong_password = _delete(client, confirmation='Northstar', current_password='not-my-password')
    wrong_name = _delete(client, confirmation='Wrong name', current_password='language10')
    assert wrong_password.status_code == wrong_name.status_code == 400
    # One answer for either mistake, so the response never confirms which part was right.
    assert wrong_password.json()['detail'] == wrong_name.json()['detail']
    assert client.get('/api/v1/auth/me').status_code == 200
    with database.SessionLocal() as db:
        assert db.query(Tenant).count() == 1


@pytest.mark.parametrize('body', [{'confirmation': 'Northstar'},
                                  {'confirmation': 'Northstar', 'current_password': ''}])
def test_missing_password_is_rejected(client, otp_codes, body):
    _create_owner_and_invite(client, otp_codes)
    assert _delete(client, **body).status_code == 422
    with database.SessionLocal() as db:
        assert db.query(User).count() == 1


def test_organization_delete_is_rate_limited(client, otp_codes):
    from backend.config import settings
    _create_owner_and_invite(client, otp_codes)
    for _ in range(settings.auth_rate_limit):
        assert _delete(client, confirmation='Northstar', current_password='guess-1234').status_code == 400
    blocked = _delete(client, confirmation='Northstar', current_password='language10')
    assert blocked.status_code == 429
    assert 'retry-after' in blocked.headers
    with database.SessionLocal() as db:
        assert db.query(Tenant).count() == 1


def test_success_clears_translation_cache_and_logs_counts_only(client, otp_codes, caplog):
    import logging
    from apps.api.routers import query
    _, code = _create_owner_and_invite(client, otp_codes)
    with TestClient(client.app) as member:
        _join(member, code, otp_codes)
    query._translation_cache[('hi', 'derived-label')] = 'translated label'
    with caplog.at_level(logging.INFO, logger='backend.routers.auth'):
        response = _delete(client, confirmation='Northstar', current_password='language10')
    assert response.status_code == 204
    assert not query._translation_cache
    audit = [r.getMessage() for r in caplog.records if 'organization_deleted' in r.getMessage()]
    assert len(audit) == 1
    assert 'accounts_removed=2' in audit[0]
    assert 'Northstar' not in audit[0] and '@' not in audit[0]
