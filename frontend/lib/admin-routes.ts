const ownerOnlyRoutes = ['/admin/settings', '/privacy', '/streams'];

export function isOwnerOnlyPath(currentPath: string): boolean {
  return ownerOnlyRoutes.some(
    (path) => currentPath === path || currentPath.startsWith(`${path}/`),
  );
}
