const ownerOnlyRoutes = ['/settings', '/privacy', '/streams'];

export function isOwnerOnlyPath(currentPath: string): boolean {
  return ownerOnlyRoutes.some(
    (path) => currentPath === path || currentPath.startsWith(`${path}/`),
  );
}
