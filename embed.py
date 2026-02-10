import os
import glob
import psycopg2
from dotenv import load_dotenv
from google import genai

load_dotenv()

# Gemini setup
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY missing from .env")

# Initialize the modern client
#client = genai.Client(api_key=api_key)
client = genai.Client(
    api_key=api_key,
    http_options={'api_version': 'v1beta'}
)

for model in client.models.list():
    print(f"Name: {model.name}, Supported Actions: {model.supported_actions}")


def embed_text(text: str):
    """Generates embeddings using the model confirmed in your list."""
    try:
        result = client.models.embed_content(
            model="text-embedding-004", # Try the standard name first
            contents=text
        )
        return result.embeddings[0].values
    except Exception:
        # Fallback 
        print("text-embedding-004 not found, trying gemini-embedding-001")
        result = client.models.embed_content(
            model="gemini-embedding-001", 
            contents=text
        )
        return result.embeddings[0].values


# DB Connection
conn = psycopg2.connect(
    dbname=os.getenv("POSTGRES_DB", "ai_support"),
    user=os.getenv("POSTGRES_USER", "postgres"),
    password=os.getenv("POSTGRES_PASSWORD", ""),
    host=os.getenv("POSTGRES_HOST", "localhost"),
    port=os.getenv("POSTGRES_PORT", "5432")
)

cur = conn.cursor()

print("Clearing existing kb_articles...")
cur.execute("TRUNCATE TABLE kb_articles;")
conn.commit()

# Chunking
def chunk_text(text, max_chars=500):
    chunks = []
    current = ""

    for line in text.split("\n"):
        if len(current) + len(line) > max_chars:
            chunks.append(current.strip())
            current = ""
        current += line + "\n"

    if current.strip():
        chunks.append(current.strip())

    return chunks


# Policy ingestion
POLICY_FOLDER = "policies"

for filepath in glob.glob(f"{POLICY_FOLDER}/*.md"):
    category = os.path.basename(filepath).replace(".md", "").upper()

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    chunks = chunk_text(content)

    for chunk in chunks:
        embedding = embed_text(chunk)

        cur.execute(
            """
            INSERT INTO kb_articles (text_chunk, category, source_file, embedding)
            VALUES (%s, %s, %s, %s)
            """,
            (chunk, category, os.path.basename(filepath), embedding)
        )

        print(f"Inserted chunk from {filepath}")

conn.commit()
cur.close()
conn.close()

print("Done!")
