#!/bin/bash
# One-shot setup: install gh CLI (if missing), auth, init repo, commit, push to GitHub.
# Run with:  bash "/Users/leo/Documents/Claude/Projects/Unicorn Monitoring Console/deploy/setup.sh"
set -e

DEPLOY_DIR="/Users/leo/Documents/Claude/Projects/Unicorn Monitoring Console/deploy"
REPO_NAME="${REPO_NAME:-unicorn-dashboard}"
VISIBILITY="${VISIBILITY:---public}"

cd "$DEPLOY_DIR"
echo "==> Working directory: $DEPLOY_DIR"
echo "==> Repo name: $REPO_NAME ($VISIBILITY)"
echo ""

# 1) Ensure gh CLI is installed
if ! command -v gh >/dev/null 2>&1; then
  echo "==> gh CLI not found. Installing via Homebrew..."
  if ! command -v brew >/dev/null 2>&1; then
    echo "❌ Homebrew not installed. Install it first from https://brew.sh, then re-run this script."
    exit 1
  fi
  brew install gh
fi
echo "==> gh CLI: $(gh --version | head -n1)"

# 2) Auth (interactive: opens browser; you click 'Authorize')
if ! gh auth status >/dev/null 2>&1; then
  echo ""
  echo "==> Not authenticated yet. Launching 'gh auth login'..."
  echo "    Pick: GitHub.com → HTTPS → Login with a web browser"
  echo ""
  gh auth login
fi
echo "==> Logged in as: $(gh api user --jq .login)"

# 3) Init local git repo (idempotent)
if [ ! -d .git ]; then
  echo "==> git init"
  git init
  git branch -M main
else
  echo "==> .git already exists; skipping init"
fi

# 4) Stage + commit (skip if nothing changed)
git add .
if git diff --cached --quiet; then
  echo "==> Nothing new to commit"
else
  git commit -m "init unicorn dashboard"
fi

# 5) Create GitHub repo (if not exists) and push
USERNAME=$(gh api user --jq .login)
if gh repo view "$USERNAME/$REPO_NAME" >/dev/null 2>&1; then
  echo "==> Repo $USERNAME/$REPO_NAME already exists; just pushing"
  if ! git remote get-url origin >/dev/null 2>&1; then
    git remote add origin "https://github.com/$USERNAME/$REPO_NAME.git"
  fi
  git push -u origin main
else
  echo "==> Creating $USERNAME/$REPO_NAME and pushing"
  gh repo create "$REPO_NAME" $VISIBILITY --source=. --push --description "Live unicorn startup dashboard, auto-updated weekly from Wikipedia"
fi

# 6) Print next steps
echo ""
echo "✅ Done!"
echo ""
echo "🔗 Repo: https://github.com/$USERNAME/$REPO_NAME"
echo ""
echo "Next steps (one-time, in browser):"
echo "  1. Open https://github.com/$USERNAME/$REPO_NAME/settings/pages"
echo "  2. Source: Deploy from a branch · Branch: main · Folder: / (root)"
echo "  3. Wait ~1 minute, then visit https://$USERNAME.github.io/$REPO_NAME/"
echo ""
echo "To trigger a data refresh manually:"
echo "  https://github.com/$USERNAME/$REPO_NAME/actions/workflows/update-data.yml → Run workflow"
echo ""
