import * as cdk from 'aws-cdk-lib';
import { MetalDefectInspectionStack } from '../lib/stack';

const app = new cdk.App();
new MetalDefectInspectionStack(app, 'MetalDefectInspectionStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION ?? 'ap-northeast-1',
  },
});
