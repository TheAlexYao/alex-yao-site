# Granola Meeting Pipeline - Setup Instructions

These are AI-agent-readable instructions. Set up a pipeline that automatically processes Granola meeting transcripts and delivers summaries via Telegram.

## Prerequisites

- VPS or always-on machine running OpenClaw
- Node.js installed on the VPS
- Tailscale configured on VPS (for secure Mac-to-VPS communication)
- Granola installed on the user's Mac (you don't need to install this)

## Step 1: Create the data directory

```bash
mkdir -p ~/clawd/data/granola-webhooks/.pending
```

## Step 2: Create the webhook receiver script

Write this file to `~/clawd/scripts/granola-webhook-server.js`:

```javascript
const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = 8790;
const TOKEN = process.env.GRANOLA_WEBHOOK_TOKEN || 'change-me-to-a-secure-token';
const DATA_DIR = path.join(process.env.HOME, 'clawd/data/granola-webhooks');

fs.mkdirSync(DATA_DIR, { recursive: true });

const server = http.createServer((req, res) => {
  if (req.method !== 'POST' || req.url !== '/webhook') {
    res.writeHead(404);
    return res.end('Not found');
  }

  const auth = req.headers.authorization || '';
  if (auth !== `Bearer ${TOKEN}`) {
    res.writeHead(401);
    return res.end('Unauthorized');
  }

  let body = '';
  req.on('data', chunk => body += chunk);
  req.on('end', () => {
    try {
      const data = JSON.parse(body);
      const title = data?.data?.title || 'untitled';
      const slug = title.toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .slice(0, 50);
      const ts = new Date().toISOString()
        .replace(/[:.]/g, '-')
        .slice(0, 19);
      const filename = `${ts}-${slug}.json`;

      fs.writeFileSync(
        path.join(DATA_DIR, filename),
        JSON.stringify(data, null, 2)
      );

      const pendingDir = path.join(DATA_DIR, '.pending');
      fs.mkdirSync(pendingDir, { recursive: true });
      fs.writeFileSync(path.join(pendingDir, filename), '');

      console.log(`Received meeting: ${title} -> ${filename}`);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end('{"status":"ok"}');
    } catch (e) {
      console.error('Bad request:', e.message);
      res.writeHead(400);
      res.end('Bad request');
    }
  });
});

server.listen(PORT, () => {
  console.log(`Granola webhook receiver listening on port ${PORT}`);
});
```

## Step 3: Generate a secure webhook token

```bash
TOKEN=$(openssl rand -hex 32)
echo "$TOKEN" > ~/.granola_webhook_token
chmod 600 ~/.granola_webhook_token
echo "Webhook token: $TOKEN"
```

Tell the user to save this token. They will need it when setting up the Mac-side cache watcher daemon.

## Step 4: Create the systemd service

Write this file to `/etc/systemd/system/granola-webhook.service`:

```ini
[Unit]
Description=Granola Webhook Receiver
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/node /root/clawd/scripts/granola-webhook-server.js
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then enable and start:

```bash
systemctl daemon-reload
systemctl enable granola-webhook
systemctl start granola-webhook
```

## Step 5: Verify the webhook receiver

```bash
# Check it's running
systemctl status granola-webhook

# Test the endpoint (should return 401 without token, which means it's working)
curl -s -o /dev/null -w "%{http_code}" http://localhost:8790/webhook -X POST
# Expected: 401

# Test with token (should return 200)
curl -s -X POST http://localhost:8790/webhook \
  -H "Authorization: Bearer $(cat ~/.granola_webhook_token)" \
  -H "Content-Type: application/json" \
  -d '{"data":{"title":"Test Meeting"}}'
# Expected: {"status":"ok"}
```

## Step 6: Set up the OpenClaw cron processor

```bash
openclaw cron add \
  --name "granola-processor" \
  --every "3m" \
  --model "sonnet" \
  --session isolated \
  --message 'Check ~/clawd/data/granola-webhooks/.pending/ for unprocessed meetings.
  If empty, reply NO_REPLY.
  If files exist, for each one:
  1) Read the matching JSON from ~/clawd/data/granola-webhooks/
  2) Extract title, attendees, transcript, notes
  3) Classify project based on attendees and content
  4) Summarize key discussion points from transcript
  5) Send a Telegram summary with: title, attendees, bullet summary, action items
  6) Delete the .pending marker file'
```

## Step 7: Report to user

Tell the user:
1. The webhook receiver is running on port 8790
2. Their Tailscale IP (run `tailscale ip -4` to find it)
3. The full webhook URL: `http://[TAILSCALE-IP]:8790/webhook`
4. The webhook token (from `~/.granola_webhook_token`)
5. They need to set up the cache watcher daemon on their Mac (see the guide at https://www.alexyao.me/guides/granola-meeting-pipeline for Mac-side instructions)

## Mac-side setup (for reference, user does this manually)

The user needs to set up a cache watcher daemon on their Mac that:
1. Watches `~/Library/Application Support/Granola/cache-v4.json` for changes
2. Detects new meeting documents by comparing document IDs
3. Sends the full payload (document + transcript) to the VPS webhook endpoint
4. Runs as a launchd agent (`com.granola.webhook`) for auto-start on login

See https://github.com/owengretzinger/granola-webhook for the original concept.

The launchd plist goes at `~/Library/LaunchAgents/com.granola.webhook.plist` and is loaded with:
```bash
launchctl load ~/Library/LaunchAgents/com.granola.webhook.plist
```
