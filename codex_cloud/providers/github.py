from urllib.parse import quote
import aiohttp
from ..exceptions import SchemaDriftError
from ..models import Branch

class GitHubBranchProvider:
    def __init__(self, token=None, *, session=None, api_url="https://api.github.com"):
        self.token, self._session, self.api_url = token, session, api_url.rstrip("/")
        self._owns_session = session is None
    async def close(self):
        if self._owns_session and self._session: await self._session.close()
    async def list_branches(self, environment):
        if not environment.repository_full_name or "/" not in environment.repository_full_name:
            raise SchemaDriftError("Environment does not identify a GitHub repository")
        owner, repo = environment.repository_full_name.split("/", 1)
        if self._session is None: self._session = aiohttp.ClientSession()
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token: headers["Authorization"] = f"Bearer {self.token}"
        url = f"{self.api_url}/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/branches?per_page=100"
        result = []
        while url:
            async with self._session.get(url, headers=headers, allow_redirects=False) as response:
                response.raise_for_status(); data = await response.json()
                result.extend(Branch(x["name"], (x.get("commit") or {}).get("sha"), x.get("protected")) for x in data)
                url = response.links.get("next", {}).get("url")
        return result
