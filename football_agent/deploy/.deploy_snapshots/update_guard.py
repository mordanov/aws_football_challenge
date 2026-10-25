import re, os, sys
sys.path.insert(0, '/Users/aleksandr/Local/EPAM/aws_football_challenge/.venv/lib/python3.12/site-packages')

creds = {}
with open('/Users/aleksandr/Local/EPAM/aws_football_challenge/aws_creds.txt') as f:
    for line in f:
        m = re.match(r'^export\s+(\w+)=(.+)', line.strip())
        if m:
            creds[m.group(1)] = m.group(2).strip('"')

import boto3
boto3_session = boto3.Session(
    aws_access_key_id=creds.get('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=creds.get('AWS_SECRET_ACCESS_KEY'),
    aws_session_token=creds.get('AWS_SESSION_TOKEN'),
    region_name='us-east-1'
)
lam = boto3_session.client('lambda')

NEW_GUARD = '1792867671481'
functions = ['final-agent-s3-tactics-v3-learner', 'final-agent-s3-tactics-v3-collector']

for fn in functions:
    config = lam.get_function_configuration(FunctionName=fn)
    env_vars = config.get('Environment', {}).get('Variables', {}).copy()
    old_guard = env_vars.get('AFC_PROMOTION_NOT_BEFORE_MS', '?')
    env_vars['AFC_PROMOTION_NOT_BEFORE_MS'] = NEW_GUARD
    lam.update_function_configuration(FunctionName=fn, Environment={'Variables': env_vars})
    print(f"  {fn}: guard {old_guard} -> {NEW_GUARD}")

print("Done")
