import {DEFAULT_BASE_URL, requestJson} from '../../extensions/http';

export type SkillsStatus = {websearch: {enabled: boolean; active: boolean; url: string}};

export function fetchSkills(baseUrl = DEFAULT_BASE_URL): Promise<SkillsStatus> {
  return requestJson<SkillsStatus>('/api/admin/skills', undefined, baseUrl);
}

export async function setWebsearch(enabled: boolean, baseUrl = DEFAULT_BASE_URL): Promise<SkillsStatus> {
  return requestJson<SkillsStatus>('/api/admin/skills', {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({websearch: enabled}),
  }, baseUrl);
}

