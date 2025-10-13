"""CDK stack for deploying only Knowledge Base and Storage resources"""

import time
import uuid

import aws_cdk as cdk
from aws_cdk import CfnOutput, CfnParameter, Duration, RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from aws_cdk import custom_resources as cr
from cdklabs.generative_ai_cdk_constructs import bedrock
from constructs import Construct


class KBStorageStack(Stack):
    """CDK Stack for deploying only Knowledge Base and Storage resources"""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Context parameters
        deploy_storage = self.node.try_get_context("deploy_storage")
        if deploy_storage is None:  # Default to true if not specified
            deploy_storage = True

        # Creates Bedrock KB using the generative_ai_cdk_constructs
        kb = bedrock.KnowledgeBase(
            self,
            "WAFR-KnowledgeBase",
            embeddings_model=bedrock.BedrockFoundationModel.TITAN_EMBED_TEXT_V2_1024,
            instruction="Use this knowledge base to answer questions about AWS Well Architected Framework Review (WAFR).",
            description="This knowledge base contains AWS Well Architected Framework Review (WAFR) reference documents",
        )

        random_id = str(uuid.uuid4())[:8]  # First 8 characters of a UUID

        KB_ID = kb.knowledge_base_id

        # Create DynamoDB table for lens metadata
        lens_metadata_table = dynamodb.Table(
            self,
            "LensMetadataTable",
            partition_key=dynamodb.Attribute(
                name="lensAlias", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery=True,
        )

        # Create S3 bucket where well architected reference docs are stored
        wafrReferenceDocsBucket = s3.Bucket(
            self,
            "wafr-accelerator-kb-docs",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            enforce_ssl=True,
        )

        # Uploading WAFR docs to the corresponding S3 bucket [wafrReferenceDocsBucket]
        wafrReferenceDeploy = s3deploy.BucketDeployment(
            self,
            "uploadwellarchitecteddocs",
            sources=[s3deploy.Source.asset("../ecs_fargate_app/well_architected_docs")],
            destination_bucket=wafrReferenceDocsBucket,
            destination_key_prefix="wellarchitected",
        )

        WA_DOCS_BUCKET_NAME = wafrReferenceDocsBucket.bucket_name

        # Adds the created S3 bucket [wafrReferenceDocsBucket] as a Data Source for Bedrock KB
        kbDataSource = bedrock.S3DataSource(
            self,
            "DataSource",
            bucket=wafrReferenceDocsBucket,
            knowledge_base=kb,
            data_source_name="wafr-reference-docs",
            chunking_strategy=bedrock.ChunkingStrategy.hierarchical(
                overlap_tokens=60, max_parent_token_size=2000, max_child_token_size=800
            ),
        )

        # Data Ingestion Params
        dataSourceIngestionParams = {
            "dataSourceId": kbDataSource.data_source_id,
            "knowledgeBaseId": KB_ID,
        }

        # Define a custom resource to make an AwsSdk startIngestionJob call
        ingestion_job_cr = cr.AwsCustomResource(
            self,
            "IngestionCustomResource",
            on_create=cr.AwsSdkCall(
                service="bedrock-agent",
                action="startIngestionJob",
                parameters=dataSourceIngestionParams,
                physical_resource_id=cr.PhysicalResourceId.of("Parameter.ARN"),
            ),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(
                resources=cr.AwsCustomResourcePolicy.ANY_RESOURCE
            ),
        )

        # Params for the test Well-Architected Workload
        test_workload_region = Stack.of(self).region
        waToolWorkloadParams = {
            "WorkloadName": f"DO-NOT-DELETE_WAIaCAnalyzerAppKB_{test_workload_region}_{random_id}",
            "Description": f"DO-NOT-DELETE_WAIaCAnalyzerAppKB_{test_workload_region} TestWorkload for WA IoC Analyzer App KB",
            "ReviewOwner": "WA IoC Analyzer App KB",
            "Environment": "PREPRODUCTION",
            "AwsRegions": [test_workload_region],
            "Lenses": ["wellarchitected"],
            "ClientRequestToken": random_id,
        }
        # Create a test Well-Architected Workload
        workload_cr = cr.AwsCustomResource(
            self,
            "TestWorkload",
            on_create=cr.AwsSdkCall(
                service="wellarchitected",
                action="createWorkload",
                parameters=waToolWorkloadParams,
                physical_resource_id=cr.PhysicalResourceId.from_response("WorkloadId"),
                output_paths=["WorkloadId"],
            ),
            on_update=cr.AwsSdkCall(
                service="wellarchitected",
                action="listLensReviews",
                parameters={
                    "WorkloadId": cr.PhysicalResourceIdReference(),
                },
                physical_resource_id=cr.PhysicalResourceId.from_response("WorkloadId"),
                output_paths=["WorkloadId"],
            ),
            on_delete=cr.AwsSdkCall(
                service="wellarchitected",
                action="deleteWorkload",
                parameters={
                    "WorkloadId": cr.PhysicalResourceIdReference(),
                    "ClientRequestToken": random_id,
                },
            ),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(
                resources=cr.AwsCustomResourcePolicy.ANY_RESOURCE
            ),
        )

        # Lambda function to refresh and sync Knowledge Base with data source
        kb_lambda_synchronizer = lambda_.Function(
            self,
            "KbLambdaSynchronizer",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="kb_synchronizer.handler",
            code=lambda_.Code.from_asset(
                "../ecs_fargate_app/lambda_kb_synchronizer",
                bundling=cdk.BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        "pip install --no-cache -r requirements.txt -t /asset-output && cp -au . /asset-output",
                    ],
                ),
            ),
            environment={
                "KNOWLEDGE_BASE_ID": KB_ID,
                "DATA_SOURCE_ID": kbDataSource.data_source_id,
                "WA_DOCS_BUCKET_NAME": wafrReferenceDocsBucket.bucket_name,
                "WORKLOAD_ID": workload_cr.get_response_field("WorkloadId"),
                "LENS_METADATA_TABLE": lens_metadata_table.table_name,
            },
            timeout=Duration.minutes(15),
        )

        # Grant permissions to the KB synchronizer Lambda
        kb_lambda_synchronizer.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:StartIngestionJob"],
                resources=[
                    f"arn:aws:bedrock:{self.region}:{self.account}:knowledge-base/{KB_ID}"
                ],
            )
        )
        kb_lambda_synchronizer.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "wellarchitected:GetLensReview",
                    "wellarchitected:ListAnswers",
                    "wellarchitected:UpgradeLensReview",
                    "wellarchitected:AssociateLenses",
                    "wellarchitected:DisassociateLenses",
                ],
                resources=["*"],
            )
        )

        # Grant Lambda access to the lens metadata table
        lens_metadata_table.grant_read_write_data(kb_lambda_synchronizer)

        # Grant Lambda access to the WA docs bucket
        wafrReferenceDocsBucket.grant_put(kb_lambda_synchronizer)

        # Create EventBridge rule to trigger KbLambdaSynchronizer weekly on Mondays
        events.Rule(
            self,
            "WeeklyIngestionRule",
            schedule=events.Schedule.cron(
                minute="0", hour="0", month="*", week_day="2", year="*"
            ),
            targets=[targets.LambdaFunction(kb_lambda_synchronizer)],
        )

        deployment_timestamp = int(time.time())

        # Custom resource to trigger the KB Lambda synchronizer during deployment
        kb_lambda_trigger_cr = cr.AwsCustomResource(
            self,
            "KbLambdaTrigger",
            on_create=cr.AwsSdkCall(
                service="Lambda",
                action="invoke",
                parameters={
                    "FunctionName": kb_lambda_synchronizer.function_name,
                    "InvocationType": "Event",
                },
                physical_resource_id=cr.PhysicalResourceId.of(
                    f"KbLambdaSynchronizerTrigger-{deployment_timestamp}"
                ),
            ),
            on_update=cr.AwsSdkCall(
                service="Lambda",
                action="invoke",
                parameters={
                    "FunctionName": kb_lambda_synchronizer.function_name,
                    "InvocationType": "Event",
                },
                physical_resource_id=cr.PhysicalResourceId.of(
                    f"KbLambdaSynchronizerTrigger-{deployment_timestamp}"
                ),
            ),
            # Use explicit IAM policy statement instead of from_sdk_calls
            policy=cr.AwsCustomResourcePolicy.from_statements(
                [
                    iam.PolicyStatement(
                        actions=["lambda:InvokeFunction"],
                        resources=[kb_lambda_synchronizer.function_arn],
                    )
                ]
            ),
        )

        # Define storage resources if deploy_storage is true
        analysis_storage_bucket = None
        analysis_metadata_table = None

        if deploy_storage:
            # Create S3 bucket for storing analysis results
            analysis_storage_bucket = s3.Bucket(
                self,
                "AnalysisStorageBucket",
                removal_policy=RemovalPolicy.DESTROY,
                auto_delete_objects=True,
                enforce_ssl=True,
            )

            # Create DynamoDB table for metadata
            analysis_metadata_table = dynamodb.Table(
                self,
                "AnalysisMetadataTable",
                partition_key=dynamodb.Attribute(
                    name="userId", type=dynamodb.AttributeType.STRING
                ),
                sort_key=dynamodb.Attribute(
                    name="fileId", type=dynamodb.AttributeType.STRING
                ),
                billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
                removal_policy=RemovalPolicy.DESTROY,
                point_in_time_recovery=True,
            )

        # Output the Knowledge Base ID for .env configuration
        CfnOutput(
            self,
            "KnowledgeBaseID",
            value=KB_ID,
            description="ID of the Bedrock knowledge base",
        )

        # Output the S3 bucket name for .env configuration
        CfnOutput(
            self,
            "WellArchitectedDocsS3Bucket",
            value=WA_DOCS_BUCKET_NAME,
            description="S3 bucket with well-architected documents",
        )

        # Output the lens metadata table name for .env configuration
        CfnOutput(
            self,
            "LensMetadataTableName",
            value=lens_metadata_table.table_name,
            description="DynamoDB table for lens metadata",
        )

        # Output storage resource information if deployed
        if deploy_storage and analysis_storage_bucket and analysis_metadata_table:
            CfnOutput(
                self,
                "AnalysisStorageBucketName",
                value=analysis_storage_bucket.bucket_name,
                description="S3 bucket for storing analysis results",
            )

            CfnOutput(
                self,
                "AnalysisMetadataTableName",
                value=analysis_metadata_table.table_name,
                description="DynamoDB table for analysis metadata",
            )

        # Node dependencies
        kbDataSource.node.add_dependency(wafrReferenceDocsBucket)
        ingestion_job_cr.node.add_dependency(kb)
        kb_lambda_synchronizer.node.add_dependency(kb)
        kb_lambda_synchronizer.node.add_dependency(kbDataSource)
        kb_lambda_synchronizer.node.add_dependency(wafrReferenceDocsBucket)
        kb_lambda_synchronizer.node.add_dependency(workload_cr)
        kb_lambda_trigger_cr.node.add_dependency(kb_lambda_synchronizer)
