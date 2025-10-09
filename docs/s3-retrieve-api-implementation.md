# S3 + Bedrock Retrieve API Implementation Plan

## Overview

This implementation replaces Bedrock Knowledge Base with a hybrid approach using:
- S3 for document storage
- Manual chunking and embedding generation
- Bedrock Retrieve API for vector search
- Direct control over the RAG pipeline

## Architecture Changes

### Before (Bedrock Knowledge Base)
```
S3 Docs → Bedrock KB (managed) → RetrieveAndGenerate API
```

### After (S3 + Retrieve API)
```
S3 Docs → Lambda Processor → S3 Vectors → Bedrock Retrieve API
```

## Implementation Steps

### Step 1: Create Vector Storage Structure in S3

**New S3 Structure:**
```
wa-vectors-bucket/
├── embeddings/
│   ├── wellarchitected/
│   │   ├── operational-excellence/
│   │   │   ├── chunk-001.json
│   │   │   └── chunk-002.json
│   │   └── security/
│   └── other-lenses/
└── metadata/
    └── index.json
```

**Chunk Format:**
```json
{
  "id": "chunk-001",
  "text": "Original text content...",
  "embedding": [0.123, 0.456, ...],
  "metadata": {
    "lens_name": "Well-Architected Framework",
    "pillar": "Operational Excellence",
    "source_file": "wellarchitected-operational-excellence-pillar.pdf",
    "page": 5,
    "chunk_index": 1
  }
}
```

### Step 2: Document Processing Lambda

This Lambda will:
1. Read documents from source S3 bucket
2. Chunk documents intelligently
3. Generate embeddings using Bedrock
4. Store vectors in S3
5. Maintain searchable index

### Step 3: Backend Service Changes

Replace Knowledge Base calls with:
1. Generate query embedding
2. Search S3 vectors using Bedrock Retrieve
3. Return top-k results

### Step 4: Remove Bedrock KB Resources

Clean up CDK stack by removing:
- Knowledge Base construct
- Data source
- Ingestion job custom resource

## Benefits of This Approach

1. **Lower Cost**: No KB storage fees, only embedding generation
2. **More Control**: Custom chunking strategies
3. **Simpler**: Fewer managed services
4. **Flexible**: Easy to modify chunking/embedding logic
5. **Portable**: Vectors stored in S3 can be used with other tools

## Implementation Timeline

- Step 1-2: 3-4 days
- Step 3: 2-3 days  
- Step 4: 1 day
- Testing: 2-3 days
- **Total: ~8-11 days**
