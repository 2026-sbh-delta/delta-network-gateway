#!/usr/bin/env python3
"""Fetch only the application credential, then replace this process with Node."""
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    config = json.loads(Path('/etc/delta-sample/runtime.json').read_text())
    release = json.loads(Path('/opt/delta-sample/current/release.json').read_text())
    env = os.environ.copy()
    for key in ['HOST', 'PORT', 'APP_ENV', 'API_ENDPOINT', 'STORAGE_BACKEND', 'PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGSSLMODE', 'PGSSLROOTCERT']:
        if key in config:
            env[key] = config[key]
    env['APP_VERSION'] = release['commit']
    if config['ROLE'] == 'frontend':
        env.update(API_HOST=release['api_host'], API_PORT='8080', API_PROTOCOL='http')
    else:
        result = subprocess.run(['aws', 'secretsmanager', 'get-secret-value', '--region', config['REGION'],
                                 '--secret-id', config['APP_SECRET_ARN'], '--query', 'SecretString', '--output', 'text'],
                                check=True, capture_output=True, text=True, timeout=30)
        secret = json.loads(result.stdout)
        if secret['username'] != config['PGUSER'] or not secret['password']:
            raise ValueError('Invalid application credential')
        env['PGPASSWORD'] = secret['password']
    os.execve('/usr/local/bin/node', ['node', '/opt/delta-sample/current/server.mjs'], env)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Runtime configuration or credential retrieval failed.', file=sys.stderr)
        sys.exit(1)
