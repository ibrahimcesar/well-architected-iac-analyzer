#!/usr/bin/env python3
"""
Script to process Well-Architected documents and generate vectors.
This script invokes the VectorProcessor Lambda function for each document.
"""

import boto3
import json
import sys
import time
from typing import List, Dict

# Initialize AWS clients
lambda_client = boto3.client('lambda')
cloudformation = boto3.client('cloudformation')


def get_stack_outputs() -> Dict[str, str]:
    """Get outputs from the CDK stack."""
    try:
        # Get the stack name from environment or use default
        stack_name = 'WAGenAIStack'
        
        response = cloudformation.describe_stacks(StackName=stack_name)
        outputs = {}
        
        for output in response['Stacks'][0]['Outputs']:
            outputs[output['OutputKey']] = output['OutputValue']
        
        return outputs
    except Exception as e:
        print(f"Error getting stack outputs: {e}")
        print("Make sure the CDK stack is deployed.")
        sys.exit(1)


def process_documents(function_name: str, documents: List[Dict]) -> None:
    """Process documents by invoking the Lambda function."""
    print(f"Processing {len(documents)} documents...")
    print(f"Using Lambda function: {function_name}\n")
    
    successful = 0
    failed = 0
    
    for doc in documents:
        try:
            print(f"Processing: {doc['source_file']}")
            print(f"  Lens: {doc['lens_name']}")
            print(f"  Pillar: {doc['pillar']}")
            
            # Invoke Lambda asynchronously
            response = lambda_client.invoke(
                FunctionName=function_name,
                InvocationType='Event',  # Async invocation
                Payload=json.dumps(doc)
            )
            
            if response['StatusCode'] == 202:
                print(f"  ✓ Invoked successfully (Status: {response['StatusCode']})")
                successful += 1
            else:
                print(f"  ✗ Failed (Status: {response['StatusCode']})")
                failed += 1
            
            # Small delay to avoid throttling
            time.sleep(0.5)
            
        except Exception as e:
            print(f"  ✗ Error: {e}")
            failed += 1
        
        print()
    
    print("\n" + "="*60)
    print(f"Processing Summary:")
    print(f"  Successful: {successful}")
    print(f"  Failed: {failed}")
    print(f"  Total: {len(documents)}")
    print("="*60)
    
    if failed > 0:
        print("\nNote: Check CloudWatch Logs for detailed error information.")
        print("Lambda function logs can be found in CloudWatch under:")
        print(f"  /aws/lambda/{function_name}")


def main():
    """Main execution function."""
    print("Well-Architected Document Vector Processor")
    print("="*60 + "\n")
    
    # Get stack outputs
    print("Retrieving stack information...")
    outputs = get_stack_outputs()
    
    function_name = outputs.get('VectorProcessorFunctionName')
    wa_docs_bucket = outputs.get('WellArchitectedDocsS3Bucket')
    
    if not function_name:
        print("Error: VectorProcessorFunctionName not found in stack outputs")
        sys.exit(1)
    
    if not wa_docs_bucket:
        print("Error: WellArchitectedDocsS3Bucket not found in stack outputs")
        sys.exit(1)
    
    print(f"Lambda Function: {function_name}")
    print(f"Source Bucket: {wa_docs_bucket}\n")
    
    # Define documents to process
    documents = [
        {
            'source_key': 'wellarchitected/wellarchitected-operational-excellence-pillar.pdf',
            'lens_name': 'Well-Architected Framework',
            'pillar': 'Operational Excellence',
            'source_file': 'wellarchitected-operational-excellence-pillar.pdf'
        },
        {
            'source_key': 'wellarchitected/wellarchitected-security-pillar.pdf',
            'lens_name': 'Well-Architected Framework',
            'pillar': 'Security',
            'source_file': 'wellarchitected-security-pillar.pdf'
        },
        {
            'source_key': 'wellarchitected/wellarchitected-reliability-pillar.pdf',
            'lens_name': 'Well-Architected Framework',
            'pillar': 'Reliability',
            'source_file': 'wellarchitected-reliability-pillar.pdf'
        },
        {
            'source_key': 'wellarchitected/wellarchitected-performance-efficiency-pillar.pdf',
            'lens_name': 'Well-Architected Framework',
            'pillar': 'Performance Efficiency',
            'source_file': 'wellarchitected-performance-efficiency-pillar.pdf'
        },
        {
            'source_key': 'wellarchitected/wellarchitected-cost-optimization-pillar.pdf',
            'lens_name': 'Well-Architected Framework',
            'pillar': 'Cost Optimization',
            'source_file': 'wellarchitected-cost-optimization-pillar.pdf'
        },
        {
            'source_key': 'wellarchitected/wellarchitected-sustainability-pillar.pdf',
            'lens_name': 'Well-Architected Framework',
            'pillar': 'Sustainability',
            'source_file': 'wellarchitected-sustainability-pillar.pdf'
        },
    ]
    
    # Process documents
    process_documents(function_name, documents)
    
    print("\nNote: Vector processing is asynchronous.")
    print("Check the vectors bucket and CloudWatch Logs to monitor progress.")
    print(f"\nVectors will be stored in S3 bucket: {outputs.get('VectorsBucketName', 'N/A')}")


if __name__ == '__main__':
    main()
