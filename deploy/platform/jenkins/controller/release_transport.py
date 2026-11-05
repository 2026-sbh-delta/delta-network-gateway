"""서버 인벤토리의 EIC/IAP를 통해 신뢰된 호스트 도구를 전달·분리 실행·조회한다."""
import base64
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import tempfile
import time

from delivery_contract import canonical_hash


def validate_target(target):
    if target.get("environment") != "production" or target.get("cloud") not in ("aws", "gcp"):
        raise ValueError("AWS 또는 GCP production 대상 하나가 필요하다")
    for name in ("target_id", "os_user", "compose_project"):
        if not isinstance(target.get(name), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", target[name]):
            raise ValueError("대상 식별자가 올바르지 않다")
    root = target.get("install_dir", "/opt/delta-compose")
    if not isinstance(root, str) or not re.fullmatch(r"/[A-Za-z0-9_./-]+", root) or ".." in PurePosixPath(root).parts:
        raise ValueError("설치 경로가 올바르지 않다")
    if target.get("db_major") != 18:
        raise ValueError("대상 DB 메이저18이 필요하다")
    for port in ("frontend_port", "backend_port"):
        if type(target.get(port)) is not int or not 1 <= target[port] <= 65535:
            raise ValueError("대상 포트가 올바르지 않다")
    if not target.get("db_secret_ref") or not target.get("db_name"):
        raise ValueError("서버 소유 DB/비밀 참조가 필요하다")
    expected = "gcplogs" if target["cloud"] == "gcp" else "awslogs"
    if target.get("log_driver") != expected:
        raise ValueError("클라우드 로그 드라이버가 올바르지 않다")
    names = ("project", "zone", "vm_name", "log_project") if target["cloud"] == "gcp" else ("region", "instance_id", "endpoint_id", "availability_zone")
    for name in names:
        if not isinstance(target.get(name), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", target[name]):
            raise ValueError("대상 클라우드 인벤토리가 불완전하다")
    if target.get("connection") != ("iap" if target["cloud"] == "gcp" else "eic"):
        raise ValueError("등록된 EIC 또는 IAP 접속이 필요하다")
    try:
        if not ipaddress.ip_address(target["private_ip"]).is_private:
            raise ValueError("사설 운영 VM 주소가 필요하다")
    except (KeyError, TypeError) as exc:
        raise ValueError("사설 운영 VM 주소가 필요하다") from exc
    if target["cloud"] == "aws" and not re.fullmatch(r"[0-9]{12}", target.get("account_id", "")):
        raise ValueError("AWS 대상 계정 ID가 필요하다")
    if target["cloud"] == "aws" and not str(target.get("assume_role_arn", "")).startswith("arn:aws:iam::" + target["account_id"] + ":role/"):
        raise ValueError("AWS 위임 역할과 대상 계정이 다르다")


# stdin JSON으로만 요청·도구를 전달한다. 앱 소스·컨테이너 이미지를 운반하지 않는다.
BOOTSTRAP = r'''
import base64,json,os,pathlib,re,subprocess,sys
os.umask(0o077)
data=json.load(sys.stdin); request=data['request']; target=request['target']; dep=request['deployment_id']
root=pathlib.Path(target.get('install_dir','/opt/delta-compose'))/'delivery'
root.mkdir(parents=True,exist_ok=True,mode=0o700)
tools=root/'tools'/data['unit']; tools.mkdir(parents=True,exist_ok=True,mode=0o700)
for name,content in data['tools'].items():
 if name not in ('compose_host.py','delivery_contract.py'): raise ValueError('invalid tool')
 path=tools/name; temporary=path.with_suffix('.new'); temporary.write_bytes(base64.b64decode(content)); temporary.chmod(0o600); temporary.replace(path)
request_path=root/(dep+'.request.json')
payload=json.dumps(request,sort_keys=True,separators=(',',':'))
if request_path.exists() and request_path.read_text()!=payload: raise ValueError('deployment conflict')
if not request_path.exists():
 with request_path.open('x') as output: output.write(payload)
# 이 bootstrap이 재호출돼도 먼저 query한다. running/unknown은 새 배포로 재시작하지 않는다.
query=subprocess.run(['python3',str(tools/'compose_host.py'),'query','--deployment-id',dep,'--state-dir',str(root/'state')],capture_output=True,text=True)
if query.returncode==0:
 existing=json.loads(query.stdout)
 if existing.get('state') not in (None,'not_found','absent'):
  print(query.stdout); sys.exit(0)
else:
 raise ValueError('existing deployment state cannot be determined')
intent=root/(dep+'.launch-intent')
if intent.exists():
 print(json.dumps({'deployment_id':dep,'state':'unknown'})); sys.exit(0)
with intent.open('x') as output: output.write(data['unit'])
command=['python3',str(tools/'compose_host.py'),'deploy','--request',str(request_path),'--state-dir',str(root/'state')]
unit='delta-delivery-'+data['unit']
# PID1이 소유하는 별도 프로세스가 flock/상태/복귀를 끝까지 소유한다.
subprocess.run(['systemd-run','--quiet','--collect','--unit',unit,'--property=Type=exec',
 '--property=StandardOutput=null','--property=StandardError=null','--',*command],check=True,capture_output=True)
print(json.dumps({'deployment_id':dep,'state':'running'}))
'''


def execute(request, *, runner=subprocess.run, sleep=time.sleep):
    target = request["target"]
    validate_target(target)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AWS_", "CLOUDSDK_", "GOOGLE_"))
           and k not in ("APPROVAL", "IMAGES_JSON", "BUNDLE_JSON")}
    env.update(AWS_SHARED_CREDENTIALS_FILE="/dev/null", AWS_CONFIG_FILE="/dev/null", AWS_PAGER="")

    def run(argv, **kwargs):
        result = runner(argv, capture_output=True, text=True, env=env, timeout=120, **kwargs)
        if result.returncode:
            raise RuntimeError("원격 연결 또는 조회 실패")
        return result

    with tempfile.TemporaryDirectory(prefix="delta-release-") as directory:
        key = str(Path(directory) / "ssh")
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key])
        known_hosts = os.environ.get("DELTA_RELEASE_KNOWN_HOSTS", "/run/delta/known_hosts")
        ssh_options = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + known_hosts,
                       "-o", "ConnectTimeout=20", "-o", "IdentitiesOnly=yes", "-i", key]
        if target["cloud"] == "aws":
            ssh_options += ["-o", "HostKeyAlias=" + target.get("private_ip", target["instance_id"])]
            role = target.get("assume_role_arn")
            if not isinstance(role, str) or not re.fullmatch(r"arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]+", role):
                raise ValueError("AWS 위임 역할이 필요하다")
            creds = json.loads(run(["aws", "sts", "assume-role", "--role-arn", role, "--role-session-name", "delta-release",
                                    "--duration-seconds", "3600", "--region", target["region"]]).stdout)["Credentials"]
            env.update(AWS_ACCESS_KEY_ID=creds["AccessKeyId"], AWS_SECRET_ACCESS_KEY=creds["SecretAccessKey"], AWS_SESSION_TOKEN=creds["SessionToken"])
            instances = json.loads(run(["aws", "ec2", "describe-instances", "--region", target["region"],
                                        "--instance-ids", target["instance_id"]]).stdout)
            instance = instances["Reservations"][0]["Instances"][0]
            endpoints = json.loads(run(["aws", "ec2", "describe-instance-connect-endpoints", "--region", target["region"],
                                        "--instance-connect-endpoint-ids", target["endpoint_id"]]).stdout)["InstanceConnectEndpoints"]
            if (instance["PrivateIpAddress"] != target["private_ip"] or instance["State"]["Name"] != "running"
                    or instance["Placement"]["AvailabilityZone"] != target["availability_zone"]
                    or len(endpoints) != 1 or endpoints[0]["State"] != "create-complete"
                    or endpoints[0]["VpcId"] != instance["VpcId"]):
                raise ValueError("등록한 VM/EIC endpoint 실제 인벤토리가 다르다")

            def connect(command, payload=None):
                # EIC 공개 키 유효 시간이 짧으므로 매 접속 직전에 다시 전송한다.
                run(["aws", "ec2-instance-connect", "send-ssh-public-key", "--region", target["region"],
                     "--instance-id", target["instance_id"], "--availability-zone", target["availability_zone"],
                     "--instance-os-user", target["os_user"], "--ssh-public-key", "file://" + key + ".pub"])
                proxy = shlex.join(["aws", "ec2-instance-connect", "open-tunnel", "--region", target["region"],
                                    "--instance-id", target["instance_id"], "--instance-connect-endpoint-id", target["endpoint_id"],
                                    "--remote-port", "22", "--max-tunnel-duration", "900"])
                return run(["ssh", *ssh_options, "-o", "ProxyCommand=" + proxy,
                            target["os_user"] + "@" + target["instance_id"], shlex.join(command)], input=payload)
        else:
            env.update(CLOUDSDK_CONFIG=directory + "/gcloud", CLOUDSDK_CORE_DISABLE_PROMPTS="1",
                       CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE=os.environ.get("DELTA_RELEASE_WIF_CONFIG", "/run/delta/gcp-release.json"))
            instance = json.loads(run(["gcloud", "compute", "instances", "describe", target["vm_name"], "--project", target["project"],
                                       "--zone", target["zone"], "--format=json"]).stdout)
            metadata = {item["key"]: item["value"] for item in instance.get("metadata", {}).get("items", [])}
            if "enable-oslogin" not in metadata:
                project = json.loads(run(["gcloud", "compute", "project-info", "describe", "--project", target["project"], "--format=json"]).stdout)
                metadata = {item["key"]: item["value"] for item in project.get("commonInstanceMetadata", {}).get("items", [])}
            if (metadata.get("enable-oslogin", "").lower() != "true" or instance.get("status") != "RUNNING"
                    or target["private_ip"] not in [item.get("networkIP") for item in instance.get("networkInterfaces", [])]):
                raise ValueError("등록한 GCP VM/OS Login 실제 인벤토리가 다르다")

            def connect(command, payload=None):
                flags = ["--ssh-flag=" + item for item in ssh_options]
                return run(["gcloud", "compute", "ssh", target["os_user"] + "@" + target["vm_name"],
                            "--project", target["project"], "--zone", target["zone"], "--tunnel-through-iap", "--quiet",
                            "--ssh-key-file", key, "--ssh-key-expire-after=15m", *flags, "--command", shlex.join(command)], input=payload)

        root = str(PurePosixPath(target.get("install_dir", "/opt/delta-compose")) / "delivery")
        unit = canonical_hash({"target": target["target_id"], "id": request["deployment_id"]})[:24]
        query = ["sudo", "python3", root + "/tools/" + unit + "/compose_host.py", "query", "--deployment-id", request["deployment_id"], "--state-dir", root + "/state"]
        # 조회가 실패하면 없다고 가정하지 않는다. 부트스트랩도 기존 상태를 확인한다.
        folder = Path(__file__).parent
        payload = {"request": request, "unit": unit,
                   "tools": {name: base64.b64encode((folder / name).read_bytes()).decode()
                             for name in ("compose_host.py", "delivery_contract.py")}}
        result = json.loads(connect(["sudo", "python3", "-c", BOOTSTRAP], json.dumps(payload)).stdout)
        deadline = time.monotonic() + 900
        while result.get("state") in ("running", "reserved"):
            if time.monotonic() >= deadline:
                return {"deployment_id": request["deployment_id"], "state": "unknown", "target_id": target["target_id"]}
            sleep(2)
            result = json.loads(connect(query).stdout)
        if result.get("state") == "ready":
            result = {**result, "state": "verified"}
            if "commit" not in result and "commit_sha" in result:
                result["commit"] = result["commit_sha"]
        return result
