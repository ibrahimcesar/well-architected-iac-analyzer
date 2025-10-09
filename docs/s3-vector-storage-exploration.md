# Exploring S3 Vector Storage as an Alternative to Bedrock Knowledge Base

## Executive Summary

This document explores replacing Amazon Bedrock Knowledge Base with S3-based vector storage for the Well-Architected IaC Analyzer project. This analysis covers architecture options, implementation approaches, trade-offs, and migration considerations.

## Current Architecture

### Bedrock Knowledge Base Implementation

The current system uses:
- **Bedrock Knowledge Base** with Titan Embeddings v2 (1024 dimensions)
- **S3 Data Source** for Well-Architected documentation
- **Hierarchical Chunking** (60 token overlap, 2000 parent/800 child tokens)
- **RetrieveAndGenerate API** for RAG operations
- **Lambda Synchronizer** for weekly KB updates

**Key Code Locations:**
- CDK Stack: `ecs_fargate_app/wa_genai_stack.py` (lines 200-230)
- Backend Service: `ecs_fargate_app/backend/src/modules/analyzer/analyzer.service.ts`
- KB Prompts: `ecs_fargate_app/backend/src/prompts/knowledge-base-prompts.ts`
- KB Synchronizer: `ecs_fargate_app/lambda_kb_synchronizer/kb_synchronizer.py`

## S3 Vector Storage Options

### Option 1: S3 + OpenSearch Serverless Vector Engine

**Architecture:**
```
Documents (S3) → Embedding Model → Vector Store (OpenSearch Serverless)
                                          ↓
Query → Embedding → Vector Search → Top-K Results → LLM Context
```

**Pros:**
- Native AWS integration
- Serverless scaling
- Advanced filtering capabilities
- HNSW algorithm for fast similarity search
- Built-in security with IAM

**Cons:**
- Additional cost for OpenSearch Serverless
- More complex setup than Bedrock KB
- Requires managing vector indices
- Need to handle embedding generation manually

**Implementation Components:**
1. OpenSearch Serverless collection with vector engine
2. Lambda function for document processing and embedding
3. S3 bucket for source documents
4. Backend service modifications for vector search

### Option 2: S3 + pgvector (RDS Aurora Serverless)

**Architecture:**
```
Documents (S3) → Embedding Model → Vector Store (Aurora PostgreSQL + pgvector)
                                          ↓
Query → Embedding → SQL Vector Search → Top-K Results → LLM Context
```

**Pros:**
- Familiar SQL interface
- Strong consistency guarantees
- ACID transactions
- Good for structured metadata alongside vectors
- Cost-effective for moderate scale

**Cons:**
- Requires RDS management (even if serverless)
- Cold start latency with Aurora Serverless v2
- Less optimized for pure vector search vs OpenSearch
- Additional database maintenance

### Option 3: S3 + DynamoDB with Vector Search (Preview)

**Architecture:**
```
Documents (S3) → Embedding Model → Vector Store (DynamoDB)
                                          ↓
Query → Embedding → Vector Search → Top-K Results → LLM Context
```

**Pros:**
- Fully serverless
- No infrastructure management
- Pay-per-request pricing
- Fast single-digit millisecond latency
- Native AWS service

**Cons:**
- Currently in preview (limited availability)
- Limited documentation and examples
- May have feature limitations vs mature solutions
- Uncertain long-term pricing

### Option 4: S3 + In-Memory Vector Search (FAISS/Annoy)

**Architecture:**
```
Documents (S3) → Embedding Model → Vector Index (S3)
                                          ↓
Lambda loads index → In-memory search → Top-K Results → LLM Context
```

**Pros:**
- Simplest architecture
- No additional database services
- Very fast search once loaded
- Full control over algorithms

**Cons:**
- Cold start penalty (loading index)
- Limited by Lambda memory (10GB max)
- Not suitable for large document sets
- Manual index management

### Option 5: S3 + Bedrock Retrieve API (Hybrid)

**Architecture:**
```
Documents (S3) → Manual Chunking → Embeddings → S3 Storage
                                                      ↓
Query → Bedrock Retrieve API → Vector Search → Results
```

**Pros:**
- Uses Bedrock's retrieval capabilities
- Simpler than full KB
- More control over chunking
- Lower cost than full KB

**Cons:**
- Still requires Bedrock service
- Less integrated than full KB
- Manual embedding management
- Limited filtering options

## Detailed Comparison

### Cost Analysis

| Solution | Setup Cost | Monthly Cost (est.) | Scaling Cost |
|----------|-----------|---------------------|--------------|
| **Current (Bedrock KB)** | Low | $50-200 | Linear |
| **OpenSearch Serverless** | Medium | $100-300 | Sub-linear |
| **Aurora + pgvector** | Medium | $50-150 | Linear |
| **DynamoDB Vector** | Low | $30-100 | Sub-linear |
| **FAISS/Lambda** | Low | $10-50 | Linear |

*Estimates based on ~1000 documents, 10K queries/month*

### Performance Comparison

| Solution | Query Latency | Cold Start | Scalability | Accuracy |
|----------|--------------|------------|-------------|----------|
| **Current (Bedrock KB)** | 2-5s | None | High | High |
| **OpenSearch Serverless** | 100-500ms | None | Very High | High |
| **Aurora + pgvector** | 200-800ms | 5-30s | Medium | High |
| **DynamoDB Vector** | 50-200ms | None | Very High | Medium-High |
| **FAISS/Lambda** | 50-100ms | 2-10s | Low | High |

### Feature Comparison

| Feature | Bedrock KB | OpenSearch | Aurora | DynamoDB | FAISS |
|---------|-----------|------------|--------|----------|-------|
| **Managed Service** | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Auto-scaling** | ✅ | ✅ | ⚠️ | ✅ | ❌ |
| **Metadata Filtering** | ✅ | ✅ | ✅ | ⚠️ | ❌ |
| **Hybrid Search** | ❌ | ✅ | ✅ | ❌ | ❌ |
| **Built-in RAG** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Multi-tenancy** | ✅ | ✅ | ✅ | ✅ | ⚠️ |

## Recommended Approach: OpenSearch Serverless

### Why OpenSearch Serverless?

1. **Best Balance**: Offers managed service benefits with better performance than Bedrock KB
2. **AWS Native**: Fully integrated with AWS ecosystem
3. **Proven Technology**: Mature vector search capabilities
4. **Flexibility**: Can add hybrid search, advanced filtering later
5. **Cost Effective**: Pay only for what you use

### Implementation Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     S3 Vector Storage Architecture               │
└─────────────────────────────────────────────────────────────────┘

┌──────────────┐         ┌──────────────┐         ┌──────────────┐
│   S3 Bucket  │────────▶│   Lambda     │────────▶│  OpenSearch  │
│  (WA Docs)   │         │  Processor   │         │  Serverless  │
└──────────────┘         └──────────────┘         └──────────────┘
                                │                          │
                                │                          │
                         ┌──────▼──────┐          ┌───────▼──────┐
                         │   Bedrock   │          │    Vector    │
                         │  Embeddings │          │    Index     │
                         └─────────────┘          └──────────────┘
                                                          │
                                                          │
┌──────────────┐         ┌──────────────┐         ┌─────▼────────┐
│   Backend    │────────▶│   Query      │────────▶│   Search     │
│   Service    │         │  Embedding   │         │   Results    │
└──────────────┘         └──────────────┘         └──────────────┘
```

### Implementation Steps

#### 1. CDK Infrastructure Changes

**New Resources Needed:**
```python
# OpenSearch Serverless Collection
from aws_cdk import aws_opensearchserverless as opensearch

# Create encryption policy
encryption_policy = opensearch.CfnSecurityPolicy(
    self, "EncryptionPolicy",
    name="wa-analyzer-encryption",
    type="encryption",
    policy=json.dumps({
        "Rules": [{
            "ResourceType": "collection",
            "Resource": ["collection/wa-analyzer-vectors"]
        }],
        "AWSOwnedKey": True
    })
)

# Create network policy
network_policy = opensearch.CfnSecurityPolicy(
    self, "NetworkPolicy",
    name="wa-analyzer-network",
    type="network",
    policy=json.dumps([{
        "Rules": [{
            "ResourceType": "collection",
            "Resource": ["collection/wa-analyzer-vectors"]
        }],
        "AllowFromPublic": False,
        "SourceVPCEs": [vpc_endpoint.vpc_endpoint_id]
    }])
)

# Create collection
vector_collection = opensearch.CfnCollection(
    self, "VectorCollection",
    name="wa-analyzer-vectors",
    type="VECTORSEARCH",
    description="Vector storage for WA documentation"
)
```

#### 2. Document Processing Lambda

**Purpose:** Process documents, generate embeddings, store in OpenSearch

```python
import boto3
import json
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth

def handler(event, context):
    # Initialize clients
    s3 = boto3.client('s3')
    bedrock = boto3.client('bedrock-runtime')
    
    # Get document from S3
    bucket = event['bucket']
    key = event['key']
    
    response = s3.get_object(Bucket=bucket, Key=key)
    content = response['Body'].read().decode('utf-8')
    
    # Chunk document
    chunks = chunk_document(content)
    
    # Generate embeddings
    vectors = []
    for chunk in chunks:
        embedding_response = bedrock.invoke_model(
            modelId='amazon.titan-embed-text-v2:0',
            body=json.dumps({
                "inputText": chunk['text'],
                "dimensions": 1024,
                "normalize": True
            })
        )
        
        embedding = json.loads(embedding_response['body'].read())
        vectors.append({
            'vector': embedding['embedding'],
            'text': chunk['text'],
            'metadata': chunk['metadata']
        })
    
    # Store in OpenSearch
    store_vectors(vectors)
    
    return {'statusCode': 200}
```

#### 3. Backend Service Modifications

**Replace `retrieveFromKnowledgeBase` method:**

```typescript
private async retrieveFromVectorStore(
    pillar: string,
    question: string,
    questionGroup: QuestionGroup,
    lensName?: string
): Promise<string[]> {
    try {
        // Generate query embedding
        const queryEmbedding = await this.generateEmbedding(
            `${pillar}: ${question}`
        );
        
        // Search OpenSearch
        const searchResults = await this.searchVectors(
            queryEmbedding,
            {
                pillar: pillar,
                lens_name: lensName || 'Well-Architected Framework'
            },
            10 // top-k results
        );
        
        // Extract text from results
        return searchResults.map(result => result._source.text);
    } catch (error) {
        this.logger.error('Error retrieving from vector store:', error);
        throw error;
    }
}

private async generateEmbedding(text: string): Promise<number[]> {
    const bedrockClient = this.awsConfig.createBedrockClient();
    
    const response = await bedrockClient.invokeModel({
        modelId: 'amazon.titan-embed-text-v2:0',
        body: JSON.stringify({
            inputText: text,
            dimensions: 1024,
            normalize: true
        })
    });
    
    const result = JSON.parse(response.body.transformToString());
    return result.embedding;
}

private async searchVectors(
    queryVector: number[],
    filters: Record<string, string>,
    k: number = 10
): Promise<any[]> {
    const opensearchClient = this.awsConfig.createOpenSearchClient();
    
    const searchBody = {
        size: k,
        query: {
            bool: {
                must: [{
                    knn: {
                        vector_field: {
                            vector: queryVector,
                            k: k
                        }
                    }
                }],
                filter: Object.entries(filters).map(([key, value]) => ({
                    term: { [key]: value }
                }))
            }
        }
    };
    
    const response = await opensearchClient.search({
        index: 'wa-documents',
        body: searchBody
    });
    
    return response.body.hits.hits;
}
```

#### 4. Configuration Changes

**Update `config.ini`:**
```ini
[settings]
# Replace knowledge_base_id with opensearch_endpoint
opensearch_endpoint = your-collection-endpoint.region.aoss.amazonaws.com
opensearch_index = wa-documents
embedding_model = amazon.titan-embed-text-v2:0
embedding_dimensions = 1024
```

**Update environment variables:**
```typescript
// In backend container
environment: {
    "OPENSEARCH_ENDPOINT": opensearchEndpoint,
    "OPENSEARCH_INDEX": "wa-documents",
    "EMBEDDING_MODEL": "amazon.titan-embed-text-v2:0",
    // Remove KNOWLEDGE_BASE_ID
}
```

## Migration Strategy

### Phase 1: Parallel Running (Recommended)

1. **Deploy OpenSearch alongside Bedrock KB**
2. **Implement feature flag** to switch between implementations
3. **Compare results** for quality and performance
4. **Gradual rollout** to users

```typescript
// Feature flag approach
private async retrieveContext(
    pillar: string,
    question: string,
    questionGroup: QuestionGroup,
    lensName?: string
): Promise<string[]> {
    const useVectorStore = this.configService.get<boolean>(
        'features.useVectorStore',
        false
    );
    
    if (useVectorStore) {
        return this.retrieveFromVectorStore(
            pillar, question, questionGroup, lensName
        );
    }
    
    return this.retrieveFromKnowledgeBase(
        pillar, question, questionGroup, lensName
    );
}
```

### Phase 2: Full Migration

1. **Remove Bedrock KB resources** from CDK
2. **Update all references** in code
3. **Migrate existing data** to OpenSearch
4. **Update documentation**

## Trade-offs Analysis

### Advantages of S3 + OpenSearch

1. **Performance**: 5-10x faster query response times
2. **Cost**: Potentially 30-50% lower for high-volume usage
3. **Flexibility**: More control over chunking, embeddings, search algorithms
4. **Hybrid Search**: Can combine vector + keyword search
5. **Advanced Filtering**: Complex metadata queries
6. **Observability**: Better monitoring and debugging

### Disadvantages of S3 + OpenSearch

1. **Complexity**: More components to manage
2. **Development Time**: Significant implementation effort
3. **Maintenance**: Need to handle embedding generation, index management
4. **Learning Curve**: Team needs OpenSearch expertise
5. **No Built-in RAG**: Must implement retrieval + generation separately

### When to Choose S3 + OpenSearch

✅ **Choose S3 + OpenSearch if:**
- Query latency is critical (< 500ms requirement)
- Need advanced filtering or hybrid search
- High query volume (> 100K/month)
- Want more control over the RAG pipeline
- Team has OpenSearch experience

❌ **Stick with Bedrock KB if:**
- Simplicity is priority
- Low to moderate query volume
- Limited DevOps resources
- Want fully managed solution
- Current performance is acceptable

## Implementation Estimate

### Development Effort

| Task | Effort | Priority |
|------|--------|----------|
| OpenSearch CDK setup | 2-3 days | High |
| Document processor Lambda | 3-4 days | High |
| Backend service changes | 4-5 days | High |
| Testing & validation | 3-4 days | High |
| Migration scripts | 2-3 days | Medium |
| Documentation | 1-2 days | Medium |
| **Total** | **15-21 days** | |

### Infrastructure Costs (Monthly Estimates)

**Current Bedrock KB:**
- Knowledge Base: ~$50-100
- Embeddings: ~$20-50
- Storage: ~$5
- **Total: ~$75-155/month**

**OpenSearch Serverless:**
- OCU (compute): ~$80-150
- Storage: ~$10-20
- Data transfer: ~$5-10
- Embeddings: ~$20-50
- **Total: ~$115-230/month**

*Note: Costs vary significantly based on usage patterns*

## Code Changes Required

### Files to Modify

1. **CDK Stack** (`wa_genai_stack.py`):
   - Remove Bedrock KB resources
   - Add OpenSearch Serverless collection
   - Add VPC endpoint for OpenSearch
   - Update IAM policies

2. **Backend Service** (`analyzer.service.ts`):
   - Replace `retrieveFromKnowledgeBase` method
   - Add embedding generation
   - Add vector search logic
   - Update error handling

3. **Configuration** (`aws.config.ts`):
   - Add OpenSearch client initialization
   - Remove KB client references
   - Add embedding model configuration

4. **Lambda Functions**:
   - Create new document processor
   - Update KB synchronizer to use OpenSearch
   - Add index management functions

### Backward Compatibility

To maintain compatibility during migration:

```typescript
interface VectorStoreConfig {
    type: 'bedrock-kb' | 'opensearch' | 'aurora' | 'dynamodb';
    endpoint?: string;
    indexName?: string;
    knowledgeBaseId?: string;
}

class VectorStoreFactory {
    static create(config: VectorStoreConfig): VectorStore {
        switch (config.type) {
            case 'bedrock-kb':
                return new BedrockKBStore(config);
            case 'opensearch':
                return new OpenSearchStore(config);
            // ... other implementations
        }
    }
}
```

## Testing Strategy

### 1. Quality Assurance
- Compare retrieval results between KB and OpenSearch
- Measure answer accuracy with test queries
- Validate metadata filtering works correctly

### 2. Performance Testing
- Load test with concurrent queries
- Measure p50, p95, p99 latencies
- Test cold start scenarios

### 3. Cost Validation
- Monitor actual costs for 1 month
- Compare against Bedrock KB baseline
- Adjust OCU allocation if needed

## Recommendations

### Short Term (Next 3 months)
**Recommendation: Stay with Bedrock Knowledge Base**

**Rationale:**
- Current solution works well
- Team can focus on feature development
- Bedrock KB is improving rapidly
- Migration effort doesn't justify immediate benefits

### Medium Term (3-12 months)
**Recommendation: Evaluate OpenSearch Serverless**

**Triggers to reconsider:**
- Query volume exceeds 50K/month
- Latency becomes user complaint
- Need advanced filtering features
- Cost optimization becomes priority

### Long Term (12+ months)
**Recommendation: Consider DynamoDB Vector Search**

**Rationale:**
- Feature will be GA with better documentation
- Simplest serverless option
- Best cost model for variable workloads
- Minimal operational overhead

## Proof of Concept

### Minimal PoC Scope

To validate the OpenSearch approach:

1. **Create small OpenSearch collection** (1 OCU)
2. **Process 100 sample documents**
3. **Implement basic search** in separate branch
4. **Compare results** with current KB
5. **Measure performance** and costs

**Estimated PoC Time: 1 week**

## Conclusion

While S3 + OpenSearch Serverless offers compelling advantages in performance and flexibility, the current Bedrock Knowledge Base implementation is well-suited for this application's needs. 

**Key Takeaway:** Unless you're experiencing specific pain points (latency, cost, or feature limitations), the migration effort may not be justified at this time. However, keeping OpenSearch as a future option provides a clear path for optimization as the application scales.

## Next Steps

If you decide to proceed with exploration:

1. ✅ Review this document with the team
2. ⬜ Create PoC branch
3. ⬜ Implement minimal OpenSearch integration
4. ⬜ Run comparative tests
5. ⬜ Make data-driven decision

## References

- [OpenSearch Serverless Vector Search](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-vector-search.html)
- [Bedrock Knowledge Base Pricing](https://aws.amazon.com/bedrock/pricing/)
- [OpenSearch Serverless Pricing](https://aws.amazon.com/opensearch-service/pricing/)
- [pgvector Extension](https://github.com/pgvector/pgvector)
- [FAISS Library](https://github.com/facebookresearch/faiss)
