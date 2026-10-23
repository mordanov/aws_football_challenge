const assert = require('node:assert/strict');
const { test } = require('node:test');
const { App } = require('aws-cdk-lib');
const { Template } = require('aws-cdk-lib/assertions');
const { readAgentCoreProject, transformAgentCoreJson } = require('@aws/agentcore-cdk');
const { AgentCoreStack } = require('../dist/lib/cdk-stack');

for (const [enabled, backend] of [[false, 'dynamo'], [true, 'dynamo'], [true, 's3']]) {
  test(`five-player registry binding enabled=${enabled}, backend=${backend}`, () => {
    const project = readAgentCoreProject();
    const application = transformAgentCoreJson(project.agentCoreJson, {
      projectRoot: project.projectRoot,
      credentials: { account: '123456789012', region: 'us-east-1' },
      target: 'default',
    });
    const app = new App({
      context: { afcLearning: {
        enabled, backend, stackName: 'test-learning', teamId: 'our_team', bucket: 'our-bucket',
      } },
    });
    const stack = new AgentCoreStack(app, 'BindingTest', {
      env: { account: '123456789012', region: 'us-east-1' },
      application,
    });
    const resources = Object.values(Template.fromStack(stack).toJSON().Resources);
    const runtimes = resources.filter(r => r.Type === 'AWS::BedrockAgentCore::Runtime');
    assert.equal(runtimes.length, 5);
    for (const runtime of runtimes) {
      const vars = runtime.Properties.EnvironmentVariables;
      if (enabled && backend === 's3') {
        assert.equal(vars.AFC_TEAM_ID, 'our_team');
        assert.equal(vars.AFC_PROFILE_BUCKET, 'our-bucket');
        assert.equal(vars.AFC_OWNER_ACCOUNT, '123456789012');
        assert.equal(vars.AFC_PROFILE_TABLE, undefined);
      } else if (enabled) {
        assert.equal(vars.AFC_TEAM_ID, 'our_team');
        assert.deepEqual(vars.AFC_PROFILE_TABLE, { 'Fn::ImportValue': 'test-learning-RegistryTable' });
      } else {
        assert.equal(vars.AFC_TEAM_ID, undefined);
        assert.equal(vars.AFC_PROFILE_TABLE, undefined);
      }
    }
    const attached = resources.filter(r => r.Type === 'AWS::IAM::Role').filter(
      r => (r.Properties.ManagedPolicyArns || []).some(
        p => p['Fn::ImportValue'] === 'test-learning-PlayerPolicyArn',
      ),
    );
    assert.equal(attached.length, enabled && backend === 'dynamo' ? 5 : 0);
    if (backend === 's3') {
      const policies = resources.filter(r => r.Type === 'AWS::IAM::Policy');
      const pins = policies.flatMap(r => r.Properties.PolicyDocument.Statement).filter(
        s => s.Action === 's3:PutObject' || (Array.isArray(s.Action) && s.Action.includes('s3:PutObject')),
      );
      assert.equal(pins.length, 5);
      for (const policy of pins) {
        assert.equal(policy.Condition.StringEquals['s3:if-none-match'], '*');
        assert.match(JSON.stringify(policy.Resource), /registry\/matches\//);
      }
    }
  });
}
