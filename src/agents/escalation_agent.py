"""
Escalation Agent

Manages escalation of conversations to human operators when:
- Sentiment is negative/angry (Gate 1 failure)
- Intent confidence is low (Gate 2 failure)
- Business Process Agent cannot handle the query (Gate 3 failure)
- Any agent reports an error

Design Decisions:
- Maintains an in-memory queue of escalated sessions
- Updates session status in Context Store
- Can assign to specific operators (future: load balancing)
- Publishes notifications for UI/monitoring
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from collections import deque

# Flexible imports
try:
    from ..event_bus import EventBus, Event
except (ImportError, ValueError):
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from event_bus import EventBus, Event


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class EscalationAgent:
    """
    Escalation Agent for managing human operator handoffs.
    
    Responsibilities:
    1. Receive escalation requests from Coordinator or other agents
    2. Maintain escalation queue
    3. Assign to available operators (future: load balancing)
    4. Update session status
    5. Notify UI/monitoring systems
    
    Escalation Reasons:
    - NEGATIVE_SENTIMENT_* : Angry/frustrated customer
    - LOW_INTENT_CONFIDENCE : Can't understand user request
    - AGENT_ERROR_* : Technical failure
    - MANUAL_REQUEST : User asked for human
    - BPA_CANNOT_HANDLE : Business logic requires human
    """
    
    def __init__(self, event_bus: EventBus):
        """
        Initialize the Escalation Agent.
        
        Args:
            event_bus: The event bus for communication
        """
        self.bus = event_bus
        
        # Escalation queue (FIFO)
        self.queue = deque()
        
        # Active escalations (session_id -> escalation_info)
        self.active_escalations: Dict[str, Dict[str, Any]] = {}
        
        # Statistics
        self.stats = {
            'total_escalations': 0,
            'queued': 0,
            'assigned': 0,
            'resolved': 0,
            'by_reason': {}
        }
        
        # Subscribe to events
        self.bus.subscribe('TASK_ESCALATE', self.handle_escalation)
        self.bus.subscribe('OPERATOR_AVAILABLE', self.assign_to_operator)
        self.bus.subscribe('ESCALATION_RESOLVED', self.handle_resolution)
        
        logger.info("EscalationAgent initialized")
    
    def handle_escalation(self, event: Event):
        """
        Handle an escalation request.
        
        Expected event payload:
        {
            'session_id': str,
            'reason': str,
            'details': dict (optional),
            'context': dict (optional),
            'priority': str (optional) - 'HIGH', 'NORMAL', 'LOW'
        }
        
        Publishes:
        - RESULT_ESCALATION_COMPLETE: Confirmation that escalation is queued
        - NOTIFICATION_OPERATOR: Alert to operator dashboard
        """
        try:
            payload = event.payload
            session_id = payload['session_id']
            reason = payload['reason']
            details = payload.get('details', {})
            context = payload.get('context')
            priority = payload.get('priority', 'NORMAL')
            
            logger.warning(f"[Escalation Agent] Escalating session {session_id}")
            logger.info(f"[Escalation Agent] Reason: {reason}")
            logger.debug(f"[Escalation Agent] Details: {details}")
            
            # Create escalation record
            escalation = {
                'session_id': session_id,
                'reason': reason,
                'details': details,
                'context': context,
                'priority': priority,
                'escalated_at': datetime.utcnow().isoformat(),
                'status': 'QUEUED',
                'operator_id': None,
                'assigned_at': None,
                'queue_position': len(self.queue) + 1
            }
            
            # Add to queue (priority queue in future)
            if priority == 'HIGH':
                self.queue.appendleft(escalation)  # Front of queue
            else:
                self.queue.append(escalation)  # Back of queue
            
            # Track in active escalations
            self.active_escalations[session_id] = escalation
            
            # Update statistics
            self.stats['total_escalations'] += 1
            self.stats['queued'] += 1
            self.stats['by_reason'][reason] = self.stats['by_reason'].get(reason, 0) + 1
            
            # Publish confirmation
            self.bus.publish('RESULT_ESCALATION_COMPLETE', {
                'session_id': session_id,
                'status': 'QUEUED',
                'queue_position': escalation['queue_position'],
                'estimated_wait_time': self._estimate_wait_time()
            })
            
            # Notify operator dashboard
            self.bus.publish('NOTIFICATION_OPERATOR', {
                'type': 'NEW_ESCALATION',
                'session_id': session_id,
                'reason': reason,
                'priority': priority,
                'queue_size': len(self.queue)
            })
            
            logger.info(f"[Escalation Agent] Session {session_id} queued (position: {escalation['queue_position']})")
            
        except Exception as e:
            logger.error(f"[Escalation Agent] Error handling escalation: {e}", exc_info=True)
    
    def assign_to_operator(self, event: Event):
        """
        Assign the next escalation to an available operator.
        
        Expected event payload:
        {
            'operator_id': str,
            'operator_name': str (optional),
            'skills': list (optional) - for future skill-based routing
        }
        
        Publishes:
        - RESULT_OPERATOR_ASSIGNED: Confirmation with session details
        """
        try:
            payload = event.payload
            operator_id = payload['operator_id']
            operator_name = payload.get('operator_name', operator_id)
            
            if not self.queue:
                logger.info(f"[Escalation Agent] No escalations in queue for operator {operator_name}")
                self.bus.publish('RESULT_OPERATOR_ASSIGNED', {
                    'operator_id': operator_id,
                    'assigned': False,
                    'reason': 'QUEUE_EMPTY'
                })
                return
            
            # Get next escalation from queue
            escalation = self.queue.popleft()
            session_id = escalation['session_id']
            
            # Update escalation record
            escalation['status'] = 'ASSIGNED'
            escalation['operator_id'] = operator_id
            escalation['assigned_at'] = datetime.utcnow().isoformat()
            
            # Update statistics
            self.stats['queued'] -= 1
            self.stats['assigned'] += 1
            
            logger.info(f"[Escalation Agent] Assigned session {session_id} to operator {operator_name}")
            
            # Publish assignment confirmation
            self.bus.publish('RESULT_OPERATOR_ASSIGNED', {
                'operator_id': operator_id,
                'operator_name': operator_name,
                'assigned': True,
                'session_id': session_id,
                'reason': escalation['reason'],
                'context': escalation.get('context'),
                'escalated_at': escalation['escalated_at'],
                'wait_time_seconds': self._calculate_wait_time(escalation)
            })
            
        except Exception as e:
            logger.error(f"[Escalation Agent] Error assigning to operator: {e}", exc_info=True)
    
    def handle_resolution(self, event: Event):
        """
        Handle resolution of an escalation.
        
        Expected event payload:
        {
            'session_id': str,
            'operator_id': str,
            'resolution_notes': str (optional),
            'satisfaction_rating': int (optional)
        }
        """
        try:
            payload = event.payload
            session_id = payload['session_id']
            operator_id = payload['operator_id']
            
            if session_id not in self.active_escalations:
                logger.warning(f"[Escalation Agent] Session {session_id} not found in active escalations")
                return
            
            escalation = self.active_escalations.pop(session_id)
            
            # Update statistics
            self.stats['assigned'] -= 1
            self.stats['resolved'] += 1
            
            logger.info(f"[Escalation Agent] Session {session_id} resolved by operator {operator_id}")
            
            # Publish resolution confirmation
            self.bus.publish('RESULT_ESCALATION_RESOLVED', {
                'session_id': session_id,
                'operator_id': operator_id,
                'total_time_seconds': self._calculate_total_time(escalation),
                'resolution_notes': payload.get('resolution_notes')
            })
            
        except Exception as e:
            logger.error(f"[Escalation Agent] Error handling resolution: {e}", exc_info=True)
    
    def _estimate_wait_time(self) -> int:
        """
        Estimate wait time in seconds based on queue size.
        
        Simple formula: queue_size * avg_handling_time
        Future: Use historical data
        """
        avg_handling_time = 300  # 5 minutes per escalation (placeholder)
        return len(self.queue) * avg_handling_time
    
    def _calculate_wait_time(self, escalation: Dict[str, Any]) -> int:
        """Calculate actual wait time from escalation to assignment"""
        if not escalation.get('assigned_at'):
            return 0
        
        escalated = datetime.fromisoformat(escalation['escalated_at'])
        assigned = datetime.fromisoformat(escalation['assigned_at'])
        return int((assigned - escalated).total_seconds())
    
    def _calculate_total_time(self, escalation: Dict[str, Any]) -> int:
        """Calculate total time from escalation to resolution"""
        escalated = datetime.fromisoformat(escalation['escalated_at'])
        resolved = datetime.utcnow()
        return int((resolved - escalated).total_seconds())
    
    def get_queue_status(self) -> Dict[str, Any]:
        """Get current queue status"""
        return {
            'queue_size': len(self.queue),
            'active_escalations': len(self.active_escalations),
            'estimated_wait_time': self._estimate_wait_time(),
            'queue': [
                {
                    'session_id': esc['session_id'],
                    'reason': esc['reason'],
                    'priority': esc['priority'],
                    'position': i + 1
                }
                for i, esc in enumerate(self.queue)
            ]
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get escalation statistics"""
        return {
            **self.stats,
            'queue_size': len(self.queue),
            'active_escalations': len(self.active_escalations)
        }


if __name__ == "__main__":
    """Standalone test"""
    print("=== Escalation Agent Test ===\n")
    
    from event_bus import EventBus
    
    bus = EventBus()
    agent = EscalationAgent(bus)
    
    # Subscribe to results
    def handle_result(event):
        print(f"Result: {event.payload}")
    
    bus.subscribe('RESULT_ESCALATION_COMPLETE', handle_result)
    bus.subscribe('RESULT_OPERATOR_ASSIGNED', handle_result)
    
    # Test 1: Escalate a session
    print("Test 1: Escalating session")
    bus.publish('TASK_ESCALATE', {
        'session_id': 'session-001',
        'reason': 'NEGATIVE_SENTIMENT_ANGRY',
        'details': {'sentiment': 'ANGRY', 'confidence': 0.95},
        'priority': 'HIGH'
    })
    
    # Test 2: Another escalation
    print("\nTest 2: Escalating another session")
    bus.publish('TASK_ESCALATE', {
        'session_id': 'session-002',
        'reason': 'LOW_INTENT_CONFIDENCE',
        'details': {'confidence': 0.55}
    })
    
    # Test 3: Assign to operator
    print("\nTest 3: Operator becomes available")
    bus.publish('OPERATOR_AVAILABLE', {
        'operator_id': 'op-123',
        'operator_name': 'Alice'
    })
    
    print("\n=== Queue Status ===")
    print(agent.get_queue_status())
    
    print("\n=== Statistics ===")
    print(agent.get_stats())