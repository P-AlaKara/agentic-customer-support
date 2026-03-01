def test_chat_session_round_trip_and_history(client):
    response = client.post('/chat', json={'message': 'Hello there', 'customer_email': 'user@example.com'})
    assert response.status_code == 200
    payload = response.json()

    assert payload['session_id']
    assert payload['status'] in {'responded', 'processing', 'escalated'}

    history = client.get(f"/chat/{payload['session_id']}")
    assert history.status_code == 200
    history_payload = history.json()

    assert history_payload['session_id'] == payload['session_id']
    assert any(msg['sender'] == 'USER' for msg in history_payload['messages'])
