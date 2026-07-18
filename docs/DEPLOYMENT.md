# Documentation Site Deployment Guide

This guide explains how to deploy the MkDocs documentation site using either **GitHub Pages** or **Vercel**. Both options are free; choose based on your repository visibility and team preference.

## Option A: GitHub Pages (Built-in)

### Requirements

- Repository must be **public**, OR
- GitHub account must be **Pro** ($4/month) for private repo Pages

### Setup

1. Go to repository **Settings → Pages**
2. Under **Build and deployment**, set **Source** to **GitHub Actions**
3. Push to `master` or `main` branch

The workflow at `.github/workflows/docs.yml` will automatically:
- Install MkDocs Material
- Build the site with `mkdocs build --strict`
- Deploy to `https://<owner>.github.io/<repo>/`

### Workflow behavior

- **Build job**: Always runs and uploads `site/` as an artifact (14-day retention)
- **Pages setup**: Uses `continue-on-error: true` so it does not fail when Pages is not enabled
- **Deploy job**: Only runs on `master`/`main` branch pushes, and only if Pages is configured

### URLs

- Production: `https://<owner>.github.io/<repo>/`
- PR preview: Not supported by GitHub Pages (use Vercel for PR previews)

---

## Option B: Vercel (Recommended for private repos)

### Requirements

- Free Vercel account
- GitHub integration

### Setup

1. Go to [vercel.com](https://vercel.com) and sign in with GitHub
2. Click **Add New → Project**
3. Import the `siq-research-engine` repository
4. Vercel will auto-detect `vercel.json` and configure:
   - **Build Command**: `pip install mkdocs==1.6.1 mkdocs-material==9.5.49 && mkdocs build --strict`
   - **Output Directory**: `site`
5. Click **Deploy**

### Features

- **Free** for private repositories
- Automatic HTTPS + global CDN
- **PR previews**: Every PR gets a preview URL
- Push to deploy on every branch
- Custom domain support

### URLs

- Production: `https://<repo>.vercel.app` (auto-assigned)
- Branch previews: `https://<repo>-<branch>.vercel.app`
- PR previews: `https://<repo>-git-<pr-branch>-<owner>.vercel.app`

---

## Comparison

| Feature | GitHub Pages | Vercel |
| --- | --- | --- |
| Cost (public repo) | Free | Free |
| Cost (private repo) | Requires Pro ($4/mo) | Free |
| HTTPS | Yes | Yes |
| CDN | Yes | Yes |
| PR previews | No | Yes |
| Branch previews | No | Yes |
| Custom domain | Yes | Yes |
| Build minutes | 2000/min/month (Actions) | 6000/min/month |
| Setup complexity | Low (just enable Pages) | Low (import + vercel.json) |
| Same as sunbo-blog | No | Yes |

## Recommendation

- **If repository is public**: Use GitHub Pages (simpler, no extra account)
- **If repository is private**: Use Vercel (free, with PR previews)
- **For team collaboration**: Vercel (PR previews are valuable for review)

## Local Development

```bash
# Install MkDocs
pip install mkdocs mkdocs-material

# Serve locally with hot reload
mkdocs serve

# Build static site
mkdocs build --strict

# Output is in site/ directory
```

## Configuration Files

- `mkdocs.yml` - Main MkDocs config (theme, nav, plugins)
- `vercel.json` - Vercel deployment config
- `.github/workflows/docs.yml` - GitHub Actions workflow
- `docs/site/stylesheets/extra.css` - Black/white palette overrides