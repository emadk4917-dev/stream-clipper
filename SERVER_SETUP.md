# Server setup: about 45 minutes, once

What you end up with: a server running 24/7 that watches your campaign streamers, clips
the best moments while they're live, and sends each clip to your Telegram for a one-tap
approval. When a clip's posting slot comes up, it sends the file and captions to your phone.

## Step 1: Buy the server (€37/month)
1. Go to **https://contabo.com/en/vps/** and pick **Cloud VPS 16** (16 cores, 64 GB RAM),
   the server the launch plan picked. A smaller VPS works for a few streamers, but this one handles 20+.
2. Choose: **Region:** European Union or United Kingdom · **Image:** Ubuntu 24.04 · **Term:** monthly.
3. Set a strong root password. Contabo emails you the server's **IP address** (takes 15–60 minutes).

## Step 2: Create the Telegram bot (2 minutes)
1. In Telegram, search **@BotFather**, then send `/newbot`. Give it a name (e.g. *Clip HQ*) and a username ending in `bot`.
2. BotFather replies with a **token** like `123456:ABC-...`. Keep it private and don't paste it in any chat.
3. Make a **Telegram group** with you, Imad and your brother, and **add the bot** to it.
   Approvals and posting packs go to the group, so whoever is free can tap.

## Step 3: Get the other keys
- **Claude API key:** https://console.anthropic.com → *API keys* → *Create key*.
  Under *Billing*, add credit and **set a monthly spend limit** (start around $150).
- **YouTube Data API key** (for the trend radar, free): https://console.cloud.google.com → create a project →
  *APIs & Services → Library* → enable **YouTube Data API v3** → *Credentials → Create credentials → API key*.
- **GitHub token** (so the server can download this private code): GitHub → *Settings → Developer settings →
  Fine-grained tokens → Generate new token* → *Only select repositories: stream-clipper* → *Contents: Read-only*.

## Step 4: Install (copy-paste into Terminal on your Mac)
```bash
ssh root@YOUR_SERVER_IP
```
On the server (when git asks for a password, paste the GitHub token):
```bash
git clone https://github.com/emadk4917-dev/stream-clipper.git /opt/stream-clipper
```
```bash
bash /opt/stream-clipper/deploy/setup_server.sh
```
```bash
nano /opt/stream-clipper/.env
```
In nano, fill in `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN` and `YOUTUBE_API_KEY`, then press Ctrl+O, Enter, Ctrl+X to save and exit.

## Step 5: Connect Telegram, then start everything
```bash
systemctl enable --now clipper-bot
```
In your Telegram group, send `/start`. The bot replies **"Your chat id is -100…"**. Put that number in
`.env` as `TELEGRAM_CHAT_IDS=-100…`, then:
```bash
systemctl restart clipper-bot && systemctl enable --now clipper-realtime clipper-live clipper-trends
```

## Step 6: Add your campaigns (in Telegram)
```
/addcampaign Name | twitch_login | rate per 1k | campaign link | account set | rules
/addcampaign Kai GTA | kaicenat | 1.5 | https://contentrewards.com/... | Set 1 | must tag @kaicenat
```
The real-time clipper starts watching that streamer's chat within 5 minutes. Other commands: `/campaigns`, `/pause 2`,
`/resume 2`, `/queue`, `/stats`, `/link CLIPID URL`, `/report`.

## Updating to a new version later
```bash
cd /opt/stream-clipper && git pull && .venv/bin/pip install -r requirements.txt && systemctl restart clipper-bot clipper-realtime clipper-live clipper-trends
```

## If something looks wrong
```bash
journalctl -u clipper-realtime -n 100
```
Paste the output to Claude **after checking there are no keys in it**.
