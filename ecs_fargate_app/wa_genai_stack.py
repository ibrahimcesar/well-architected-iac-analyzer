"""CDK stack for hosting react app in ECS and Fargate"""

import configparser
import os
import platform
import time
import uuid

import aws_cdk as cdk
import aws_cdk.aws_servicediscovery as servicediscovery
from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_certificatemanager as aws_certificatemanager
from aws_cdk import aws_cognito as aws_cognito
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ecs_patterns as ecs_patterns
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_elasticloadbalancingv2_actions as actions
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from aws_cdk import aws_secretsmanager as aws_secretsmanager
from aws_cdk import custom_resources as cr
from aws_cdk.aws_ecr_assets import DockerImageAsset, Platform
from cdklabs.generative_ai_cdk_constructs import bedrock
from constructs import Construct


class WAGenAIStack(Stack):

    def parse_auth_config(self, config: configparser.ConfigParser):
        auth_config = {
            "enabled": config.getboolean("settings", "authentication", fallback=False),
            "authType": config.get("settings", "auth_type", fallback="none"),
            "certificateArn": config.get("settings", "certificate_arn", fallback=""),
        }

        if not auth_config["enabled"]:
            return auth_config

        if not auth_config["certificateArn"]:
            raise ValueError(
                "certificate_arn is required when authentication is enabled"
            )

        if auth_config["authType"] == "new-cognito":
            auth_config["cognito"] = {
                "domainPrefix": config.get("settings", "cognito_domain_prefix"),
                "callbackUrls": config.get("settings", "callback_urls").split(","),
                "logoutUrl": config.get("settings", "logout_url"),
            }
        elif auth_config["authType"] == "existing-cognito":
            auth_config["cognito"] = {
                "userPoolArn": config.get("settings", "existing_user_pool_arn"),
                "clientId": config.get("settings", "existing_user_pool_client_id"),
                "domain": config.get("settings", "existing_user_pool_domain"),
                "logoutUrl": config.get("settings", "existing_cognito_logout_url"),
            }
        elif auth_config["authType"] == "oidc":
            auth_config["oidc"] = {
                "issuer": config.get("settings", "oidc_issuer"),
                "clientId": config.get("settings", "oidc_client_id"),
                "authorizationEndpoint": config.get(
                    "settings", "oidc_authorization_endpoint"
                ),
                "tokenEndpoint": config.get("settings", "oidc_token_endpoint"),
                "userInfoEndpoint": config.get("settings", "oidc_user_info_endpoint"),
                "logoutUrl": config.get("settings", "oidc_logout_url"),
            }

        return auth_config

    def create_alb_auth_action(
        self,
        auth_config: dict,
        alb_domain: str,
        existing_user_pool=None,
        existing_client=None,
        existing_domain=None,
    ) -> elbv2.ListenerAction:
        if auth_config["authType"] == "new-cognito":
            # Use existing user pool, client, and domain if provided
            if existing_user_pool and existing_client and existing_domain:
                user_pool = existing_user_pool
                client = existing_client
                domain = existing_domain
            else:
                # Create user pool
                user_pool = aws_cognito.UserPool(
                    self,
                    "WAAnalyzerUserPool",
                    user_pool_name="WAAnalyzerUserPool",
                    self_sign_up_enabled=False,
                    sign_in_aliases=aws_cognito.SignInAliases(email=True),
                    standard_attributes=aws_cognito.StandardAttributes(
                        email=aws_cognito.StandardAttribute(required=True)
                    ),
                )

                # Create the domain
                domain = user_pool.add_domain(
                    "CognitoDomain",
                    cognito_domain=aws_cognito.CognitoDomainOptions(
                        domain_prefix=auth_config["cognito"]["domainPrefix"]
                    ),
                )

                # Create the client
                client = user_pool.add_client(
                    "WAAnalyzerClient",
                    generate_secret=True,
                    o_auth=aws_cognito.OAuthSettings(
                        flows=aws_cognito.OAuthFlows(authorization_code_grant=True),
                        scopes=[aws_cognito.OAuthScope.OPENID],
                        callback_urls=auth_config["cognito"]["callbackUrls"],
                        logout_urls=auth_config["cognito"]["logoutUrl"],
                    ),
                    auth_flows=aws_cognito.AuthFlow(user_password=True, user_srp=True),
                    prevent_user_existence_errors=True,
                )

            return actions.AuthenticateCognitoAction(
                user_pool=user_pool,
                user_pool_client=client,
                user_pool_domain=domain,
                next=elbv2.ListenerAction.forward([self.frontend_target_group]),
            )
        elif auth_config["authType"] == "existing-cognito":
            user_pool = aws_cognito.UserPool.from_user_pool_arn(
                self, "ImportedUserPool", auth_config["cognito"]["userPoolArn"]
            )

            domain = aws_cognito.UserPoolDomain.from_domain_name(
                self,
                "ImportedDomain",
                user_pool_domain_name=auth_config["cognito"]["domain"],
            )

            user_pool_client = aws_cognito.UserPoolClient.from_user_pool_client_id(
                self,
                "ImportedUserPoolClient",
                user_pool_client_id=auth_config["cognito"]["clientId"],
            )

            return actions.AuthenticateCognitoAction(
                user_pool=user_pool,
                user_pool_client=user_pool_client,
                user_pool_domain=domain,
                next=elbv2.ListenerAction.forward([self.frontend_target_group]),
            )
        elif auth_config["authType"] == "oidc":
            # OIDC configuration

            # Retrieve existing secret "WAIaCAnalyzerOIDCSecret" (See README for more details about creating this secret prior deployment)
            oidc_secret = aws_secretsmanager.Secret.from_secret_name_v2(
                self, "OidcClientSecret", "WAIaCAnalyzerOIDCSecret"
            )

            return elbv2.ListenerAction.authenticate_oidc(
                authorization_endpoint=auth_config["oidc"]["authorizationEndpoint"],
                client_id=auth_config["oidc"]["clientId"],
                client_secret=oidc_secret.secret_value,
                issuer=auth_config["oidc"]["issuer"],
                token_endpoint=auth_config["oidc"]["tokenEndpoint"],
                user_info_endpoint=auth_config["oidc"]["userInfoEndpoint"],
                next=elbv2.ListenerAction.forward([self.frontend_target_group]),
            )

    def create_stack_cleanup_resources(self):
        """
        Create resources for automatic stack cleanup via EventBridge and Lambda
        """
        # Get deployment stack name from environment
        deployment_stack_name = os.environ.get("DEPLOYMENT_STACK_NAME", "")

        # Validate that deployment_stack_name is provided
        if not deployment_stack_name:
            raise ValueError(
                "DEPLOYMENT_STACK_NAME environment variable must be provided"
            )

        # Create Lambda execution role
        lambda_role = iam.Role(
            self,
            "StackCleanupLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )

        # Add CloudWatch Logs permissions
        lambda_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaBasicExecutionRole"
            )
        )

        # Add CloudFormation permissions to delete the specific stack
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudformation:DeleteStack"],
                resources=[
                    f"arn:aws:cloudformation:{self.region}:{self.account}:stack/{deployment_stack_name}/*",
                ],
            )
        )

        # Read-only operations needed during stack deletion
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "cloudformation:DescribeStacks",
                    "cloudformation:GetTemplate",
                    "ec2:DescribeInstances",
                    "ec2:DescribeInternetGateways",
                    "ec2:DescribeRouteTables",
                    "ec2:DescribeSecurityGroups",
                    "ec2:DescribeSubnets",
                    "ec2:DescribeVpcs",
                    "iam:GetInstanceProfile",
                    "iam:GetRole",
                    "iam:ListAttachedRolePolicies",
                    "iam:ListRolePolicies",
                    "ssm:DescribeAssociation",
                    "ssm:DescribePatchBaselines",
                    "ssm:GetDocument",
                    "ssm:ListInstanceAssociations",
                    "sts:AssumeRole",
                    "sts:GetCallerIdentity",
                    "tagging:GetResources",
                ],
                resources=["*"],
            )
        )

        # EC2, SSM, Logs write operations - With resource tag condition for specific stack
        tag_conditions = {
            "StringEquals": {
                "aws:ResourceTag/aws:cloudformation:stack-name": [deployment_stack_name]
            }
        }

        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ec2:DeleteInternetGateway",
                    "ec2:DeleteRoute",
                    "ec2:DeleteRouteTable",
                    "ec2:DeleteSecurityGroup",
                    "ec2:DeleteSubnet",
                    "ec2:DeleteVpc",
                    "ec2:DetachInternetGateway",
                    "ec2:DisassociateRouteTable",
                    "ec2:TerminateInstances",
                    "ssm:GetDeployablePatchSnapshotForInstance",
                    "ssm:PutComplianceItems",
                    "ssm:PutInventory",
                    "ssm:RegisterManagedInstance",
                    "ssm:UpdateInstanceAssociationStatus",
                    "ssm:UpdateInstanceInformation",
                    "logs:CreateLogStream",
                ],
                resources=["*"],
                conditions=tag_conditions,
            )
        )

        # IAM permissions for the specific stack resources
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "iam:DeleteRole",
                    "iam:DeleteRolePolicy",
                    "iam:DetachRolePolicy",
                ],
                resources=[
                    f"arn:aws:iam::{self.account}:role/{deployment_stack_name}*"
                ],
            )
        )

        lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "iam:DeleteInstanceProfile",
                    "iam:RemoveRoleFromInstanceProfile",
                ],
                resources=[
                    f"arn:aws:iam::{self.account}:instance-profile/{deployment_stack_name}*"
                ],
            )
        )

        # Create Lambda function
        cleanup_lambda = lambda_.Function(
            self,
            "StackCleanupLambda",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="stack_cleanup.handler",
            code=lambda_.Code.from_asset(
                "ecs_fargate_app/lambda_stack_cleanup",
                bundling=cdk.BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        "pip install --no-cache -r requirements.txt -t /asset-output && cp -au . /asset-output",
                    ],
                ),
            ),
            timeout=Duration.minutes(10),
            role=lambda_role,
            environment={
                # Pass the deployment stack name to the Lambda
                "DEPLOYMENT_STACK_NAME": deployment_stack_name
            },
        )

        # Create EventBridge rule that only matches the specific stack name
        rule = events.Rule(
            self,
            "StackCleanupRule",
            event_pattern=events.EventPattern(
                source=["iac.analyzer.deployment"],
                detail_type=["Stack Cleanup Request"],
                detail={"stack-name": [deployment_stack_name]},
            ),
        )

        # Add Lambda as target for the rule
        rule.add_target(targets.LambdaFunction(cleanup_lambda))

    def __init__(self, scope: Construct, construct_id: str, **kwarg) -> None:
        super().__init__(scope, construct_id, **kwarg)

        # Read config.ini
        config = configparser.ConfigParser()
        config.read("config.ini")
        model_id = config["settings"]["model_id"]
        public_lb = config["settings"].getboolean("public_load_balancer", False)

        # Check if auto-cleanup is enabled (from environment variable set by deploy script)
        auto_cleanup = os.environ.get("AUTO_CLEANUP", "false").lower() == "true"
        deployment_stack_name = os.environ.get("DEPLOYMENT_STACK_NAME", "")

        # Parse authentication config
        auth_config = self.parse_auth_config(config)

        # Create sign out URL based on auth type
        sign_out_url = ""
        if auth_config["enabled"]:
            if auth_config["authType"] == "existing-cognito":
                # For Cognito, construct base sign out URL
                cognito_domain = auth_config["cognito"]["domain"]
                sign_out_url = (
                    f"https://{cognito_domain}/logout?"
                    f"client_id={auth_config['cognito']['clientId']}&"
                    f"logout_uri={auth_config['cognito']['logoutUrl']}&"
                    "response_type=code"
                )
            elif auth_config["authType"] == "oidc":
                # For OIDC, use the configured sign out endpoint if available
                sign_out_url = auth_config["oidc"]["logoutUrl"]

        random_id = str(uuid.uuid4())[:8]  # First 8 characters of a UUID

        platform_mapping = {
            "x86_64": {
                "fargate_architecture": ecs.CpuArchitecture.X86_64,
                "build_architecture": Platform.LINUX_AMD64,
                "build_architecture_argument": "amd64",
            },
            "arm64": {
                "fargate_architecture": ecs.CpuArchitecture.ARM64,
                "build_architecture": Platform.LINUX_ARM64,
                "build_architecture_argument": "arm64",
            },
            "aarch64": {
                "fargate_architecture": ecs.CpuArchitecture.ARM64,
                "build_architecture": Platform.LINUX_ARM64,
                "build_architecture_argument": "arm64",
            },
        }
        # Get architecture from platform (depending the machine that runs CDK)
        architecture = platform_mapping[platform.machine()]

        # Creates Bedrock KB using the generative_ai_cdk_constructs
        kb = bedrock.KnowledgeBase(
            self,
            "WAFR-KnowledgeBase",
            embeddings_model=bedrock.BedrockFoundationModel.TITAN_EMBED_TEXT_V2_1024,
            instruction="Use this knowledge base to answer questions about AWS Well Architected Framework Review (WAFR).",
            description="This knowledge base contains AWS Well Architected Framework Review (WAFR) reference documents",
        )

        KB_ID = kb.knowledge_base_id

        # Create S3 bucket and DynamoDB table for storage layer
        # Create S3 bucket for storing analysis results
        analysis_storage_bucket = s3.Bucket(
            self,
            "AnalysisStorageBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            enforce_ssl=True,
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.GET, s3.HttpMethods.PUT],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                )
            ],
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

        # Create S3 bucket for vector storage
        vectors_bucket = s3.Bucket(
            self,
            "VectorStorageBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            enforce_ssl=True,
        )

        # Uploading WAFR docs to the corresponding S3 bucket [wafrReferenceDocsBucket]
        wafrReferenceDeploy = s3deploy.BucketDeployment(
            self,
            "uploadwellarchitecteddocs",
            sources=[s3deploy.Source.asset("ecs_fargate_app/well_architected_docs")],
            destination_bucket=wafrReferenceDocsBucket,
            destination_key_prefix="wellarchitected",
        )

        WA_DOCS_BUCKET_NAME = wafrReferenceDocsBucket.bucket_name

        # Adds the created S3 bucket [docBucket] as a Data Source for Bedrock KB
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
            "WorkloadName": f"DO-NOT-DELETE_WAIaCAnalyzerApp_{test_workload_region}_{random_id}",
            "Description": f"DO-NOT-DELETE_WAIaCAnalyzerApp_{test_workload_region} TestWorkload for WA IoC Analyzer App",
            "ReviewOwner": "WA IoC Analyzer App",
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

        # Create vector processor Lambda
        vector_processor = lambda_.Function(
            self,
            "VectorProcessor",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="vector_processor.handler",
            code=lambda_.Code.from_asset(
                "ecs_fargate_app/lambda_vector_processor",
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
                "VECTORS_BUCKET": vectors_bucket.bucket_name,
                "SOURCE_BUCKET": wafrReferenceDocsBucket.bucket_name,
                "EMBEDDING_MODEL": "amazon.titan-embed-text-v2:0",
                "EMBEDDING_DIMENSIONS": "1024",
            },
            timeout=Duration.minutes(15),
        )

        # Grant permissions to vector processor
        vectors_bucket.grant_read_write(vector_processor)
        wafrReferenceDocsBucket.grant_read(vector_processor)
        vector_processor.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=["*"]
            )
        )

        # Lambda function to refresh and sync Knowledge Base with data source
        kb_lambda_synchronizer = lambda_.Function(
            self,
            "KbLambdaSynchronizer",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="kb_synchronizer.handler",
            code=lambda_.Code.from_asset(
                "ecs_fargate_app/lambda_kb_synchronizer",
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

        frontend_image = DockerImageAsset(
            self,
            "FrontendImage",
            directory="ecs_fargate_app",
            file="finch/frontend.Dockerfile",
            platform=architecture["build_architecture"],
            build_args={
                "BUILDKIT_INLINE_CACHE": "1",
                "PLATFORM": architecture["build_architecture_argument"],
            },
        )

        backend_image = DockerImageAsset(
            self,
            "BackendImage",
            directory="ecs_fargate_app",
            file="finch/backend.Dockerfile",
            platform=architecture["build_architecture"],
            build_args={
                "BUILDKIT_INLINE_CACHE": "1",
                "PLATFORM": architecture["build_architecture_argument"],
            },
        )

        # create app execute role
        app_execute_role = iam.Role(
            self,
            "AppExecuteRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        app_execute_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ecr:GetAuthorizationToken",
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                resources=["*"],
            )
        )

        # Add policy statements to the IAM role
        app_execute_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "wellarchitected:GetLensReview",
                    "wellarchitected:ListAnswers",
                    "wellarchitected:GetWorkload",
                    "wellarchitected:UpdateAnswer",
                    "wellarchitected:CreateMilestone",
                    "wellarchitected:GetLensReviewReport",
                    "wellarchitected:AssociateLenses",
                ],
                resources=["*"],
            )
        )
        app_execute_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "wellarchitected:CreateWorkload",
                    "wellarchitected:TagResource",
                ],
                resources=["*"],
                conditions={
                    "StringLike": {
                        "aws:RequestTag/WorkloadName": [
                            "DO_NOT_DELETE_temp_IaCAnalyzer_*",
                            "IaCAnalyzer_*",
                        ]
                    }
                },
            )
        )
        app_execute_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "wellarchitected:DeleteWorkload",
                ],
                resources=["*"],
                conditions={
                    "StringLike": {
                        "aws:ResourceTag/WorkloadName": [
                            "DO_NOT_DELETE_temp_IaCAnalyzer_*",
                            "IaCAnalyzer_*",
                        ]
                    }
                },
            )
        )
        app_execute_role.add_to_policy(
            iam.PolicyStatement(actions=["bedrock:InvokeModel"], resources=["*"])
        )
        app_execute_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:ListBucket"],
                resources=[
                    f"arn:aws:s3:::{WA_DOCS_BUCKET_NAME}",
                    f"arn:aws:s3:::{WA_DOCS_BUCKET_NAME}/*",
                ],
            )
        )
        app_execute_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonBedrockFullAccess")
        )

        # Adding DDB and S3 data store bucket permission for app_execute_role
        app_execute_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "s3:PutObject",
                    "s3:GetObject",
                    "s3:DeleteObject",
                    "s3:ListBucket",
                ],
                resources=[
                    analysis_storage_bucket.bucket_arn,
                    f"{analysis_storage_bucket.bucket_arn}/*",
                ],
            )
        )

        app_execute_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "dynamodb:PutItem",
                    "dynamodb:GetItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:Query",
                    "dynamodb:UpdateItem",
                ],
                resources=[
                    analysis_metadata_table.table_arn,
                    f"{analysis_metadata_table.table_arn}/index/*",
                ],
            )
        )

        # Grant permissions to scan the lens metadata table
        app_execute_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "dynamodb:Scan",
                    "dynamodb:GetItem",
                    "dynamodb:Query",
                ],
                resources=[
                    lens_metadata_table.table_arn,
                    f"{lens_metadata_table.table_arn}/index/*",
                ],
            )
        )

        # Grant backend access to vectors bucket
        app_execute_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:ListBucket"],
                resources=[
                    vectors_bucket.bucket_arn,
                    f"{vectors_bucket.bucket_arn}/*",
                ],
            )
        )

        # Create VPC to host the ECS cluster
        vpc = ec2.Vpc(
            self,
            "ECSVpc",
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public", subnet_type=ec2.SubnetType.PUBLIC
                ),
                ec2.SubnetConfiguration(
                    name="private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
                ),
            ],
        )

        # Capture the public subnets
        public_subnets = vpc.select_subnets(subnet_type=ec2.SubnetType.PUBLIC)

        # Create ECS Cluster
        ecs_cluster = ecs.Cluster(self, "AppCluster", vpc=vpc, container_insights=True)

        # Add ECS Service Discovery namespace
        namespace = servicediscovery.PrivateDnsNamespace(
            self, "ServiceDiscovery", name="internal", vpc=vpc
        )

        # Create security groups for frontend and backend
        frontend_security_group = ec2.SecurityGroup(
            self,
            "FrontendSecurityGroup",
            vpc=vpc,
            description="Security group for frontend service",
        )

        backend_security_group = ec2.SecurityGroup(
            self,
            "BackendSecurityGroup",
            vpc=vpc,
            description="Security group for backend service",
        )

        # Create frontend service with ALB
        if auth_config["enabled"]:
            # Create HTTPS listener with authentication
            certificate = aws_certificatemanager.Certificate.from_certificate_arn(
                self, "ALBCertificate", auth_config["certificateArn"]
            )

            frontend_service = ecs_patterns.ApplicationLoadBalancedFargateService(
                self,
                "FrontendService",
                cluster=ecs_cluster,
                runtime_platform=ecs.RuntimePlatform(
                    operating_system_family=ecs.OperatingSystemFamily.LINUX,
                    cpu_architecture=architecture["fargate_architecture"],
                ),
                task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                    image=ecs.ContainerImage.from_docker_image_asset(frontend_image),
                    container_port=8080,
                    environment={
                        # Use service discovery DNS name
                        "VITE_API_URL": f"http://backend.internal:3000"
                    },
                ),
                public_load_balancer=public_lb,
                security_groups=[frontend_security_group],
                certificate=certificate,
                redirect_http=True,
                ssl_policy=elbv2.SslPolicy.RECOMMENDED_TLS,
            )

            # Store reference to frontend target group
            self.frontend_target_group = frontend_service.target_group

            # Create Cognito resources once if using new Cognito
            user_pool = None
            client = None
            domain = None
            if auth_config["authType"] == "new-cognito":
                user_pool = aws_cognito.UserPool(
                    self,
                    "WAAnalyzerUserPool",
                    user_pool_name="WAAnalyzerUserPool",
                    self_sign_up_enabled=False,
                    sign_in_aliases=aws_cognito.SignInAliases(email=True),
                    standard_attributes=aws_cognito.StandardAttributes(
                        email=aws_cognito.StandardAttribute(required=True)
                    ),
                )

                domain = user_pool.add_domain(
                    "CognitoDomain",
                    cognito_domain=aws_cognito.CognitoDomainOptions(
                        domain_prefix=auth_config["cognito"]["domainPrefix"]
                    ),
                    managed_login_version=aws_cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN,
                )

                # Convert logoutUrl to array
                logout_urls = (
                    [auth_config["cognito"]["logoutUrl"]]
                    if auth_config["cognito"]["logoutUrl"]
                    else []
                )

                client = user_pool.add_client(
                    "WAAnalyzerClient",
                    generate_secret=True,
                    o_auth=aws_cognito.OAuthSettings(
                        flows=aws_cognito.OAuthFlows(authorization_code_grant=True),
                        scopes=[aws_cognito.OAuthScope.OPENID],
                        callback_urls=auth_config["cognito"]["callbackUrls"],
                        logout_urls=logout_urls,
                    ),
                    auth_flows=aws_cognito.AuthFlow(user_password=True, user_srp=True),
                    prevent_user_existence_errors=True,
                )

                # Create branding for the managed login UI
                aws_cognito.CfnManagedLoginBranding(
                    self,
                    "WAAnalyzerManagedLoginBranding",
                    user_pool_id=user_pool.user_pool_id,
                    client_id=client.user_pool_client_id,
                    use_cognito_provided_values=True,
                )

                # Update sign_out_url for new Cognito setup
                sign_out_url = (
                    f"https://{auth_config['cognito']['domainPrefix']}.auth.{Stack.of(self).region}.amazoncognito.com/logout?"
                    f"client_id={client.user_pool_client_id}&"
                    f"logout_uri={auth_config['cognito']['logoutUrl']}&"
                    "response_type=code"
                )

            # Modify the default actions of the existing HTTPS listener
            https_listener = frontend_service.listener

            # Set the authenticate action as the default action
            auth_action = self.create_alb_auth_action(
                auth_config,
                frontend_service.load_balancer.load_balancer_dns_name,
                user_pool,
                client,
                domain,
            )

            # Remove any existing actions and add the auth action as the only action
            https_listener.add_action("DefaultAuth", action=auth_action)
        else:
            # HTTP-only ALB creation
            frontend_service = ecs_patterns.ApplicationLoadBalancedFargateService(
                self,
                "FrontendService",
                cluster=ecs_cluster,
                runtime_platform=ecs.RuntimePlatform(
                    operating_system_family=ecs.OperatingSystemFamily.LINUX,
                    cpu_architecture=architecture["fargate_architecture"],
                ),
                task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                    image=ecs.ContainerImage.from_docker_image_asset(frontend_image),
                    container_port=8080,
                    environment={
                        # Use service discovery DNS name
                        "VITE_API_URL": f"http://backend.internal:3000"
                    },
                ),
                public_load_balancer=public_lb,
                security_groups=[frontend_security_group],
            )
            # Store reference to frontend target group
            self.frontend_target_group = frontend_service.target_group

        # Set ALB idle timeout to 60 minutes
        frontend_service.load_balancer.set_attribute(
            "idle_timeout.timeout_seconds", "3600"
        )

        # Allow ALB to access frontend on port 8080
        frontend_security_group.add_ingress_rule(
            peer=frontend_service.load_balancer.connections.security_groups[
                0
            ],  # ALB security group
            connection=ec2.Port.tcp(8080),
            description="Allow ALB to access frontend",
        )

        # Allow frontend to access backend on port 3000
        backend_security_group.add_ingress_rule(
            peer=frontend_security_group,
            connection=ec2.Port.tcp(3000),
            description="Allow frontend to access backend",
        )

        # Get the ALB DNS name after frontend service is created
        alb_dns = frontend_service.load_balancer.load_balancer_dns_name

        # Configure health check for ALB
        frontend_service.target_group.configure_health_check(path="/healthz")

        # Create backend service with service discovery
        backend_task_definition = ecs.FargateTaskDefinition(
            self,
            "BackendTaskDef",
            runtime_platform=ecs.RuntimePlatform(
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
                cpu_architecture=architecture["fargate_architecture"],
            ),
            task_role=app_execute_role,
        )

        backend_container = backend_task_definition.add_container(
            "BackendContainer",
            image=ecs.ContainerImage.from_docker_image_asset(backend_image),
            environment={
                "WA_DOCS_S3_BUCKET": WA_DOCS_BUCKET_NAME,
                "KNOWLEDGE_BASE_ID": KB_ID,
                "MODEL_ID": model_id,
                "AWS_REGION": Stack.of(self).region,
                "FRONTEND_URL": f"http://{alb_dns}",
                "AUTH_ENABLED": str(auth_config["enabled"]).lower(),
                "AUTH_SIGN_OUT_URL": sign_out_url,
            },
            logging=ecs.LogDriver.aws_logs(stream_prefix="backend"),
        )

        backend_container.add_port_mappings(ecs.PortMapping(container_port=3000))

        # Environment variables for the backend service when auth is enabled
        backend_container.add_environment("STORAGE_ENABLED", "true")

        backend_container.add_environment(
            "ANALYSIS_STORAGE_BUCKET", analysis_storage_bucket.bucket_name
        )
        backend_container.add_environment(
            "ANALYSIS_METADATA_TABLE", analysis_metadata_table.table_name
        )
        backend_container.add_environment(
            "LENS_METADATA_TABLE", lens_metadata_table.table_name
        )
        backend_container.add_environment(
            "VECTORS_BUCKET", vectors_bucket.bucket_name
        )
        backend_container.add_environment(
            "EMBEDDING_MODEL", "amazon.titan-embed-text-v2:0"
        )
        backend_container.add_environment(
            "EMBEDDING_DIMENSIONS", "1024"
        )
        backend_container.add_environment(
            "USE_VECTOR_SEARCH", "false"
        )

        # Create the backend service
        backend_service = ecs.FargateService(
            self,
            "BackendService",
            cluster=ecs_cluster,
            task_definition=backend_task_definition,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[backend_security_group],
        )

        # Add service discovery
        backend_service.enable_cloud_map(cloud_map_namespace=namespace, name="backend")

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

        # Migration Lambda function for transitioning from single-lens to multi-lens storage structure
        # The new multi-lenses support introduced on 14-April-2025 is a breaking change. This function is meant to support a seamless transition from previous single-lens (wellarchitected) storage structure to the new multi-lens structure.
        # The Lambda will only run at cdk deployment time once and only for deployments where the old single-lens structure is detected.
        migration_lambda = lambda_.Function(
            self,
            "MigrationLambda",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="migration.handler",
            code=lambda_.Code.from_asset(
                "ecs_fargate_app/lambda_migration",
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
                "ANALYSIS_METADATA_TABLE": analysis_metadata_table.table_name,
                "ANALYSIS_STORAGE_BUCKET": analysis_storage_bucket.bucket_name,
                "WA_DOCS_BUCKET_NAME": wafrReferenceDocsBucket.bucket_name,
            },
            timeout=Duration.minutes(15),
        )

        # Grant DynamoDB permissions to migration Lambda
        analysis_metadata_table.grant_read_write_data(migration_lambda)

        # Grant S3 permissions for both buckets to migration Lambda
        analysis_storage_bucket.grant_read_write(migration_lambda)
        wafrReferenceDocsBucket.grant_read_write(migration_lambda)

        # Create a custom resource to trigger migration Lambda after KB synchronization
        migration_trigger_cr = cr.AwsCustomResource(
            self,
            "MigrationTrigger",
            on_create=cr.AwsSdkCall(
                service="Lambda",
                action="invoke",
                parameters={
                    "FunctionName": migration_lambda.function_name,
                    "InvocationType": "Event",
                },
                physical_resource_id=cr.PhysicalResourceId.of("MigrationLambdaTrigger"),
            ),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(
                resources=cr.AwsCustomResourcePolicy.ANY_RESOURCE
            ),
        )

        migration_lambda.grant_invoke(migration_trigger_cr)

        # Conditionally create stack cleanup resources if auto_cleanup is enabled
        if auto_cleanup:
            self.create_stack_cleanup_resources()

        # Output the frontend ALB DNS name
        cdk.CfnOutput(
            self,
            "FrontendURL",
            value=frontend_service.load_balancer.load_balancer_dns_name,
            description="Frontend application URL",
        )

        # Output the ID of the Bedrock knowledge base
        cdk.CfnOutput(
            self,
            "KnowledgeBaseID",
            value=KB_ID,
            description="ID of the Bedrock knowledge base",
        )

        # Output S3 bucket (Source of Bedrock knowledge base) with well-architected documents.
        cdk.CfnOutput(
            self,
            "WellArchitectedDocsS3Bucket",
            value=wafrReferenceDocsBucket.bucket_name,
            description="S3 bucket (Source of Bedrock knowledge base) with well-architected documents.",
        )

        # Output the VPC ID
        cdk.CfnOutput(
            self,
            "VpcId",
            value=vpc.vpc_id,
            description="ID of the VPC where the private ALB is created",
        )

        # Output the ID of the first public subnet
        cdk.CfnOutput(
            self,
            "PublicSubnetId",
            value=public_subnets.subnet_ids[0],
            description="ID of the public subnet created in the VPC",
        )

        # Add authentication configuration outputs
        if auth_config["enabled"]:
            cdk.CfnOutput(
                self,
                "AuthenticationType",
                value=auth_config["authType"],
                description="Type of authentication configured",
            )

            if auth_config["authType"] in ["new-cognito", "existing-cognito"]:
                cdk.CfnOutput(
                    self,
                    "CognitoDomain",
                    value=(
                        f"{auth_config['cognito']['domainPrefix']}.auth.{self.region}.amazoncognito.com"
                        if auth_config["authType"] == "new-cognito"
                        else auth_config["cognito"]["domain"]
                    ),
                    description="Cognito domain for authentication",
                )

        # Outputs for the storage resources
        cdk.CfnOutput(
            self,
            "AnalysisStorageBucketName",
            value=analysis_storage_bucket.bucket_name,
            description="S3 bucket for storing analysis results",
        )

        cdk.CfnOutput(
            self,
            "AnalysisMetadataTableName",
            value=analysis_metadata_table.table_name,
            description="DynamoDB table for analysis metadata",
        )

        # Output lens metadata table name
        cdk.CfnOutput(
            self,
            "LensMetadataTableName",
            value=lens_metadata_table.table_name,
            description="DynamoDB table for lens metadata",
        )

        # Output vectors bucket name
        cdk.CfnOutput(
            self,
            "VectorsBucketName",
            value=vectors_bucket.bucket_name,
            description="S3 bucket for vector storage",
        )

        # Output vector processor Lambda function name
        cdk.CfnOutput(
            self,
            "VectorProcessorFunctionName",
            value=vector_processor.function_name,
            description="Lambda function for processing vectors",
        )

        # Node dependencies
        kbDataSource.node.add_dependency(wafrReferenceDocsBucket)
        ingestion_job_cr.node.add_dependency(kb)
        kb_lambda_synchronizer.node.add_dependency(kb)
        kb_lambda_synchronizer.node.add_dependency(kbDataSource)
        kb_lambda_synchronizer.node.add_dependency(wafrReferenceDocsBucket)
        kb_lambda_synchronizer.node.add_dependency(workload_cr)

        kb_lambda_trigger_cr.node.add_dependency(kb_lambda_synchronizer)
        kb_lambda_trigger_cr.node.add_dependency(kb)
        kb_lambda_trigger_cr.node.add_dependency(kbDataSource)
        kb_lambda_trigger_cr.node.add_dependency(wafrReferenceDocsBucket)
        kb_lambda_trigger_cr.node.add_dependency(workload_cr)

        migration_trigger_cr.node.add_dependency(kb_lambda_trigger_cr)
        migration_lambda.node.add_dependency(analysis_metadata_table)
        migration_lambda.node.add_dependency(analysis_storage_bucket)
        migration_lambda.node.add_dependency(wafrReferenceDocsBucket)
