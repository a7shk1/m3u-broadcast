name: Update premierleague.m3u (TNT/Sky only)

on:
  workflow_dispatch:
  schedule:
    - cron: "*/30 * * * *"  # كل 30 دقيقة (عدّل إذا تريد)

env:
  # نفس المعلمات القديمة تمامًا (تقدر تغيّرها من هنا أو كـ Secrets/Vars)
  SOURCE_URL: "https://raw.githubusercontent.com/DisabledAbel/daddylivehd-m3u/f582ae100c91adf8c8db905a8f97beb42f369a0b/daddylive-events.m3u8"
  DEST_RAW_URL: "https://raw.githubusercontent.com/a7shk1/m3u-broadcast/refs/heads/main/premierleague.m3u"
  GITHUB_REPO: "a7shk1/m3u-broadcast"
  GITHUB_BRANCH: "main"
  DEST_REPO_PATH: "premierleague.m3u"
  COMMIT_MESSAGE: "chore: update premierleague URLs (Match!/TNT/Sky)"
  OUTPUT_LOCAL_PATH: "./out/premierleague.m3u"
  TIMEOUT: "25"
  VERIFY_SSL: "true"

jobs:
  run:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          ref: ${{ env.GITHUB_BRANCH }}

      - name: Update M3U (TNT 1/2 + Sky Main Event/PL فقط)
        shell: bash
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          set -euo pipefail

          curl_opts=("--max-time" "${TIMEOUT}" "--fail" "--show-error" "--location")
          if [ "${VERIFY_SSL}" != "true" ]; then curl_opts+=("-k"); fi

          # حمّل المصدر والوجهة
          curl "${curl_opts[@]}" "$SOURCE_URL" -o /tmp/src.m3u
          curl "${curl_opts[@]}" "$DEST_RAW_URL" -o /tmp/dest.m3u || true

          # إذا الوجهة فارغة أنشئ هيدر
          if [ ! -s /tmp/dest.m3u ]; then echo "#EXTM3U" > /tmp/dest.m3u; fi

          # بايثون مصغّر داخل الوركفلو (بدون سكربت خارجي)
          python - <<'PY'
import os, re, sys, base64, pathlib, requests

SOURCE_URL    = os.environ["SOURCE_URL"]
DEST_PATH     = os.environ.get("DEST_REPO_PATH","premierleague.m3u")
VERIFY_SSL    = os.environ.get("VERIFY_SSL","true").lower() == "true"
TIMEOUT       = int(os.environ.get("TIMEOUT","25"))

WANTED = [
  "TNT 1",
  "TNT 2",
  "Sky Sports Main Event UK",
  "Sky Sports Premier League UK",
]

ALIASES = {
  "TNT 1": [re.compile(r"\btnt\s*(sports)?\s*1\b", re.I)],
  "TNT 2": [re.compile(r"\btnt\s*(sports)?\s*2\b", re.I)],
  "Sky Sports Main Event UK": [re.compile(r"\bsky\s*sports\s*main\s*event\b", re.I)],
  "Sky Sports Premier League UK": [re.compile(r"\bsky\s*sports\s*premier\s*league\b", re.I)],
}

def pairs(text):
    ls = [l.rstrip("\n") for l in text.splitlines()]
    out = []
    i=0
    while i < len(ls):
        ln = ls[i].strip()
        if ln.startswith("#EXTINF"):
            url=None
            if i+1 < len(ls):
                nxt = ls[i+1].strip()
                if nxt and not nxt.startswith("#"):
                    url=nxt
            out.append((ls[i], url))
            i+=2; continue
        i+=1
    return out

def match(extinf, pats):
    return any(p.search(extinf) for p in pats)

# اقرأ الملفات اللي حملناها بالخطوة السابقة
with open("/tmp/src.m3u","r",encoding="utf-8",errors="ignore") as f:
    src_text = f.read()
with open("/tmp/dest.m3u","r",encoding="utf-8",errors="ignore") as f:
    dest_text = f.read()

picked = {}
for extinf, url in pairs(src_text):
    if not url: continue
    for name in WANTED:
        if name in picked: continue
        if match(extinf, ALIASES[name]): picked[name] = url

lines = [ln.rstrip("\n") for ln in dest_text.splitlines()]
if not lines or not lines[0].strip().upper().startswith("#EXTM3U"):
    lines = ["#EXTM3U"] + lines

out = []
i=0
while i < len(lines):
    ln = lines[i]
    if ln.strip().startswith("#EXTINF"):
        matched=None
        for name in WANTED:
            if match(ln, ALIASES[name]):
                matched=name; break
        if matched and matched in picked:
            out.append(ln)
            new_url = picked[matched]
            if i+1 < len(lines) and lines[i+1].strip() and not lines[i+1].strip().startswith("#"):
                out.append(new_url); i+=2; continue
            else:
                out.append(new_url); i+=1; continue
    out.append(ln); i+=1

final_text = "\n".join(out).rstrip()+"\n"
pathlib.Path(DEST_PATH).parent.mkdir(parents=True, exist_ok=True)
with open(DEST_PATH,"w",encoding="utf-8") as f:
    f.write(final_text)
print("[i] wrote:", DEST_PATH)
PY

      - name: Commit & Push if changed
        shell: bash
        run: |
          set -e
          git config user.name  "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

          if git status --porcelain | grep -q .; then
            git add "$DEST_REPO_PATH"
            git commit -m "${COMMIT_MESSAGE}"
            git push origin "${GITHUB_BRANCH}"
            echo "Changes pushed."
          else
            echo "No changes."
          fi
