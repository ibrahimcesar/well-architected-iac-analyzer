# Vector Storage Implementation Guide

## Overview

This guide covers the S3-based vector storage implementation that provides an alternative to Bedrock Knowledge Base for document retrieval in the Well-Architected IaC Analyzer.

## Architecture

### Components

1. **Vector Processor Lambda** (`ecs_fargate_app/lambda_vector_processor/`)
   - Processes PDF documents from S3
   - Generates embeddings using Bedrock Titan v2
   - Stores vectors in S3 with metadata

2. **Vector Search Service** (`ecs_fargate_app/backend/src/modules/vector-search/`)
   - Generates query embeddings
   - Performs cosine similarity search
   - Returns relevant document chunks

3. **S3 Vector Storage**
   - Hierarchical structure: `embeddings/{lens_name}/{pillar}/`
   - JSON files with vectors and metadata
   - Index file for quick lookups

## Deployment Steps

### 1. Deploy Infrastructure

The CDK stack has been updated with vector storage resources:

```bash
# Deploy the stack
cdk deploy

# Or use the deployment script
./deploy-wa-analyzer.sh
```

This creates:
- S3 bucket for vector storage
- Vector Processor Lambda function
- IAM permissions for backend to access vectors
- Environment variables for configuration

### 2. Process Documents

After deployment, run the processing script to generate vectors:

```bash
# Make the script executable
chmod +x scripts/process_wa_documents.py

# Run the script
python3 scripts/process_wa_documents.py
```

The script will:
- Retrieve stack outputs automatically
- Invoke the Lambda function for each pillar document
- Process documents asynchronously
- Display progress and results

### 3. Verify Vector Generation

Check that vectors were created successfully:

```bash
# List vectors in S3
aws s3 ls s3://<vectors-bucket-name>/embeddings/ --recursive

# Check the index file
aws s3 cp s3://<vectors-bucket-name>/metadata/index.json - | jq .
```

Expected structure:
```
embeddings/
├── Well-Architected Framework/
│   ├── Operational Excellence/
│   │   ├── chunk_0.json
│   │   ├── chunk_1.json
│   │   └── ...
│   ├── Security/
│   ├── Reliability/
│   ├── Performance Efficiency/
│   ├── Cost Optimization/
│   └── Sustainability/
└── metadata/
    └── index.json
```

### 4. Enable Vector Search (Optional)

Vector search is disabled by default. To enable it:

```bash
# Update the backend environment variable
aws ecs update-service \
  --cluster <cluster-name> \
  --service <backend-service-name> \
  --force-new-deployment \
  --environment-overrides name=USE_VECTOR_SEARCH,value=true

# Or update via CDK and redeploy
```

## Configuration

### Environment Variables

Backend service environment variables:

```bash
VECTORS_BUCKET=<bucket-name>              # S3 bucket for vectors
EMBEDDING_MODEL=amazon.titan-embed-text-v2:0  # Bedrock embedding model
EMBEDDING_DIMENSIONS=1024                  # Vector dimensions
USE_VECTOR_SEARCH=false                    # Feature flag (default: false)
```

### Feature Flag

The `USE_VECTOR_SEARCH` flag controls whether to use vector search or Knowledge Base:

- `false` (default): Uses Bedrock Knowledge Base
- `true`: Uses S3 vector storage with fallback to KB

## Usage

### Vector Search Service

The vector search service is automatically integrated when enabled:

```typescript
// Automatically used in analyzer service when feature flag is enabled
const context = await this.vectorSearchService.retrieveContext(
  pillar,
  question,
  lensName,
  10  // top-K results
);
```

### Manual Testing

Test vector search directly:

```typescript
// Check if vectors exist
const exists = await vectorSearchService.vectorsExist(
  'Well-Architected Framework',
  'Security'
);

// Search for similar vectors
const results = await vectorSearchService.searchSimilarVectors(
  'How do I implement encryption?',
  'Well-Architected Framework',
  'Security',
  5
);
```

## Monitoring

### CloudWatch Logs

Monitor vector processing:

```bash
# Vector Processor Lambda logs
aws logs tail /aws/lambda/<vector-processor-function-name> --follow

# Backend service logs (vector search)
aws logs tail /aws/ecs/<cluster>/<backend-service> --follow --filter-pattern "vector"
```

### Metrics to Track

1. **Vector Processing**
   - Lambda invocations
   - Processing duration
   - Error rates

2. **Vector Search**
   - Query latency
   - Embedding generation time
   - Search time
   - Cache hit rate (if implemented)

3. **Cost Metrics**
   - Bedrock API calls (embeddings)
   - S3 storage costs
   - S3 GET/LIST operations

## Performance Comparison

### Expected Improvements

| Metric | Knowledge Base | Vector Storage | Improvement |
|--------|---------------|----------------|-------------|
| Query Latency | 2-5s | 200-500ms | 4-10x faster |
| Cold Start | Yes | No | Eliminated |
| Consistency | Variable | Consistent | More reliable |

### Benchmarking

Run performance tests:

```bash
# Compare KB vs Vector Search
npm run test:performance

# Or manually test
curl -X POST http://localhost:3000/api/analyzer/analyze \
  -H "Content-Type: application/json" \
  -d '{"pillar": "Security", "question": "How to implement encryption?"}'
```

## Troubleshooting

### Vectors Not Generated

1. Check Lambda execution:
```bash
aws lambda invoke \
  --function-name <vector-processor-name> \
  --payload '{"source_key":"wellarchitected/wellarchitected-security-pillar.pdf","lens_name":"Well-Architected Framework","pillar":"Security","source_file":"wellarchitected-security-pillar.pdf"}' \
  response.json
```

2. Check CloudWatch Logs for errors

3. Verify IAM permissions:
   - Lambda can read from source bucket
   - Lambda can write to vectors bucket
   - Lambda can invoke Bedrock

### Vector Search Not Working

1. Verify feature flag is enabled:
```bash
aws ecs describe-services \
  --cluster <cluster-name> \
  --services <backend-service-name> \
  --query 'services[0].taskDefinition'
```

2. Check backend logs for errors

3. Verify vectors exist in S3

4. Test embedding generation:
```bash
aws bedrock-runtime invoke-model \
  --model-id amazon.titan-embed-text-v2:0 \
  --body '{"inputText":"test"}' \
  output.json
```

### Performance Issues

1. **Slow queries**: Check S3 latency and consider caching
2. **High costs**: Monitor Bedrock API calls
3. **Memory issues**: Adjust Lambda memory settings

## Rollback

If issues occur, disable vector search immediately:

```bash
# Method 1: Update environment variable
aws ecs update-service \
  --cluster <cluster-name> \
  --service <backend-service-name> \
  --force-new-deployment \
  --environment-overrides name=USE_VECTOR_SEARCH,value=false

# Method 2: Redeploy with CDK
# Edit wa_genai_stack.py and set USE_VECTOR_SEARCH to "false"
cdk deploy
```

The system will automatically fall back to Knowledge Base.

## Maintenance

### Updating Vectors

When Well-Architected documents are updated:

1. Upload new documents to source bucket
2. Run processing script again:
```bash
python3 scripts/process_wa_documents.py
```

3. Vectors will be regenerated with new content

### Adding New Lenses

To add support for custom lenses:

1. Upload lens documents to source bucket
2. Update processing script with new lens metadata
3. Run processing script
4. Vectors will be organized by lens name

### Cleaning Up

Remove vector storage resources:

```bash
# Delete vectors
aws s3 rm s3://<vectors-bucket-name>/embeddings/ --recursive

# Or destroy entire stack
cdk destroy
```

## Cost Optimization

### Estimated Costs

For 6 pillar documents (~1000 pages total):

- **One-time processing**: ~$0.50-1.00
  - Bedrock embeddings: ~$0.40
  - Lambda execution: ~$0.10
  - S3 storage: negligible

- **Monthly operational**: ~$1-5
  - S3 storage: ~$0.50
  - Bedrock queries: ~$0.50-4.00 (depends on usage)
  - S3 GET operations: ~$0.10

### Cost Reduction Tips

1. **Cache embeddings**: Reduce Bedrock API calls
2. **Batch processing**: Process multiple documents together
3. **Lifecycle policies**: Archive old vectors to Glacier
4. **Monitor usage**: Set up billing alerts

## Security

### IAM Permissions

Minimum required permissions:

**Vector Processor Lambda:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::source-bucket/*"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject"],
      "Resource": "arn:aws:s3:::vectors-bucket/*"
    },
    {
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel"],
      "Resource": "*"
    }
  ]
}
```

**Backend Service:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::vectors-bucket",
        "arn:aws:s3:::vectors-bucket/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel"],
      "Resource": "*"
    }
  ]
}
```

### Data Protection

- All S3 buckets enforce SSL
- Vectors contain only document chunks (no sensitive data)
- IAM roles follow least privilege principle

## Future Enhancements

### Phase 2 Features

1. **Caching Layer**
   - ElastiCache for frequent queries
   - Reduce Bedrock API calls
   - Improve response times

2. **Hybrid Search**
   - Combine vector + keyword search
   - Better handling of specific terms
   - Improved accuracy

3. **Reranking**
   - Use Bedrock Rerank API
   - Improve result ordering
   - Better context selection

4. **Batch Processing**
   - Process multiple queries in parallel
   - Reduce latency for multi-pillar analysis
   - Better resource utilization

## Support

For issues or questions:

1. Check CloudWatch Logs
2. Review this documentation
3. Check the main README.md
4. Open an issue on GitHub

## References

- [Implementation Summary](./s3-vector-implementation-summary.md)
- [Retrieve API Implementation](./s3-retrieve-api-implementation.md)
- [Vector Storage Exploration](./s3-vector-storage-exploration.md)
- [AWS Bedrock Documentation](https://docs.aws.amazon.com/bedrock/)
- [Titan Embeddings](https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html)
