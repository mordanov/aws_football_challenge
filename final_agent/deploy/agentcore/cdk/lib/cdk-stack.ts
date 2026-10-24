import { AgentCoreApplication, type AgentCoreApplicationProps } from '@aws/agentcore-cdk';
import { Fn, Stack, type StackProps } from 'aws-cdk-lib';
import { ManagedPolicy, PolicyStatement } from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export interface AgentCoreStackProps extends StackProps {
  /** Props for the AgentCore application, produced by transformAgentCoreJson from agentcore/agentcore.json. */
  application: AgentCoreApplicationProps;
}

/**
 * The stack the AgentCore CLI deploys. Everything declared in agentcore/agentcore.json is
 * created by the AgentCoreApplication construct. Add your own resources below it and wire
 * them to your runtimes and harnesses through the application's accessors.
 */
export class AgentCoreStack extends Stack {
  public readonly application: AgentCoreApplication;

  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);
    this.application = new AgentCoreApplication(this, 'Application', props.application);

    const learning: unknown = this.node.tryGetContext('afcLearning');
    if (learning !== undefined) {
      if (
        typeof learning !== 'object' || learning === null ||
        !('enabled' in learning) || typeof learning.enabled !== 'boolean' ||
        !('stackName' in learning) || typeof learning.stackName !== 'string' ||
        !/^[A-Za-z][A-Za-z0-9-]{1,44}$/.test(learning.stackName) ||
        !('teamId' in learning) || typeof learning.teamId !== 'string' ||
        !/^[A-Za-z0-9_-]{1,64}$/.test(learning.teamId)
      ) {
        throw new Error('afcLearning requires enabled, valid stackName and teamId');
      }
      if (!learning.enabled) return;
      if ('backend' in learning && learning.backend === 's3') {
        if (
          !('bucket' in learning) || typeof learning.bucket !== 'string' ||
          !/^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/.test(learning.bucket)
        ) throw new Error('S3 learning requires a valid owned bucket');
        const objects = `arn:${this.partition}:s3:::${learning.bucket}/final-agent-learning/${learning.teamId}/registry`;
        for (const name of ['r4_gk', 'r4_def', 'r4_mid', 'r4_fwd1', 'r4_fwd2']) {
          const runtime = this.application.runtime(name);
          runtime.addToRolePolicy(new PolicyStatement({
            actions: ['s3:GetObject'],
            resources: [`${objects}/*`],
            conditions: { StringEquals: { 's3:ResourceAccount': this.account } },
          }));
          runtime.addToRolePolicy(new PolicyStatement({
            actions: ['s3:ListBucket'],
            resources: [`arn:${this.partition}:s3:::${learning.bucket}`],
            conditions: { StringEquals: { 's3:ResourceAccount': this.account } },
          }));
          runtime.addToRolePolicy(new PolicyStatement({
            actions: ['s3:PutObject'],
            resources: [`${objects}/matches/*`],
            conditions: {
              StringEquals: { 's3:ResourceAccount': this.account, 's3:if-none-match': '*' },
            },
          }));
          runtime.addEnvironmentVariable('AFC_PROFILE_BUCKET', learning.bucket);
          runtime.addEnvironmentVariable('AFC_TEAM_ID', learning.teamId);
          runtime.addEnvironmentVariable('AFC_OWNER_ACCOUNT', this.account);
        }
        return;
      }
      const table = Fn.importValue(`${learning.stackName}-RegistryTable`);
      const policy = ManagedPolicy.fromManagedPolicyArn(
        this, 'MatchProfilePolicy', Fn.importValue(`${learning.stackName}-PlayerPolicyArn`),
      );
      for (const name of ['r4_gk', 'r4_def', 'r4_mid', 'r4_fwd1', 'r4_fwd2']) {
        const runtime = this.application.runtime(name);
        runtime.role.addManagedPolicy(policy);
        runtime.addEnvironmentVariable('AFC_PROFILE_TABLE', table);
        runtime.addEnvironmentVariable('AFC_TEAM_ID', learning.teamId);
      }
    }
  }
}
