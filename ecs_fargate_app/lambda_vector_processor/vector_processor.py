"""
Lambda function to process documents, generate embeddings, and store vectors in S3
"""

import json
import os
import boto3
import hashlib
from typing import List, Dict, Any
from datetime import datetime

s3_client = boto3.client('s3')
bedrock_client = boto3.client('bedrock-runtime')

# Environment variables
VECTORS_BUCKET = os.environ.get('VECTORS_BUCKET')
SOURCE_BUCKET = os.environ.get('SOURCE_BUCKET')
EMBEDDING_MODEL = os.environ.get('EMBEDDING_MODEL', 'amazon.titan-embed-text-v2:0')
EMBEDDING_DIMENSIONS = int(os.environ.get('EMBEDDING_DIMENSIONS', '1024'))


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 60) -> List[Dict[str, Any]]:
    """
    Chunk text into overlapping segments
    
    Args:
        text: The text to chunk
        chunk_size: Maximum tokens per chunk (approximate by characters)
        overlap: Number of overlapping tokens between chunks
        
    Returns:
        List of chunk dictionaries with text and metadata
    """
    # Approximate tokens by characters (rough estimate: 1 token ≈ 4 characters)
    char_chunk_size = chunk_size * 4
    char_overlap = overlap * 4
    
    chunks = []
    start = 0
    chunk_index = 0
    
    while start < len(text):
        end = start + char_chunk_size
        chunk_text = text[start:end]
        
        # Try to break at sentence boundary
        if end < len(text):
            last_period = chunk_text.rfind('.')
            last_newline = chunk_text.rfind('\n')
            break_point = max(last_period, last_newline)
            
            if break_point > char_chunk_size * 0.7:  # Only break if we're past 70% of chunk
                end = start + break_point + 1
                chunk_text = text[start:end]
        
        chunks.append({
            'text': chunk_text.strip(),
            'chunk_index': chunk_index,
            'start_char': start,
            'end_char': end
        })
        
        chunk_index += 1
        start = end - char_overlap
    
    return chunks


def generate_embedding(text: str) -> List[float]:
    """
    Generate embedding vector for text using Bedrock
    
    Args:
        text: The text to embed
        
    Returns:
        List of floats representing the embedding vector
    """
    try:
        response = bedrock_client.invoke_model(
            modelId=EMBEDDING_MODEL,
            body=json.dumps({
                "inputText": text,
                "dimensions": EMBEDDING_DIMENSIONS,
                "normalize": True
            })
        )
        
        result = json.loads(response['body'].read())
        return result['embedding']
    except Exception as e:
        print(f"Error generating embedding: {str(e)}")
        raise


def generate_chunk_id(text: str, metadata: Dict[str, Any]) -> str:
    """
    Generate a unique ID for a chunk based on its content and metadata
    
    Args:
        text: The chunk text
        metadata: The chunk metadata
        
    Returns:
        A unique hash-based ID
    """
    content = f"{text}{json.dumps(metadata, sort_keys=True)}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def process_document(
    source_key: str,
    lens_name: str,
    pillar: str,
    source_file: str
) -> List[Dict[str, Any]]:
    """
    Process a document: read, chunk, embed, and prepare for storage
    
    Args:
        source_key: S3 key of the source document
        lens_name: Name of the lens (e.g., "Well-Architected Framework")
        pillar: Pillar name (e.g., "Operational Excellence")
        source_file: Original filename
        
    Returns:
        List of processed chunks with embeddings
    """
    # Read document from S3
    response = s3_client.get_object(Bucket=SOURCE_BUCKET, Key=source_key)
    content = response['Body'].read().decode('utf-8')
    
    # Chunk the document
    chunks = chunk_text(content)
    
    # Process each chunk
    processed_chunks = []
    for chunk in chunks:
        # Generate embedding
        embedding = generate_embedding(chunk['text'])
        
        # Create metadata
        metadata = {
            'lens_name': lens_name,
            'pillar': pillar,
            'source_file': source_file,
            'chunk_index': chunk['chunk_index'],
            'start_char': chunk['start_char'],
            'end_char': chunk['end_char'],
            'processed_at': datetime.utcnow().isoformat()
        }
        
        # Generate unique ID
        chunk_id = generate_chunk_id(chunk['text'], metadata)
        
        processed_chunks.append({
            'id': chunk_id,
            'text': chunk['text'],
            'embedding': embedding,
            'metadata': metadata
        })
    
    return processed_chunks


def store_vectors(chunks: List[Dict[str, Any]], lens_name: str, pillar: str) -> None:
    """
    Store processed chunks with embeddings in S3
    
    Args:
        chunks: List of processed chunks
        lens_name: Name of the lens
        pillar: Pillar name
    """
    # Normalize names for S3 keys
    lens_key = lens_name.lower().replace(' ', '-')
    pillar_key = pillar.lower().replace(' ', '-')
    
    for chunk in chunks:
        # Create S3 key
        s3_key = f"embeddings/{lens_key}/{pillar_key}/{chunk['id']}.json"
        
        # Store chunk
        s3_client.put_object(
            Bucket=VECTORS_BUCKET,
            Key=s3_key,
            Body=json.dumps(chunk),
            ContentType='application/json'
        )


def update_index(lens_name: str, pillar: str, chunk_count: int) -> None:
    """
    Update the master index with information about processed documents
    
    Args:
        lens_name: Name of the lens
        pillar: Pillar name
        chunk_count: Number of chunks processed
    """
    index_key = 'metadata/index.json'
    
    # Try to read existing index
    try:
        response = s3_client.get_object(Bucket=VECTORS_BUCKET, Key=index_key)
        index = json.loads(response['Body'].read().decode('utf-8'))
    except s3_client.exceptions.NoSuchKey:
        index = {'lenses': {}}
    
    # Update index
    if lens_name not in index['lenses']:
        index['lenses'][lens_name] = {'pillars': {}}
    
    index['lenses'][lens_name]['pillars'][pillar] = {
        'chunk_count': chunk_count,
        'last_updated': datetime.utcnow().isoformat()
    }
    
    # Store updated index
    s3_client.put_object(
        Bucket=VECTORS_BUCKET,
        Key=index_key,
        Body=json.dumps(index, indent=2),
        ContentType='application/json'
    )


def handler(event, context):
    """
    Lambda handler for processing documents
    
    Event format:
    {
        "source_key": "wellarchitected/wellarchitected-operational-excellence-pillar.pdf",
        "lens_name": "Well-Architected Framework",
        "pillar": "Operational Excellence",
        "source_file": "wellarchitected-operational-excellence-pillar.pdf"
    }
    """
    try:
        source_key = event['source_key']
        lens_name = event['lens_name']
        pillar = event['pillar']
        source_file = event['source_file']
        
        print(f"Processing document: {source_key}")
        
        # Process document
        chunks = process_document(source_key, lens_name, pillar, source_file)
        
        print(f"Generated {len(chunks)} chunks")
        
        # Store vectors
        store_vectors(chunks, lens_name, pillar)
        
        print(f"Stored {len(chunks)} vectors in S3")
        
        # Update index
        update_index(lens_name, pillar, len(chunks))
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Document processed successfully',
                'chunks_created': len(chunks)
            })
        }
    except Exception as e:
        print(f"Error processing document: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e)
            })
        }
