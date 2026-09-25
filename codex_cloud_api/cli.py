"""Thin command-line adapter over the public API."""
import argparse, asyncio, json, sys
from pathlib import Path
from . import CodexCloudClient, CodexCloudError, login

def parser():
    p=argparse.ArgumentParser(prog="codex-cloud-api"); p.add_argument("method",nargs="?"); p.add_argument("path",nargs="?")
    p.add_argument("--login",action="store_true"); p.add_argument("--credential-file"); p.add_argument("--base-url",default="https://chatgpt.com/backend-api")
    p.add_argument("--account-id"); p.add_argument("--param",action="append",default=[]); p.add_argument("--header",action="append",default=[])
    p.add_argument("--json"); p.add_argument("--timeout",type=float,default=60); p.add_argument("--no-refresh",action="store_true"); p.add_argument("--raw",action="store_true"); return p
def _pairs(values, separator):
    result={}
    for value in values:
        if separator not in value: raise ValueError(f"invalid value {value!r}")
        key,val=value.split(separator,1); result[key.strip()]=val.strip()
    return result
def _code(info): print(f"Open {info.verification_url} and enter: {info.user_code}",file=sys.stderr)
async def run(a):
    if a.login: await login(a.credential_file,timeout=a.timeout,on_user_code=_code); return 0
    if not a.method or not a.path: raise ValueError("METHOD and PATH are required")
    payload=None
    if a.json is not None:
        text=sys.stdin.read() if a.json=="-" else Path(a.json[1:]).read_text() if a.json.startswith("@") else a.json; payload=json.loads(text)
    async with CodexCloudClient(credential_file=a.credential_file,base_url=a.base_url,account_id=a.account_id,timeout=a.timeout,no_refresh=a.no_refresh,on_user_code=_code) as client:
        response=await client.request(a.method,a.path,params=_pairs(a.param,"="),headers=_pairs(a.header,":"),json=payload)
    print(response.text() if a.raw else json.dumps(response.json(),indent=2,ensure_ascii=False)); return 0 if response.status < 400 else 1
def main():
    try: return asyncio.run(run(parser().parse_args()))
    except (CodexCloudError,ValueError,OSError,json.JSONDecodeError) as exc: print(f"error: {exc}",file=sys.stderr); return 2
