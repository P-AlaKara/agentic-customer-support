from src.api.gateway import bus, store


def test_operator_queue_takeover_respond_and_end_flow(client):
    session_id = 'session-operator-e2e'
    context = store.get_or_create(session_id, customer_email='ops@example.com')
    context.escalate('MANUAL_REQUEST')
    context.add_message('USER', 'Please connect me to a human')

    bus.publish('TASK_ESCALATE', {
        'session_id': session_id,
        'reason': 'MANUAL_REQUEST',
        'priority': 'HIGH'
    })

    queue_response = client.get('/operator/queue')
    assert queue_response.status_code == 200
    queue_payload = queue_response.json()
    assert queue_payload['queue_size'] == 1
    assert queue_payload['queue'][0]['session_id'] == session_id

    takeover_response = client.post('/operator/takeover', json={
        'session_id': session_id,
        'operator_id': 'op-42',
        'operator_name': 'Taylor'
    })
    assert takeover_response.status_code == 200
    assert takeover_response.json()['assigned'] is True

    details = client.get(f'/operator/session/{session_id}')
    assert details.status_code == 200
    details_payload = details.json()
    assert details_payload['controlled_by'] == 'OPERATOR'
    assert details_payload['operator_id'] == 'op-42'

    respond = client.post(f'/operator/respond/{session_id}', params={'message': 'I can help with this now.'})
    assert respond.status_code == 200
    assert respond.json()['status'] == 'success'

    end = client.post(f'/operator/end/{session_id}')
    assert end.status_code == 200
    assert end.json()['status'] == 'success'
