# ScoreBuzz deploy checklist

The site generator is built and verified (`~/workspace/scorebuzz/`).
Deploy is 4 moves, all on your side:

## 1. GitHub repo
- Create a repo (e.g. `scorebuzz-site`), upload the `scorebuzz` folder itself as-is
  (so the repo contains `scorebuzz/generate.py`, `scorebuzz/cricket.py`,
  `scorebuzz/.github/workflows/`, etc. — the workflow expects this layout).

## 2. GitHub secrets (repo Settings → Secrets → Actions)
- `CRICAPI_KEY` — your CricAPI key (the same one you saved with me; copy it from your cricapi.com dashboard)
- `CLOUDFLARE_API_TOKEN` — Cloudflare dashboard → My Profile → API Tokens → Create (Pages:Edit + Account:Read)
- `CLOUDFLARE_ACCOUNT_ID` — Cloudflare dashboard → Workers & Pages → Overview (right sidebar)

## 3. Cloudflare Pages project
- First deploy creates it automatically via the workflow (`--project-name=scorebuzz`).
- After the first successful run: Pages → scorebuzz → Custom domains → add `scorebuzz.net` and `www.scorebuzz.net`.

## 4. DNS (GoDaddy → Cloudflare)
- GoDaddy → scorebuzz.net → Nameservers → change to the two nameservers Cloudflare assigns
  (Cloudflare dashboard → Add site → scorebuzz.net shows them).
- DNS propagates in minutes to a few hours. The site is live at https://scorebuzz.net once it resolves.

## 5. Google Search Console (after DNS resolves)
- Add property scorebuzz.net, verify via DNS TXT record (Cloudflare DNS → Add record).
- Submit `https://scorebuzz.net/sitemap.xml` under Sitemaps.

## What runs automatically after that
- Every 15 min: rebuild (ESPN scores + CricAPI cricket within the 100/day budget),
  deploy to Cloudflare Pages. Zero servers, zero cost.

## Notes
- Cricket data starts flowing on the first Actions run (GitHub's network reaches
  the CricAPI; my server is blocked, which is why local runs show no matches).
- 3 leagues are absent because ESPN's free feed has no data for them: K League,
  Saudi Pro League, FIFA World Cup. They appear automatically if ESPN adds them.
