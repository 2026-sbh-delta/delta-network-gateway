"""Ephemeral local Terraform container with current CLI role credentials; never prints their values."""
import json,os,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[2]
assert sys.argv[1] in {'init','validate','test','plan','apply','output','fmt','show'}
credentials=json.loads(subprocess.check_output(['aws','configure','export-credentials','--profile','delta-finals','--format','process']))
env={**os.environ,'AWS_ACCESS_KEY_ID':credentials['AccessKeyId'],'AWS_SECRET_ACCESS_KEY':credentials['SecretAccessKey'],
     'AWS_SESSION_TOKEN':credentials['SessionToken'],'AWS_REGION':'ap-northeast-2'}
args=['docker','run','--rm','-v',str(root)+':/work','-w','/work/terraform/onprem-orchestrator-aws']
for key in ('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_SESSION_TOKEN','AWS_REGION'):args+=['-e',key]
result=subprocess.run(args+['hashicorp/terraform:1.16.4']+sys.argv[1:],env=env)
raise SystemExit(result.returncode)
