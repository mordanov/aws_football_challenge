const assert = require('node:assert/strict');
const { test } = require('node:test');
const { App } = require('aws-cdk-lib');
const { Template } = require('aws-cdk-lib/assertions');
const { readAgentCoreProject, transformAgentCoreJson } = require('@aws/agentcore-cdk');
const { AgentCoreStack } = require('../dist/lib/cdk-stack');

for (const enabled of [false, true]) {
  test(`five-player registry binding enabled=${enabled}`, () => {
    const project = readAgentCoreProject();
    const application = transformAgentCoreJson(project.agentCoreJson, {
      projectRoot: project.projectRoot,
      credentials: { account: '123456789012', region: 'us-east-1' },
      target: 'default',
    });
    const app = new App({
      context: { afcLearning: { enabled, stackName: 'test-learning', teamId: 'our_team' } },
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
      if (enabled) {
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
    assert.equal(attached.length, enabled ? 5 : 0);
  });
}
