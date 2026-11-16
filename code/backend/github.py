"""Bounded, public GitHub snapshots. Never fetch URLs found in a README."""
import base64
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

MAX_REPOS = 6
MAX_README = 3000
MAX_RESPONSE = 2_000_000
DEFAULT_TOKEN_FILE = Path(__file__).resolve().parents[2] / 'github.txt'


class GitHubError(Exception):
    pass


def read_token():
    token = os.getenv('GITHUB_TOKEN', '').strip()
    if not token:
        configured = os.getenv('GITHUB_TOKEN_FILE', '').strip()
        path = Path(configured) if configured else DEFAULT_TOKEN_FILE
        try:
            token = path.read_text(encoding='utf-8-sig').strip()
        except FileNotFoundError:
            if configured:
                raise GitHubError('The configured GitHub token file was not found.') from None
            return None
        except (OSError, UnicodeError):
            raise GitHubError('Could not read the server GitHub token file.') from None
        if not token:
            raise GitHubError('The server GitHub token file is empty.')
    if not re.fullmatch(r'[A-Za-z0-9_]+', token):
        raise GitHubError('The server GitHub token must contain only the token, without labels or quotes.')
    return token


def profile_links(text):
    """Find profile URLs only; paper repository owners are not candidate identities."""
    found = set()
    for match in re.finditer(r'(?<![\w@./-])(?:https?://)?(?:www\.)?github\.com/[^\s<>"\x27]+', text, re.I):
        value = match.group(0).rstrip('.,;:!?)]}')
        if not value.lower().startswith(('http://', 'https://')):
            value = 'https://' + value
        parts = urlsplit(value)
        if parts.hostname not in ('github.com', 'www.github.com') or parts.username or parts.password:
            continue
        # A profile's ?tab=repositories is fine; a repo path is not a profile.
        canonical = 'https://github.com' + parts.path
        try:
            user = username_from_url(canonical)
        except GitHubError:
            continue
        if user in {'about', 'login', 'signup', 'settings', 'features', 'topics', 'explore', 'orgs', 'marketplace', 'sponsors'}:
            continue
        found.add('https://github.com/' + user)
    return sorted(found)


def username_from_url(value):
    try:
        url = urlsplit(value.strip())
        username = url.path.strip('/')
        valid = (url.scheme == 'https' and url.netloc.lower() == 'github.com'
                 and not url.query and not url.fragment
                 and re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', username))
    except ValueError:
        valid = False
    if not valid:
        raise GitHubError('Enter a GitHub profile URL, for example https://github.com/username.')
    return username.lower()


async def api_get(client, path, *, optional=False):
    # path is built only from validated names; redirects and returned URLs are never followed.
    async with client.stream('GET', 'https://api.github.com' + path) as response:
        if optional and response.status_code == 404:
            return None
        if response.status_code in (403, 429):
            retry = 'Try later'
            if response.headers.get('x-ratelimit-remaining') == '0':
                try:
                    reset = datetime.fromtimestamp(int(response.headers['x-ratelimit-reset']), timezone.utc)
                    retry = f'Try after {reset:%H:%M UTC}'
                except (KeyError, ValueError, OverflowError, OSError):
                    pass
            raise GitHubError(f'GitHub access is limited. {retry} or configure a server GITHUB_TOKEN.')
        if response.status_code == 401:
            raise GitHubError('GitHub rejected the server token. Check GITHUB_TOKEN.')
        if response.status_code == 404:
            raise GitHubError('GitHub profile not found or not publicly accessible.')
        if response.status_code != 200:
            raise GitHubError(f'GitHub returned HTTP {response.status_code}. Try again later.')
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_RESPONSE:
                raise GitHubError('GitHub response is too large to import.')
            chunks.append(chunk)
        try:
            return json.loads(b''.join(chunks))
        except ValueError:
            raise GitHubError('GitHub returned an unreadable response.') from None


async def fetch_snapshot(url):
    username = username_from_url(url)
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'Candidate-Dossier',
               'X-GitHub-Api-Version': '2026-03-10'}
    if token := read_token():
        headers['Authorization'] = 'Bearer ' + token
    try:
        async with httpx.AsyncClient(headers=headers, timeout=15, follow_redirects=False) as client:
            user = await api_get(client, f'/users/{username}')
            if user.get('type') != 'User':
                raise GitHubError('Use a personal GitHub profile, not an organization or bot.')
            repos = await api_get(client, f'/users/{username}/repos?type=owner&sort=updated&per_page=100')
            if not isinstance(repos, list):
                raise GitHubError('GitHub returned an unreadable repository list.')
            eligible = [r for r in repos if not r.get('private') and not r.get('fork')
                        and str(r.get('owner', {}).get('login', '')).lower() == username]
            chosen = eligible[:MAX_REPOS]
            fetched_at = datetime.now(timezone.utc).isoformat()
            profile_url = f'https://github.com/{username}'
            coverage = f'{len(chosen)} original repositories from the first {len(repos)} public repositories sorted by update time.'
            pages = [{'number': 1, 'source_url': profile_url, 'filename': f'GitHub / {username}',
                      'text': f'GitHub profile: {profile_url}\nFetched at: {fetched_at}\n'
                              f'Coverage: {coverage}\nForks excluded. Private work and personal contributions are not verified.\n'
                              'Scope: repository metadata and README excerpts only; source code was not reviewed.'}]
            projects, warnings = [], []
            if len(eligible) > MAX_REPOS or len(repos) == 100:
                warnings.append('Only a sample of public repositories was imported.')
            for repo in chosen:
                name = repo.get('name', '')
                if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', name) or name in ('.', '..'):
                    continue
                repo_url = f'{profile_url}/{name}'
                project = {'name': name, 'url': repo_url, 'language': repo.get('language'),
                           'description': str(repo.get('description') or '')[:500],
                           'archived': bool(repo.get('archived')), 'fork': False,
                           'stars': repo.get('stargazers_count', 0), 'updated_at': repo.get('updated_at')}
                readme = await api_get(client, f'/repos/{username}/{name}/readme', optional=True)
                readme_text = ''
                if readme and readme.get('encoding') == 'base64':
                    try:
                        readme_text = base64.b64decode(readme.get('content', ''), validate=False).decode('utf-8', errors='replace')
                    except (ValueError, TypeError):
                        warnings.append(f'{name}: README could not be decoded.')
                truncated = len(readme_text) > MAX_README
                project['readme_truncated'] = truncated
                project['readme_available'] = bool(readme_text)
                if truncated:
                    warnings.append(f'{name}: README excerpt limited to {MAX_README:,} characters.')
                text = (f'GitHub repository metadata: {json.dumps(project, ensure_ascii=False)}\n'
                        f'Fetched at: {fetched_at}\nREADME excerpt:\n{readme_text[:MAX_README] or "README unavailable."}')
                pages.append({'number': len(pages) + 1, 'source_url': repo_url,
                              'filename': f'GitHub / {username}/{name}', 'text': text})
                projects.append(project)
            return {'username': username, 'url': profile_url, 'fetched_at': fetched_at,
                    'coverage': coverage, 'repositories': projects, 'warnings': warnings, 'pages': pages}
    except httpx.RequestError:
        raise GitHubError('Could not reach GitHub. The previous snapshot was kept; try again later.') from None
    except (KeyError, TypeError, AttributeError):
        raise GitHubError('GitHub returned an unexpected response. The previous snapshot was kept.') from None
