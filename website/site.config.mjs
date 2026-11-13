export function getSiteConfig(env = process.env) {
  const origin = new URL(env.DOCS_SITE_URL || 'https://magicbean.dev');
  if (origin.protocol !== 'https:' || origin.pathname !== '/' || origin.search || origin.hash) {
    throw new Error('DOCS_SITE_URL must be an HTTPS origin, without a path, query, or fragment.');
  }
  const repository = env.DOCS_SOURCE_REPOSITORY || 'bibiburl/Longshot';
  if (!/^[\w.-]+\/[\w.-]+$/.test(repository)) throw new Error('Invalid DOCS_SOURCE_REPOSITORY.');
  const branch = env.DOCS_SOURCE_BRANCH || 'docs/longshot-website';
  const ref = branch.split('/').map(encodeURIComponent).join('/');
  const projectRepository = 'https://git.magicbean.dev/MagicBeanDev/Longshot';
  return { origin: origin.origin, base: '/Longshot/', projectRepository,
    sourceBase: `${projectRepository}/src/branch/main/`,
    issueUrl: `${projectRepository}/issues/new`,
    editBase: `https://git.magicbean.dev/${repository}/_edit/${ref}/` };
}

export const siteConfig = getSiteConfig();
