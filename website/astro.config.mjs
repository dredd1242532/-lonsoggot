import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import { siteConfig } from './site.config.mjs';

export default defineConfig({
  site: siteConfig.origin,
  base: siteConfig.base,
  trailingSlash: 'always',
  integrations: [starlight({
    title: 'Longshot',
    description: 'Longshot by team magicbean. Guides, project updates, and practical help for your PC adventure.',
    favicon: '/branding/longshot.png',
    customCss: ['./src/styles/forest.css', './src/styles/longshot.css'],
    components: {
      Header: './src/components/Header.astro',
      Hero: './src/components/LongshotHero.astro',
      Footer: './src/components/Footer.astro',
    },
    editLink: { baseUrl: siteConfig.editBase + 'website/' },
    tableOfContents: { minHeadingLevel: 2, maxHeadingLevel: 2 },
    sidebar: [
      { label: 'Overview', slug: '' },
      { label: 'Progress & updates', slug: 'progress' },
      { label: 'Download safety', slug: 'antivirus' },
      { label: 'Setup and settings', items: [
        { label: 'Check compatibility', slug: 'compatibility' },
        { label: 'Getting started', slug: 'guides/getting-started' },
        { label: 'Graphics & controls', slug: 'guides/graphics-and-controls' },
        { label: 'Updates & saves', slug: 'guides/updates-and-saves' },
        { label: 'Transfer a save', slug: 'guides/saves' },
      ] },
      { label: 'Help and support', items: [
        { label: 'Common issues', slug: 'guides/common-issues', badge: { text: 'Start here', variant: 'note' } },
        { label: 'Report & contribute', slug: 'guides/reporting-and-contributing' },
        { label: 'Longshot issue tracker ↗', link: siteConfig.issueUrl },
      ] },
      { label: 'Development', collapsed: true, items: [
        { label: 'Reference manual', slug: 'reference/manual' },
        { label: 'Development ↗', link: 'https://git.magicbean.dev/MagicBeanDev/Longshot/src/branch/main/docs/development.md' },
        { label: 'Architecture ↗', link: 'https://git.magicbean.dev/MagicBeanDev/Longshot/src/branch/main/docs/design.md' },
        { label: 'Contributing', slug: 'reference/contributing' },
        { label: 'Technical notes ↗', link: 'https://git.magicbean.dev/MagicBeanDev/Longshot/src/branch/main/docs' },
        { label: 'Versioning ↗', link: 'https://git.magicbean.dev/MagicBeanDev/Longshot/src/branch/main/docs/versioning.md' },
        { label: 'Workflow ↗', link: 'https://git.magicbean.dev/MagicBeanDev/Longshot/src/branch/main/docs/workflow.md' },
        { label: 'Pipeline lists ↗', link: 'https://git.magicbean.dev/MagicBeanDev/Longshot/src/branch/main/assets/pipeline_states/README.md' },
      ] },
      { label: 'About Longshot', slug: 'about' },
    ],
  })],
});
