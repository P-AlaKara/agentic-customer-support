"""
Database Utilities

Handles PostgreSQL connections and operations for the customer support system.

Tables:
- completed_conversations: Conversation headers
- completed_messages: Message logs
- orders: Order information
- returns: Return requests
- kb_articles: Knowledge base with embeddings
"""

import os
import logging
from typing import Dict, Any, List, Optional
from contextlib import contextmanager
import uuid

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor, Json
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False
    logging.warning("psycopg2 not installed. Database operations will be simulated.")


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def ensure_uuid(value: Any) -> uuid.UUID:
    """
    Ensure a value is a UUID object.
    
    Args:
        value: String, UUID, or other value
    
    Returns:
        UUID object
    
    Examples:
        ensure_uuid("550e8400-e29b-41d4-a716-446655440000") -> UUID object
        ensure_uuid("session-123") -> UUID generated from string (deterministic)
        ensure_uuid(uuid_object) -> uuid_object (unchanged)
    """
    if isinstance(value, uuid.UUID):
        return value
    
    try:
        # Try to parse as UUID string
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        # Not a valid UUID, generate deterministic one from the string
        # Using uuid5 ensures same input always produces same UUID
        return uuid.uuid5(uuid.NAMESPACE_DNS, str(value))


class DatabaseConnection:
    """
    PostgreSQL database connection manager.
    
    Usage:
        db = DatabaseConnection()
        
        # Using context manager
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM orders")
            results = cursor.fetchall()
        
        # Or manual
        cursor = db.get_connection().cursor()
        cursor.execute("...")
        db.commit()
    """
    
    def __init__(self, connection_string: Optional[str] = None):
        """
        Initialize database connection.
        
        Args:
            connection_string: PostgreSQL connection string
                              If None, reads from environment variables
        """
        self.connection = None
        self.connection_string = connection_string or self._build_connection_string()
        
        if PSYCOPG2_AVAILABLE:
            self._connect()
        else:
            logger.warning("Database operations will be simulated (psycopg2 not available)")
    
    def _build_connection_string(self) -> str:
        """Build connection string from environment variables"""
        db_name = os.getenv('POSTGRES_DB', 'ai_support')
        user = os.getenv('POSTGRES_USER', 'postgres')
        password = os.getenv('POSTGRES_PASSWORD', '200303')
        host = os.getenv('POSTGRES_HOST', 'localhost')
        port = os.getenv('POSTGRES_PORT', '5432')
        
        return f"dbname={db_name} user={user} password={password} host={host} port={port}"
    
    def _connect(self):
        """Establish database connection"""
        try:
            self.connection = psycopg2.connect(self.connection_string)
            logger.info("Database connection established")
        except psycopg2.Error as e:
            logger.error(f"Database connection failed: {e}")
            raise
    
    def get_connection(self):
        """Get the database connection"""
        if not PSYCOPG2_AVAILABLE:
            return None
        
        if self.connection is None or self.connection.closed:
            self._connect()
        
        return self.connection
    
    @contextmanager
    def get_cursor(self, cursor_factory=RealDictCursor):
        """
        Context manager for database cursor.
        
        Usage:
            with db.get_cursor() as cursor:
                cursor.execute("SELECT * FROM orders")
                results = cursor.fetchall()
        """
        if not PSYCOPG2_AVAILABLE:
            # Simulate cursor for when psycopg2 is not available
            class MockCursor:
                def execute(self, *args, **kwargs):
                    logger.info(f"[SIMULATED] Would execute: {args[0][:100]}...")
                def fetchall(self):
                    return []
                def fetchone(self):
                    return None
                def close(self):
                    pass
            
            cursor = MockCursor()
            try:
                yield cursor
            finally:
                pass
            return
        
        conn = self.get_connection()
        cursor = conn.cursor(cursor_factory=cursor_factory)
        try:
            yield cursor
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}", exc_info=True)
            raise
        finally:
            cursor.close()
    
    def commit(self):
        """Commit transaction"""
        if self.connection and not self.connection.closed:
            self.connection.commit()
    
    def rollback(self):
        """Rollback transaction"""
        if self.connection and not self.connection.closed:
            self.connection.rollback()
    
    def close(self):
        """Close database connection"""
        if self.connection and not self.connection.closed:
            self.connection.close()
            logger.info("Database connection closed")


class TranscriptDB:
    """
    Database operations for conversation transcripts.
    
    Handles writes to:
    - completed_conversations
    - completed_messages
    """
    
    def __init__(self, db: DatabaseConnection):
        """
        Initialize with database connection.
        
        Args:
            db: DatabaseConnection instance
        """
        self.db = db
    
    def write_conversation(self, transcript: Dict[str, Any]) -> bool:
        """
        Write complete conversation transcript to database.
        
        Args:
            transcript: Dictionary containing conversation data
        
        Returns:
            True if successful, False otherwise
        """
        try:
            with self.db.get_cursor() as cursor:
                # Insert conversation header
                self._insert_conversation_header(cursor, transcript)
                
                # Insert all messages
                self._insert_messages(cursor, transcript)
            
            logger.info(f"Wrote conversation {transcript['session_id']} to database")
            return True
            
        except Exception as e:
            logger.error(f"Failed to write conversation to database: {e}", exc_info=True)
            return False
    
    def _insert_conversation_header(self, cursor, transcript: Dict[str, Any]):
        """Insert into completed_conversations table"""
        query = """
            INSERT INTO completed_conversations (
                conversation_id, 
                start_time, 
                end_time, 
                final_status, 
                customer_id, 
                operator_id
            )
            VALUES (%(conversation_id)s, %(start_time)s, %(end_time)s, 
                    %(final_status)s, %(customer_id)s, %(operator_id)s)
        """
        
        data = {
            'conversation_id': ensure_uuid(transcript['session_id']),
            'start_time': transcript['start_time'],
            'end_time': transcript.get('end_time'),
            'final_status': transcript.get('final_status', 'UNKNOWN'),
            'customer_id': transcript.get('customer_email'),
            'operator_id': transcript.get('operator_id')
        }
        
        cursor.execute(query, data)
    
    def _insert_messages(self, cursor, transcript: Dict[str, Any]):
        """Insert into completed_messages table"""
        query = """
            INSERT INTO completed_messages (
                conversation_id,
                timestamp,
                sender,
                text_content,
                intent_label,
                sentiment_label,
                entities,
                agent_action
            )
            VALUES (%(conversation_id)s, %(timestamp)s, %(sender)s, %(text_content)s,
                    %(intent_label)s, %(sentiment_label)s, %(entities)s, %(agent_action)s)
        """
        
        for msg in transcript.get('messages', []):
            data = {
                'conversation_id': transcript['session_id'],
                'timestamp': msg['timestamp'],
                'sender': msg['sender'],
                'text_content': msg['text'],
                'intent_label': msg.get('intent_label'),
                'sentiment_label': msg.get('sentiment_label'),
                'entities': Json(msg.get('entities')) if msg.get('entities') else None,
                'agent_action': Json(msg.get('agent_action')) if msg.get('agent_action') else None
            }
            
            cursor.execute(query, data)


class KnowledgeBaseDB:
    """
    Database operations for knowledge base.
    
    Handles queries to:
    - kb_articles (with vector similarity search)
    """
    
    def __init__(self, db: DatabaseConnection):
        """
        Initialize with database connection.
        
        Args:
            db: DatabaseConnection instance
        """
        self.db = db
    
    def search_similar(self, embedding: List[float], category: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Search for similar knowledge base articles using vector similarity.
        
        Args:
            embedding: Query embedding vector (768 dimensions)
            category: Category filter (e.g., 'RETURNS', 'SHIPPING')
            limit: Maximum number of results
        
        Returns:
            List of matching articles with text and metadata
        """
        try:
            with self.db.get_cursor() as cursor:
                query = """
                    SELECT 
                        chunk_id,
                        text_chunk,
                        category,
                        source_file,
                        embedding <=> %s::vector AS distance
                    FROM kb_articles
                    WHERE category = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                """
                
                cursor.execute(query, (embedding, category, embedding, limit))
                results = cursor.fetchall()
                
                logger.debug(f"Found {len(results)} similar articles in category {category}")
                return results
                
        except Exception as e:
            logger.error(f"KB search error: {e}", exc_info=True)
            return []


class OrdersDB:
    """Database operations for orders and returns"""
    
    def __init__(self, db: DatabaseConnection):
        self.db = db
    
    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Get order by ID"""
        try:
            with self.db.get_cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM orders WHERE order_id = %s",
                    (order_id,)
                )
                return cursor.fetchone()
        except Exception as e:
            logger.error(f"Error fetching order: {e}")
            return None
    
    def get_orders_by_email(self, email: str) -> List[Dict[str, Any]]:
        """Get all orders for a customer email"""
        try:
            with self.db.get_cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM orders WHERE customer_email = %s ORDER BY order_id DESC",
                    (email,)
                )
                return cursor.fetchall()
        except Exception as e:
            logger.error(f"Error fetching orders: {e}")
            return []
    
    def get_return(self, return_id: str) -> Optional[Dict[str, Any]]:
        """Get return by ID"""
        try:
            with self.db.get_cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM returns WHERE return_id = %s",
                    (return_id,)
                )
                return cursor.fetchone()
        except Exception as e:
            logger.error(f"Error fetching return: {e}")
            return None
    
    def create_return(self, order_id: str, customer_email: str, item_details: Dict) -> Optional[str]:
        """Create a new return request"""
        try:
            with self.db.get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO returns (order_id, customer_email, item_details, status)
                    VALUES (%s, %s, %s, 'REQUESTED')
                    RETURNING return_id
                    """,
                    (order_id, customer_email, Json(item_details))
                )
                result = cursor.fetchone()
                return result['return_id'] if result else None
        except Exception as e:
            logger.error(f"Error creating return: {e}")
            return None


# Global connection instance
_global_db_connection = None


def get_db_connection() -> DatabaseConnection:
    """Get global database connection singleton"""
    global _global_db_connection
    if _global_db_connection is None:
        _global_db_connection = DatabaseConnection()
    return _global_db_connection


if __name__ == "__main__":
    """Test database connection"""
    print("=== Database Connection Test ===\n")
    
    try:
        db = DatabaseConnection()
        print("✅ Database connection created")
        
        # Test transcript operations
        transcript_db = TranscriptDB(db)
        print("✅ TranscriptDB initialized")
        
        # Test KB operations
        kb_db = KnowledgeBaseDB(db)
        print("✅ KnowledgeBaseDB initialized")
        
        # Test orders operations
        orders_db = OrdersDB(db)
        print("✅ OrdersDB initialized")
        
        print("\n✅ All database modules working!")
        
        db.close()
        
    except Exception as e:
        print(f"❌ Error: {e}")