import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';

const PREFIX = 'bedrock-nova2-metal-defect-inspection';
const MODEL_ID = 'jp.amazon.nova-2-lite-v1:0';

export class MetalDefectInspectionStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const suffix = this.node.tryGetContext('suffix') ?? this.account;

    const detectFn = new lambda.Function(this, 'DetectFunction', {
      functionName: `${PREFIX}-detect`,
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('lambda'),
      timeout: cdk.Duration.seconds(60),
      memorySize: 256,
      environment: { MODEL_ID },
    });

    detectFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['bedrock:InvokeModel'],
        resources: [
          `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/${MODEL_ID}`,
          'arn:aws:bedrock:*::foundation-model/amazon.nova-2-lite-v1:0',
        ],
      }),
    );

    const api = new apigw.RestApi(this, 'Api', {
      restApiName: `${PREFIX}-api`,
      defaultCorsPreflightOptions: {
        allowOrigins: apigw.Cors.ALL_ORIGINS,
        allowMethods: apigw.Cors.ALL_METHODS,
        allowHeaders: ['Content-Type'],
      },
    });
    api.root.addResource('detect').addMethod('POST', new apigw.LambdaIntegration(detectFn));

    const frontendBucket = new s3.Bucket(this, 'FrontendBucket', {
      bucketName: `${PREFIX}-${suffix}-frontend`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(frontendBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      },
      defaultRootObject: 'index.html',
    });

    new s3deploy.BucketDeployment(this, 'DeployFrontend', {
      sources: [
        s3deploy.Source.asset('frontend'),
        s3deploy.Source.data('config.js', `window.API_URL = '${api.url}detect';`),
      ],
      destinationBucket: frontendBucket,
      distribution,
      distributionPaths: ['/*'],
    });

    new cdk.CfnOutput(this, 'ApiUrl', { value: api.url });
    new cdk.CfnOutput(this, 'WebsiteUrl', {
      value: `https://${distribution.distributionDomainName}`,
    });
  }
}
