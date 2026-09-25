import base64, json, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from codex_cloud_api import *
from codex_cloud_api.auth import REFRESH_TOKEN_URL

def jwt(exp):
    payload=base64.urlsafe_b64encode(json.dumps({"exp":exp}).encode()).decode().rstrip("=")
    return f"x.{payload}.x"
def document(access="access",refresh="refresh",account="acct",last=None):
    return {"auth_mode":"chatgpt","tokens":{"access_token":access,"refresh_token":refresh,"account_id":account},"last_refresh":last or datetime.now(timezone.utc).isoformat()}

class Context:
    def __init__(self,value): self.value=value
    async def __aenter__(self): return self.value
    async def __aexit__(self,*args): pass
class Raw:
    def __init__(self,status=200,body=b'{"ok":true}',headers=None): self.status=status; self._body=body; self.headers=headers or {"X":"y"}
    async def read(self): return self._body
    async def text(self): return self._body.decode()
class Session:
    def __init__(self,responses): self.responses=iter(responses); self.calls=[]; self.closed=False
    def request(self,*args,**kwargs): self.calls.append(("request",args,kwargs)); return Context(next(self.responses))
    def post(self,*args,**kwargs): self.calls.append(("post",args,kwargs)); return Context(next(self.responses))
    async def close(self): self.closed=True

class ApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/"credentials.json"
    def tearDown(self): self.tmp.cleanup()
    def save(self,doc=None): JsonCredentialStore(self.path).save(doc or document())
    def client(self,responses,**kw): return CodexCloudClient(credential_file=self.path,session=Session(responses),**kw)

    async def test_load_and_normal_request_query_json_headers_account_and_redirects(self):
        self.save(); client=self.client([Raw()]); transport=client._session
        async with client:
            response=await client.post("/wham/tasks",params={"limit":20},json={"x":1},headers={"X-App":"yes"})
        args=transport.calls[0]
        self.assertIn("limit=20",args[1][1]); self.assertEqual(args[2]["json"],{"x":1})
        self.assertEqual(args[2]["headers"]["ChatGPT-Account-Id"],"acct")
        self.assertEqual(args[2]["headers"]["X-App"],"yes"); self.assertFalse(args[2]["allow_redirects"])
        self.assertEqual(response.json(),{"ok":True}); self.assertIn("ok",response.text())

    async def test_missing_credentials_uses_device_login(self):
        expected=Credentials("a","r",None,document(),JsonCredentialStore(self.path))
        with patch("codex_cloud_api.client.acquire_credentials",AsyncMock(return_value=expected)) as acquire:
            client=self.client([]); await client.__aenter__(); await client.close()
        acquire.assert_awaited_once()

    async def test_proactive_refresh_and_rotation(self):
        self.save(document(jwt((datetime.now(timezone.utc)-timedelta(seconds=1)).timestamp())))
        session=Session([Raw(body=b'{"access_token":"new","refresh_token":"rotated"}'),Raw()])
        client=CodexCloudClient(credential_file=self.path,session=session)
        async with client: await client.get("/x")
        saved=json.loads(self.path.read_text()); self.assertEqual(saved["tokens"]["refresh_token"],"rotated")
        self.assertEqual(session.calls[0][1][0],REFRESH_TOKEN_URL); self.assertFalse(session.calls[0][2]["allow_redirects"])

    async def test_401_reload_then_retry(self):
        self.save(); session=Session([Raw(401),Raw()]); client=CodexCloudClient(credential_file=self.path,session=session)
        async with client:
            self.save(document("new")); response=await client.get("/x")
        self.assertEqual(response.status,200); self.assertEqual(len(session.calls),2)
        self.assertEqual(session.calls[1][2]["headers"]["Authorization"],"Bearer new")

    async def test_401_refresh_then_retry(self):
        self.save(); session=Session([Raw(401),Raw(body=b'{"access_token":"new"}'),Raw()]); client=CodexCloudClient(credential_file=self.path,session=session)
        async with client: response=await client.get("/x")
        self.assertEqual(response.status,200); self.assertEqual(session.calls[1][1][0],REFRESH_TOKEN_URL)

    async def test_no_refresh_leaves_401(self):
        self.save(document(jwt(0))); session=Session([Raw(401)]); client=CodexCloudClient(credential_file=self.path,session=session,no_refresh=True)
        async with client: response=await client.get("/x")
        self.assertEqual(response.status,401); self.assertEqual(len(session.calls),1)

    def test_configuration_safety_and_timeout(self):
        with self.assertRaises(UnsafeDestinationError): CodexCloudClient(base_url="https://evil.example")
        with self.assertRaises(ConfigurationError): CodexCloudClient(timeout=0)

    async def test_reserved_header_rejected(self):
        self.save(); client=self.client([])
        async with client:
            with self.assertRaises(ConfigurationError): await client.get("/x",headers={"Authorization":"bad"})

    def test_response_error(self):
        response=Response(500,{},b"failure","GET","https://example")
        with self.assertRaises(HTTPResponseError): response.raise_for_status()

if __name__ == "__main__": unittest.main()
