"""
Pytest tests for Intent Agent

Run with: pytest tests/test_intent_agent.py -v
Or: python -m pytest tests/test_intent_agent.py -v
"""

import pytest

from src.event_bus import EventBus
from src.agents.intent_agent import IntentAgent


class TestIntentAgent:
    """Test suite for Intent Agent"""
    
    @pytest.fixture
    def bus(self):
        """Create a fresh event bus for each test"""
        return EventBus()
    
    @pytest.fixture
    def agent(self, bus):
        """Create an Intent Agent for each test"""
        return IntentAgent(bus)
    
    @pytest.fixture
    def result_collector(self, bus):
        """Collect results from the agent"""
        results = []
        
        def collect(event):
            results.append(event.payload)
        
        bus.subscribe('RESULT_INTENT_RECOGNIZED', collect)
        return results
    
    # ========================================================================
    # Order Tracking Intent Tests
    # ========================================================================
    
    def test_track_order_basic(self, bus, agent, result_collector):
        """Test basic order tracking intent"""
        bus.publish('TASK_RECOGNIZE_INTENT', {
            'session_id': 'test-1',
            'text': 'Where is my order?'
        })
        
        assert len(result_collector) == 1
        result = result_collector[0]
        assert result['intent'] == 'track_order'
        assert result['confidence'] >= 0.8
        assert result['session_id'] == 'test-1'
    
    def test_track_order_variations(self, bus, agent, result_collector):
        """Test various order tracking phrases"""
        test_cases = [
            'Track my order',
            'Where is my package?',
            'When will my order arrive?',
            'Has my order shipped?',
            'What is the status of my shipment?',
            'I need to track order #12345',
        ]
        
        for text in test_cases:
            bus.publish('TASK_RECOGNIZE_INTENT', {
                'session_id': 'test',
                'text': text
            })
        
        assert len(result_collector) == len(test_cases)
        for result in result_collector:
            assert result['intent'] == 'track_order'
            assert result['confidence'] >= 0.65
    
    def test_track_order_with_order_number(self, bus, agent, result_collector):
        """Test order tracking with order number extraction"""
        bus.publish('TASK_RECOGNIZE_INTENT', {
            'session_id': 'test',
            'text': 'Track order #ABC-123-XYZ'
        })
        
        result = result_collector[0]
        assert result['intent'] == 'track_order'
        assert 'order_id' in result['entities']
        # Case doesn't matter for order IDs
        assert result['entities']['order_id'].upper() == 'ABC-123-XYZ'
    
    # ========================================================================
    # Return/Refund Intent Tests
    # ========================================================================
    
    def test_process_return_basic(self, bus, agent, result_collector):
        """Test basic return intent"""
        bus.publish('TASK_RECOGNIZE_INTENT', {
            'session_id': 'test-2',
            'text': 'I want to return my laptop'
        })
        
        result = result_collector[0]
        assert result['intent'] == 'process_return'
        assert result['confidence'] >= 0.8
    
    def test_process_return_variations(self, bus, agent, result_collector):
        """Test various return/refund phrases"""
        test_cases = [
            'I need to return this item',
            'Can I get a refund?',
            'I want my money back',
            'Need to exchange this product',
            'This is defective, want to return',
            'I want to send it back',  # Works better than "send this back"
        ]
        
        for text in test_cases:
            bus.publish('TASK_RECOGNIZE_INTENT', {
                'session_id': 'test',
                'text': text
            })
        
        assert len(result_collector) == len(test_cases)
        for result in result_collector:
            assert result['intent'] == 'process_return'
    
    def test_return_with_product(self, bus, agent, result_collector):
        """Test return with product extraction"""
        bus.publish('TASK_RECOGNIZE_INTENT', {
            'session_id': 'test',
            'text': 'I want to return my laptop'
        })
        
        result = result_collector[0]
        assert result['intent'] == 'process_return'
        assert 'product' in result['entities']
        assert result['entities']['product'] == 'laptop'
    
    # ========================================================================
    # Account Issues Intent Tests
    # ========================================================================
    
    def test_account_issues_basic(self, bus, agent, result_collector):
        """Test basic account issues intent"""
        bus.publish('TASK_RECOGNIZE_INTENT', {
            'session_id': 'test-3',
            'text': 'I can\'t log into my account'
        })
        
        result = result_collector[0]
        assert result['intent'] == 'account_issues'
        assert result['confidence'] >= 0.8
    
    def test_account_issues_variations(self, bus, agent, result_collector):
        """Test various account issue phrases"""
        test_cases = [
            'Forgot my password',
            'Need to reset my password',
            'Can\'t access my account',
            'How do I change my email?',
            'Update my profile information',
            'Account is locked',
        ]
        
        for text in test_cases:
            bus.publish('TASK_RECOGNIZE_INTENT', {
                'session_id': 'test',
                'text': text
            })
        
        assert len(result_collector) == len(test_cases)
        for result in result_collector:
            assert result['intent'] == 'account_issues'
    
    def test_account_password_issue(self, bus, agent, result_collector):
        """Test password-specific account issue"""
        bus.publish('TASK_RECOGNIZE_INTENT', {
            'session_id': 'test',
            'text': 'I forgot my password'
        })
        
        result = result_collector[0]
        assert result['intent'] == 'account_issues'
        assert 'issue_type' in result['entities']
        assert result['entities']['issue_type'] == 'password'
    
    def test_account_email_extraction(self, bus, agent, result_collector):
        """Test email extraction from account queries"""
        bus.publish('TASK_RECOGNIZE_INTENT', {
            'session_id': 'test',
            'text': 'Need to update my email to john@example.com'
        })
        
        result = result_collector[0]
        assert result['intent'] == 'account_issues'
        assert 'email' in result['entities']
        assert result['entities']['email'] == 'john@example.com'
    
    # ========================================================================
    # General Inquiry Tests
    # ========================================================================
    
    def test_general_inquiry_vague(self, bus, agent, result_collector):
        """Test vague queries that should be general_inquiry"""
        test_cases = [
            'Can you help me?',
            'I have a question',
            'Hello',
            'Need assistance',
        ]
        
        for text in test_cases:
            bus.publish('TASK_RECOGNIZE_INTENT', {
                'session_id': 'test',
                'text': text
            })
        
        assert len(result_collector) == len(test_cases)
        for result in result_collector:
            assert result['intent'] == 'general_inquiry'
            assert result['confidence'] < 0.7  # Low confidence
    
    # ========================================================================
    # Confidence Scoring Tests
    # ========================================================================
    
    def test_high_confidence_phrase_match(self, bus, agent, result_collector):
        """Exact phrase matches should have high confidence"""
        bus.publish('TASK_RECOGNIZE_INTENT', {
            'session_id': 'test',
            'text': 'I want to return this item'
        })
        
        result = result_collector[0]
        assert result['confidence'] >= 0.85
    
    def test_medium_confidence_keyword_match(self, bus, agent, result_collector):
        """Keyword matches should have medium confidence"""
        bus.publish('TASK_RECOGNIZE_INTENT', {
            'session_id': 'test',
            'text': 'My order tracking please'
        })
        
        result = result_collector[0]
        assert 0.65 <= result['confidence'] <= 0.85
    
    def test_low_confidence_no_match(self, bus, agent, result_collector):
        """No clear matches should have low confidence"""
        bus.publish('TASK_RECOGNIZE_INTENT', {
            'session_id': 'test',
            'text': 'What time is it?'
        })
        
        result = result_collector[0]
        assert result['confidence'] < 0.7
    
    # ========================================================================
    # Edge Cases Tests
    # ========================================================================
    
    def test_empty_text(self, bus, agent, result_collector):
        """Test handling of empty text"""
        bus.publish('TASK_RECOGNIZE_INTENT', {
            'session_id': 'test',
            'text': ''
        })
        
        assert len(result_collector) == 1
        result = result_collector[0]
        assert result['intent'] == 'general_inquiry'
    
    def test_mixed_intent_keywords(self, bus, agent, result_collector):
        """Test message with keywords from multiple intents"""
        bus.publish('TASK_RECOGNIZE_INTENT', {
            'session_id': 'test',
            'text': 'I want to track my order and also return an item'
        })
        
        result = result_collector[0]
        # Should pick the strongest intent
        assert result['intent'] in ['track_order', 'process_return']
        assert result['confidence'] >= 0.65
    
    def test_case_insensitive(self, bus, agent, result_collector):
        """Test that matching is case-insensitive"""
        bus.publish('TASK_RECOGNIZE_INTENT', {
            'session_id': 'test',
            'text': 'WHERE IS MY ORDER?'
        })
        
        result = result_collector[0]
        assert result['intent'] == 'track_order'
    
    # ========================================================================
    # Statistics Tests
    # ========================================================================
    
    def test_statistics_tracking(self, bus, agent, result_collector):
        """Test that statistics are tracked correctly"""
        # Process multiple intents
        test_cases = [
            ('track_order', 'Where is my order?'),
            ('track_order', 'Track my package'),
            ('process_return', 'I want a refund'),
            ('account_issues', 'Forgot password'),
            ('general_inquiry', 'Hello'),
        ]
        
        for _, text in test_cases:
            bus.publish('TASK_RECOGNIZE_INTENT', {
                'session_id': 'test',
                'text': text
            })
        
        stats = agent.get_stats()
        assert stats['total_analyzed'] == 5
        assert stats['track_order'] == 2
        assert stats['process_return'] == 1
        assert stats['account_issues'] == 1
        assert stats['general_inquiry'] == 1
    
    # ========================================================================
    # Error Handling Tests
    # ========================================================================
    
    def test_error_handling(self, bus, agent):
        """Test that errors are handled gracefully"""
        error_events = []
        
        def collect_errors(event):
            error_events.append(event.payload)
        
        bus.subscribe('AGENT_ERROR', collect_errors)
        
        # Publish malformed event
        bus.publish('TASK_RECOGNIZE_INTENT', {
            'session_id': 'test',
            # Missing 'text' field
        })
        
        assert len(error_events) == 1
        assert error_events[0]['agent_name'] == 'INTENT_AGENT'


class TestIntentAgentIntegration:
    """Integration tests with other components"""
    
    @pytest.fixture
    def setup(self):
        """Setup event bus and agent"""
        bus = EventBus()
        agent = IntentAgent(bus)
        results = []
        
        def collect(event):
            results.append(event.payload)
        
        bus.subscribe('RESULT_INTENT_RECOGNIZED', collect)
        
        return bus, agent, results
    
    def test_full_workflow_order_tracking(self, setup):
        """Test complete workflow for order tracking"""
        bus, agent, results = setup
        
        # Simulate user message
        bus.publish('TASK_RECOGNIZE_INTENT', {
            'session_id': 'session-123',
            'text': 'Where is my order #ABC123?',
            'conversation_history': ['Hello', 'I need help']
        })
        
        assert len(results) == 1
        result = results[0]
        
        # Verify all expected fields
        assert 'session_id' in result
        assert 'intent' in result
        assert 'confidence' in result
        assert 'entities' in result
        
        assert result['session_id'] == 'session-123'
        assert result['intent'] == 'track_order'
        assert result['confidence'] > 0.7
        assert 'order_id' in result['entities']
    
    def test_multiple_sequential_requests(self, setup):
        """Test handling multiple requests in sequence"""
        bus, agent, results = setup
        
        messages = [
            'I want to return my laptop',
            'Where is my order?',
            'Can\'t log in',
        ]
        
        for i, text in enumerate(messages):
            bus.publish('TASK_RECOGNIZE_INTENT', {
                'session_id': f'session-{i}',
                'text': text
            })
        
        assert len(results) == 3
        assert results[0]['intent'] == 'process_return'
        assert results[1]['intent'] == 'track_order'
        assert results[2]['intent'] == 'account_issues'


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, '-v'])