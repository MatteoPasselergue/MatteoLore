# CREDIT
# This code is a modified version of this: https://github.com/jstrieb/github-stats


import asyncio
import os
from typing import Dict, List, Optional, Set, Tuple, Any, cast

import aiohttp
import requests


###############################################################################
# Main Classes
###############################################################################


class Queries(object):
    """
    Class with functions to query the GitHub GraphQL (v4) API and the REST (v3)
    API. Also includes functions to dynamically generate GraphQL queries.
    """

    def __init__(
            self,
            username: str,
            access_token: str,
            session: aiohttp.ClientSession,
            max_connections: int = 10,
    ):
        self.username = username
        self.access_token = access_token
        self.session = session
        self.semaphore = asyncio.Semaphore(max_connections)

    async def query(self, generated_query: str) -> Dict:
        """
        Make a request to the GraphQL API using the authentication token from
        the environment
        :param generated_query: string query to be sent to the API
        :return: decoded GraphQL JSON output
        """
        headers = {
            "Authorization": f"Bearer {self.access_token}",
        }
        try:
            async with self.semaphore:
                r_async = await self.session.post(
                    "https://api.github.com/graphql",
                    headers=headers,
                    json={"query": generated_query},
                )
            result = await r_async.json()
            if result is not None:
                return result
        except:
            print("aiohttp failed for GraphQL query")
            # Fall back on non-async requests
            async with self.semaphore:
                r_requests = requests.post(
                    "https://api.github.com/graphql",
                    headers=headers,
                    json={"query": generated_query},
                )
                result = r_requests.json()
                if result is not None:
                    return result
        return dict()

    async def query_rest(self, path: str, params: Optional[Dict] = None) -> Dict:
        """
        Make a request to the REST API
        :param path: API path to query
        :param params: Query parameters to be passed to the API
        :return: deserialized REST JSON output
        """

        for _ in range(60):
            headers = {
                "Authorization": f"token {self.access_token}",
            }
            if params is None:
                params = dict()
            if path.startswith("/"):
                path = path[1:]
            try:
                async with self.semaphore:
                    r_async = await self.session.get(
                        f"https://api.github.com/{path}",
                        headers=headers,
                        params=tuple(params.items()),
                    )
                if r_async.status == 202:
                    # print(f"{path} returned 202. Retrying...")
                    print(f"A path returned 202. Retrying...")
                    await asyncio.sleep(2)
                    continue

                result = await r_async.json()
                if result is not None:
                    return result
            except:
                print("aiohttp failed for rest query")
                # Fall back on non-async requests
                async with self.semaphore:
                    r_requests = requests.get(
                        f"https://api.github.com/{path}",
                        headers=headers,
                        params=tuple(params.items()),
                    )
                    if r_requests.status_code == 202:
                        print(f"A path returned 202. Retrying...")
                        await asyncio.sleep(2)
                        continue
                    elif r_requests.status_code == 200:
                        return r_requests.json()
        # print(f"There were too many 202s. Data for {path} will be incomplete.")
        print("There were too many 202s. Data for this repository will be incomplete.")
        return dict()

    @staticmethod
    def repos_overview(
            contrib_cursor: Optional[str] = None, owned_cursor: Optional[str] = None
    ) -> str:
        """
        :return: GraphQL query with overview of user repositories
        """
        return f"""{{
  viewer {{
    login,
    name,
    repositories(
        first: 100,
        orderBy: {{
            field: UPDATED_AT,
            direction: DESC
        }},
        isFork: false,
        after: {"null" if owned_cursor is None else '"' + owned_cursor + '"'}
    ) {{
      pageInfo {{
        hasNextPage
        endCursor
      }}
      nodes {{
        nameWithOwner
        stargazers {{
          totalCount
        }}
        forkCount
        languages(first: 10, orderBy: {{field: SIZE, direction: DESC}}) {{
          edges {{
            size
            node {{
              name
              color
            }}
          }}
        }}
      }}
    }}
    repositoriesContributedTo(
        first: 100,
        includeUserRepositories: false,
        orderBy: {{
            field: UPDATED_AT,
            direction: DESC
        }},
        contributionTypes: [
            COMMIT,
            PULL_REQUEST,
            REPOSITORY,
            PULL_REQUEST_REVIEW
        ]
        after: {"null" if contrib_cursor is None else '"' + contrib_cursor + '"'}
    ) {{
      pageInfo {{
        hasNextPage
        endCursor
      }}
      nodes {{
        nameWithOwner
        stargazers {{
          totalCount
        }}
        forkCount
        languages(first: 10, orderBy: {{field: SIZE, direction: DESC}}) {{
          edges {{
            size
            node {{
              name
              color
            }}
          }}
        }}
      }}
    }}
  }}
}}
"""

    @staticmethod
    def contrib_years() -> str:
        """
        :return: GraphQL query to get all years the user has been a contributor
        """
        return """
query {
  viewer {
    contributionsCollection {
      contributionYears
    }
  }
}
"""

    @staticmethod
    def contribs_by_year(year: str) -> str:
        """
        :param year: year to query for
        :return: portion of a GraphQL query with desired info for a given year
        """
        return f"""
    year{year}: contributionsCollection(
        from: "{year}-01-01T00:00:00Z",
        to: "{int(year) + 1}-01-01T00:00:00Z"
    ) {{
      contributionCalendar {{
        totalContributions
      }}
    }}
"""

    @classmethod
    def all_contribs(cls, years: List[str]) -> str:
        """
        :param years: list of years to get contributions for
        :return: query to retrieve contribution information for all user years
        """
        by_years = "\n".join(map(cls.contribs_by_year, years))
        return f"""
query {{
  viewer {{
    {by_years}
  }}
}}
"""


class Stats(object):

    def __init__(
            self,
            username: str,
            access_token: str,
            session: aiohttp.ClientSession,
    ):
        self.username = username
        self.queries = Queries(username, access_token, session)

        self._stargazers: Optional[int] = None
        self._forks: Optional[int] = None
        self._total_contributions: Optional[int] = None
        self._languages: Optional[Dict[str, Any]] = None
        self._repos: Optional[Set[str]] = None

    async def getData(self):
        contributions = await self.total_contributions
        forks = await self.forks
        repos = len(await self.repos)
        stars = await self.stargazers
        languages = await self.languages_proportional

        return [contributions, forks, repos, stars, languages]

    async def get_stats(self) -> None:
        self._stargazers = 0
        self._forks = 0
        self._languages = dict()
        self._repos = set()

        next_owned = None
        next_contrib = None
        while True:
            raw_results = await self.queries.query(
                Queries.repos_overview(
                    owned_cursor=next_owned, contrib_cursor=next_contrib
                )
            )
            raw_results = raw_results if raw_results is not None else {}

            contrib_repos = (
                raw_results.get("data", {})
                .get("viewer", {})
                .get("repositoriesContributedTo", {})
            )
            owned_repos = (
                raw_results.get("data", {}).get("viewer", {}).get("repositories", {})
            )

            repos = owned_repos.get("nodes", [])
            repos += contrib_repos.get("nodes", [])

            for repo in repos:
                if repo is None:
                    continue
                name = repo.get("nameWithOwner")
                if name in self._repos:
                    continue
                self._repos.add(name)
                self._stargazers += repo.get("stargazers").get("totalCount", 0)
                self._forks += repo.get("forkCount", 0)

                for lang in repo.get("languages", {}).get("edges", []):
                    name = lang.get("node", {}).get("name", "Other")
                    languages = await self.languages
                    if name in languages:
                        languages[name]["size"] += lang.get("size", 0)
                    else:
                        languages[name] = {
                            "size": lang.get("size", 0),
                        }

            if owned_repos.get("pageInfo", {}).get(
                    "hasNextPage", False
            ) or contrib_repos.get("pageInfo", {}).get("hasNextPage", False):
                next_owned = owned_repos.get("pageInfo", {}).get(
                    "endCursor", next_owned
                )
                next_contrib = contrib_repos.get("pageInfo", {}).get(
                    "endCursor", next_contrib
                )
            else:
                break
        langs_total = sum([v.get("size", 0) for v in self._languages.values()])
        for k, v in self._languages.items():
            v["prop"] = 100 * (v.get("size", 0) / langs_total)
        # dict(sorted(self._languages.items(), key=lambda item: item[1], reverse=True))

    @property
    async def stargazers(self) -> int:
        if self._stargazers is not None:
            return self._stargazers
        await self.get_stats()
        assert self._stargazers is not None
        return self._stargazers

    @property
    async def forks(self) -> int:
        if self._forks is not None:
            return self._forks
        await self.get_stats()
        assert self._forks is not None
        return self._forks

    @property
    async def languages(self) -> Dict:
        if self._languages is not None:
            return self._languages
        await self.get_stats()
        assert self._languages is not None
        return self._languages

    @property
    async def languages_proportional(self) -> Dict:
        if self._languages is None:
            await self.get_stats()
            assert self._languages is not None
        a = {k: v.get("prop", 0) for (k, v) in self._languages.items()}
        a = dict(sorted(a.items(), key=lambda item: item[1], reverse=True))
        return a

    @property
    async def repos(self) -> Set[str]:
        if self._repos is not None:
            return self._repos
        await self.get_stats()
        assert self._repos is not None
        return self._repos

    @property
    async def total_contributions(self) -> int:
        if self._total_contributions is not None:
            return self._total_contributions

        self._total_contributions = 0
        years = (
            (await self.queries.query(Queries.contrib_years()))
            .get("data", {})
            .get("viewer", {})
            .get("contributionsCollection", {})
            .get("contributionYears", [])
        )
        by_year = (
            (await self.queries.query(Queries.all_contribs(years)))
            .get("data", {})
            .get("viewer", {})
            .values()
        )
        for year in by_year:
            self._total_contributions += year.get("contributionCalendar", {}).get(
                "totalContributions", 0
            )
        return cast(int, self._total_contributions)

def language(data):
    language_data = {}
    i = 0
    for k, v in data.items():
        if i <= 2:
            language_data[k] = v
            i = i+1
        else:
            break

    d = {
        "PHP": "https://pngimg.com/uploads/php/php_PNG23.png",
        "Dart": "https://cdn.freebiesupply.com/logos/large/2x/dart-logo-png-transparent.png",
        "Kotlin": "https://cdn.freebiesupply.com/logos/large/2x/kotlin-1-logo-png-transparent.png",
        "Shell": "https://tse4.mm.bing.net/th?id=OIP.nO-KdkQLpAoBAh_m_7GY8QHaId&pid=Api",
        "Python": "https://tse2.mm.bing.net/th?id=OIP.fkvxbuKHOLhO4A_MqA9DVAHaHv&pid=Api",
        "C++": "https://tse2.mm.bing.net/th?id=OIP.H3I3buZeC8Bkez8ADSrqMwHaHa&pid=Api",
        "Css": "https://tse1.mm.bing.net/th?id=OIP.FCaF9-F7IFllP3x312SHEQHaHa&pid=Api",
        "CMake": "https://tse2.mm.bing.net/th?id=OIP.SaRbjGIkNNv3lGPMq5-mJwAAAA&pid=Api",
        "JavaScript": "https://tse4.mm.bing.net/th?id=OIP.PHBTJoshbg880IH9z_PB6QHaHa&pid=Api",
        "Java": "https://logodix.com/logo/283001.png",
        "HTML": "https://www.clipartkey.com/mpngs/m/210-2104705_html-logo-png-transparent-background.png",
        "Swift": "http://www.sic-sales.de/wp-content/uploads/2016/04/Swift_logo.svg.png",
        "Hack": "https://www.bonconseil.org/wp-content/uploads/2019/10/rond_gris.png",

    }
    formatted_languages = [f' <code><img height="15" src="{d[k]}"></code>  {k}: {v: 0.0f}%' for k, v in language_data.items()]

    return formatted_languages


async def main() -> None:
    access_token = os.getenv("ACCESS_TOKEN")
    user = os.getenv("GITHUB_ACTOR")
    if access_token is None or user is None:
        raise RuntimeError(
            "ACCESS_TOKEN and GITHUB_ACTOR environment variables cannot be None!"
        )
    async with aiohttp.ClientSession() as session:
        s = Stats(user, access_token, session)
        d = await s.getData()
        f = "README.md"

        lang = language(d[4])

        with open("model.md", "r") as fichier:
            contenu = fichier.read()
        new = contenu.replace("<commits>", str(d[0]))
        new = new.replace("<repo>", str(d[2]))
        new = new.replace("<stars>", str(d[3]))

        new = new.replace("<value1>", lang[0])
        new = new.replace("<value2>", lang[1])
        new = new.replace("<value3>", lang[2])

        with open(f, "w") as fichier:
            fichier.write(new)



if __name__ == "__main__":
    asyncio.run(main())
