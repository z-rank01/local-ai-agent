import {requestJson} from './http';

export type PackageRevision = {revision: string; source: string; manifest: {
  title: string; version: string; description: string; permissions: string[]; required: string[];
}};
export type InstalledPackage = {id: string; active: PackageRevision | null; pending?: PackageRevision | null;
  history: PackageRevision[]; deployment?: string; error?: string; uninstalled?: boolean};
export const fetchPackages = () => requestJson<{packages: InstalledPackage[]}>('/api/plugin-packages');
export type PackageDiff = {files: {path: string; change: string; diff?: string; truncated?: boolean; binary?: boolean}[]};
export const fetchPackageDiff = (id: string, after: string) => requestJson<PackageDiff>(
  `/api/plugin-packages/${encodeURIComponent(id)}/diff?after=${encodeURIComponent(after)}`);
export const importPackage = (path: string) => requestJson('/api/plugin-packages/import', {
  method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({path}),
});
export const changePackage = (id: string, action: string, revision?: string, trusted = false) =>
  requestJson(`/api/plugin-packages/${encodeURIComponent(id)}`, {method:'POST',
    headers:{'content-type':'application/json'}, body:JSON.stringify({action, revision, trusted})});
