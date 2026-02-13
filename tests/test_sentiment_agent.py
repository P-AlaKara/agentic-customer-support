"""
Pytest tests for Sentiment Agent

Run with: pytest tests/test_sentiment_agent.py -v
"""

import pytest

from src.event_bus import EventBus
from src.agents.sentiment_agent import SentimentAgent


class TestSentimentAgent:
    """Test suite for Sentiment Agent"""
    
    @pytest.fixture
    def bus(self):
        """Create a fresh event bus for each test"""
        return EventBus()
    
    @pytest.fixture
    def agent(self, bus):
        """Create a Sentiment Agent for each test"""
        return SentimentAgent(bus)
    
    @pytest.fixture
    def result_collector(self, bus):
        """Collect results from the agent"""
        results = []
        
        def collect(event):
            results.append(event.payload)
        
        bus.subscribe('RESULT_SENTIMENT_RECOGNIZED', collect)
        return results
    
    # ========================================================================
    # Angry Sentiment Tests
    # ========================================================================
    
    def test_angry_basic(self, bus, agent, result_collector):
        """Test basic angry sentiment detection"""
        bus.publish('TASK_RECOGNIZE_SENTIMENT', {
            'session_id': 'test-1',
            'text': 'I am so angry about this!'
        })
        
        result = result_collector[0]
        assert result['sentiment'] == 'ANGRY'
        assert result['confidence'] >= 0.85
    
    def test_angry_variations(self, bus, agent, result_collector):
        """Test various angry phrases"""
        test_cases = [
            'This is terrible!',
            'I hate this product',
            'This is the worst service ever',
            'Absolutely unacceptable',
            'What a scam!',
        ]
        
        for text in test_cases:
            bus.publish('TASK_RECOGNIZE_SENTIMENT', {
                'session_id': 'test',
                'text': text
            })
        
        for result in result_collector:
            assert result['sentiment'] == 'ANGRY'
    
    def test_angry_with_caps_and_exclamations(self, bus, agent, result_collector):
        """Test that caps and exclamations boost angry confidence"""
        bus.publish('TASK_RECOGNIZE_SENTIMENT', {
            'session_id': 'test',
            'text': 'THIS IS TERRIBLE!!!'
        })
        
        result = result_collector[0]
        assert result['sentiment'] == 'ANGRY'
        assert result['confidence'] >= 0.90
    
    # ========================================================================
    # Negative Sentiment Tests
    # ========================================================================
    
    def test_negative_basic(self, bus, agent, result_collector):
        """Test basic negative sentiment"""
        bus.publish('TASK_RECOGNIZE_SENTIMENT', {
            'session_id': 'test',
            'text': 'This product is really bad and disappointing'
        })
        
        result = result_collector[0]
        assert result['sentiment'] == 'NEGATIVE'
        assert result['confidence'] >= 0.65  # Single keyword = lower confidence
    
    def test_negative_variations(self, bus, agent, result_collector):
        """Test various negative phrases"""
        test_cases = [
            'I am disappointed with this',
            'This is broken',
            'This is a problem',
            'Unhappy with the service',
        ]
        
        for text in test_cases:
            bus.publish('TASK_RECOGNIZE_SENTIMENT', {
                'session_id': 'test',
                'text': text
            })
        
        for result in result_collector:
            # All should be negative or neutral (not positive)
            assert result['sentiment'] in ['NEGATIVE', 'NEUTRAL']
    
    # ========================================================================
    # Urgent Sentiment Tests
    # ========================================================================
    
    def test_urgent_basic(self, bus, agent, result_collector):
        """Test urgent sentiment detection"""
        bus.publish('TASK_RECOGNIZE_SENTIMENT', {
            'session_id': 'test',
            'text': 'I need help ASAP!'
        })
        
        result = result_collector[0]
        assert result['sentiment'] == 'URGENT'
        assert result['confidence'] >= 0.80
    
    def test_urgent_variations(self, bus, agent, result_collector):
        """Test various urgent phrases"""
        test_cases = [
            'This is urgent!',
            'Need this immediately',
            'Right now please',
            'Emergency situation',
        ]
        
        for text in test_cases:
            bus.publish('TASK_RECOGNIZE_SENTIMENT', {
                'session_id': 'test',
                'text': text
            })
        
        for result in result_collector:
            assert result['sentiment'] == 'URGENT'
    
    # ========================================================================
    # Positive Sentiment Tests
    # ========================================================================
    
    def test_positive_basic(self, bus, agent, result_collector):
        """Test positive sentiment detection"""
        bus.publish('TASK_RECOGNIZE_SENTIMENT', {
            'session_id': 'test',
            'text': 'Thank you so much! This is great!'
        })
        
        result = result_collector[0]
        assert result['sentiment'] == 'POSITIVE'
        assert result['confidence'] >= 0.80
    
    def test_positive_variations(self, bus, agent, result_collector):
        """Test various positive phrases"""
        test_cases = [
            'Excellent service',
            'I love this product',
            'Perfect, exactly what I needed',
            'You are very helpful',
        ]
        
        for text in test_cases:
            bus.publish('TASK_RECOGNIZE_SENTIMENT', {
                'session_id': 'test',
                'text': text
            })
        
        for result in result_collector:
            assert result['sentiment'] == 'POSITIVE'
    
    # ========================================================================
    # Neutral Sentiment Tests
    # ========================================================================
    
    def test_neutral_basic(self, bus, agent, result_collector):
        """Test neutral sentiment detection"""
        bus.publish('TASK_RECOGNIZE_SENTIMENT', {
            'session_id': 'test',
            'text': 'I want to return my laptop'
        })
        
        result = result_collector[0]
        assert result['sentiment'] == 'NEUTRAL'
        assert result['confidence'] >= 0.80
    
    def test_neutral_variations(self, bus, agent, result_collector):
        """Test various neutral phrases"""
        test_cases = [
            'Where is my order?',
            'I need to track my package',
            'Can you help me with my account?',
            'What is the status of my shipment?',
        ]
        
        for text in test_cases:
            bus.publish('TASK_RECOGNIZE_SENTIMENT', {
                'session_id': 'test',
                'text': text
            })
        
        for result in result_collector:
            assert result['sentiment'] == 'NEUTRAL'
    
    # ========================================================================
    # Negation Tests
    # ========================================================================
    
    def test_negation_flips_sentiment(self, bus, agent, result_collector):
        """Test that negation words flip sentiment"""
        bus.publish('TASK_RECOGNIZE_SENTIMENT', {
            'session_id': 'test',
            'text': 'This is not bad at all'
        })
        
        result = result_collector[0]
        # Should detect negation and handle accordingly
        assert result['sentiment'] in ['POSITIVE', 'NEUTRAL']
        assert 'has_negation' in result['details']
        assert result['details']['has_negation'] is True
    
    # ========================================================================
    # Intensifier Tests
    # ========================================================================
    
    def test_intensifiers_boost_confidence(self, bus, agent, result_collector):
        """Test that intensifiers increase confidence"""
        # Without intensifier
        bus.publish('TASK_RECOGNIZE_SENTIMENT', {
            'session_id': 'test-1',
            'text': 'This is terrible'
        })
        
        # With intensifier
        bus.publish('TASK_RECOGNIZE_SENTIMENT', {
            'session_id': 'test-2',
            'text': 'This is very terrible'
        })
        
        assert len(result_collector) == 2
        conf_without = result_collector[0]['confidence']
        conf_with = result_collector[1]['confidence']
        
        assert conf_with > conf_without
        assert result_collector[1]['details']['has_intensifier'] is True
    
    # ========================================================================
    # Edge Cases
    # ========================================================================
    
    def test_empty_text(self, bus, agent, result_collector):
        """Test handling of empty text"""
        bus.publish('TASK_RECOGNIZE_SENTIMENT', {
            'session_id': 'test',
            'text': ''
        })
        
        result = result_collector[0]
        assert result['sentiment'] == 'NEUTRAL'
    
    def test_special_characters(self, bus, agent, result_collector):
        """Test handling of special characters"""
        bus.publish('TASK_RECOGNIZE_SENTIMENT', {
            'session_id': 'test',
            'text': '!@#$%^&*()'
        })
        
        result = result_collector[0]
        assert 'sentiment' in result
    
    def test_mixed_sentiment(self, bus, agent, result_collector):
        """Test text with mixed sentiment"""
        bus.publish('TASK_RECOGNIZE_SENTIMENT', {
            'session_id': 'test',
            'text': 'Great product but terrible service'
        })
        
        result = result_collector[0]
        # Should pick the strongest sentiment
        assert result['sentiment'] in ['POSITIVE', 'NEGATIVE', 'ANGRY']
    
    # ========================================================================
    # Confidence Scoring Tests
    # ========================================================================
    
    def test_confidence_ranges(self, bus, agent, result_collector):
        """Test that confidence is always in valid range"""
        test_cases = [
            'terrible',
            'great',
            'okay',
            'urgent',
            'angry',
        ]
        
        for text in test_cases:
            bus.publish('TASK_RECOGNIZE_SENTIMENT', {
                'session_id': 'test',
                'text': text
            })
        
        for result in result_collector:
            assert 0.0 <= result['confidence'] <= 1.0
    
    # ========================================================================
    # Statistics Tests
    # ========================================================================
    
    def test_statistics_tracking(self, bus, agent, result_collector):
        """Test that statistics are tracked correctly"""
        test_cases = [
            'I am angry',
            'This is great',
            'Need help',
            'Very angry',
            'Neutral message',
        ]
        
        for text in test_cases:
            bus.publish('TASK_RECOGNIZE_SENTIMENT', {
                'session_id': 'test',
                'text': text
            })
        
        stats = agent.get_stats()
        assert stats['total_analyzed'] == 5
        assert stats['angry'] >= 1
        assert stats['positive'] >= 1
    
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
        bus.publish('TASK_RECOGNIZE_SENTIMENT', {
            'session_id': 'test',
            # Missing 'text' field
        })
        
        assert len(error_events) == 1
        assert error_events[0]['agent_name'] == 'SENTIMENT_AGENT'


class TestSentimentAgentIntegration:
    """Integration tests"""
    
    def test_full_workflow(self):
        """Test complete workflow"""
        bus = EventBus()
        agent = SentimentAgent(bus)
        results = []
        
        bus.subscribe('RESULT_SENTIMENT_RECOGNIZED', lambda e: results.append(e.payload))
        
        bus.publish('TASK_RECOGNIZE_SENTIMENT', {
            'session_id': 'session-123',
            'text': 'This is terrible!'
        })
        
        assert len(results) == 1
        result = results[0]
        
        assert result['session_id'] == 'session-123'
        assert result['sentiment'] == 'ANGRY'
        assert 'confidence' in result
        assert 'details' in result


if __name__ == "__main__":
    pytest.main([__file__, '-v'])