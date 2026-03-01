from src.api.gateway import store


def test_operator_respond_requires_operator_control(client):
    session_id = 'session-not-controlled'
    store.get_or_create(session_id)

    response = client.post(f'/operator/respond/{session_id}', params={'message': 'should fail'})
    assert response.status_code == 409
    assert 'not controlled by an operator' in response.json()['detail'].lower()
